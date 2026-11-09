"""The coach agent: understand the message, act with tools, reply within the reply rules.

    ingest -> greet (whole-message greeting, code only) -------------> respond -> END
           -> confirm_previous (a bare "yes" to an old question, code) -> respond -> END
           -> classify -> decline (off_topic: a fixed line, code) -------> respond -> END
                       -> agent <-> tools (ToolNode) -----------------> respond -> END

classify picks 1-2 categories (core/classifier.py); each category is a skill file (skills/<name>.md) that
gives the agent only its tools and its guidance for this turn. "other" = every tool and every skill's
guidance (and the message is kept for review in unclassified_messages).

One LangGraph thread per chat session (`session-{user_id}-{start}`, core/chat_sessions.py), so the
agent sees this conversation and nothing older; saved memory and today's data come in through the
system message every turn. The LLM decides what to do; numbers, rules and plan messages are code.
Who and when (user_id, now, session_id, use_fixtures) come from the run config, never from the state.
"""
import logging
import re
import time
from datetime import timedelta

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from connectors.health import Health
from core import chat_sessions, classifier, db, memory, models, reply_rules, score
from core import skills as skill_files
from core.config import CONSULT_TTL_MIN
from core.day import day_of
from core.labels import clock
from core.llm import load_prompt
from core.tools import CORE_TOOLS, turn
from modules.registry import AVAILABLE, enabled_modules, load

GREETING = re.compile(r"^\s*(hi+|hey+|hello+|hiya|yo|good\s+(morning|afternoon|evening))[\s!.]*$", re.I)
GREETING_TAIL = "Tell me what you ate, ask for a workout, or ask how your day is going."
EXPIRED_CHECKIN = "Still want to check in today?"
NO_REPLY = "Could you say that another way?"
CONFIRM_PREVIOUS = "Just to check: do you mean my question from {when}: “{question}”"
QUOTE_MAX_WORDS = 25
HISTORY_HUMAN_TURNS = 5      # the agent sees the conversation from the 5th-last message they sent (~10 messages)
# Tools that change something. When one of them ran next to a tool whose text is sent as is,
# the agent's own line (e.g. "Logged 2 eggs.", or the answer to the rest of "done, what should I eat?")
# is sent too, after the code-built text.
WRITE_TOOLS = {"log_food", "edit_food", "remove_food", "remember", "forget", "add_manual_workout", "mark_done"}
RECENT_FOR_CLASSIFIER = 6    # the classifier sees the last 6 messages (about 3 exchanges) before this one
# A check-in answer or a yes/no workout question waiting belongs to this category, whatever the classifier says.
PENDING_OWNER = "training"
log = logging.getLogger(__name__)


def make_model(override: str | None = None):
    """The agent's chat model (job `agent`; tests replace this function)."""
    return models.chat("agent", override)


def _log(kind: str, messages: list, output, t, response=None, started: float | None = None,
         override: str | None = None) -> None:
    """One runs row for an agent call (job `agent`, with its model, tokens and latency)."""
    input_tokens, output_tokens = models.usage(response)
    db.log_run(kind, models.model_name("agent", override), _trace(messages), output, t.user.id, job="agent",
               input_tokens=input_tokens, output_tokens=output_tokens,
               latency_ms=round((time.perf_counter() - started) * 1000) if started else None)


class AgentState(MessagesState):
    greeting: bool          # this turn's message is only a greeting
    confirm_previous: bool  # a bare "yes" over 12 h after the question it may answer: ask which one first
    expired_checkin: bool   # a check-in question from an earlier session was left unanswered today
    code_reply: bool        # this turn's reply was built by code (greeting, confirmation): no reply rules
    categories: list[str]   # this turn's skills (core/classifier.py), or ["other"]


def all_tools() -> list:
    """Every tool of every module (the ToolNode can run any; the model is only shown the enabled ones)."""
    return CORE_TOOLS + [t for name in AVAILABLE for t in load(name).tools()]


def _text(content) -> str:
    if isinstance(content, list):   # some models return content blocks
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
    return str(content or "").strip()


def _this_turn(messages: list) -> list:
    """Messages since their latest message."""
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return messages[last_human + 1:]


def recent(messages: list) -> list:
    """The part of the conversation the model sees; always starts at one of their messages,
    so a tool call is never separated from its result."""
    humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    start = humans[-HISTORY_HUMAN_TURNS] if len(humans) >= HISTORY_HUMAN_TURNS else 0
    return messages[start:]


def _when(sent, now) -> str:
    """"today at 9:05am", "yesterday at 11:08am", "Saturday at 11:08am"."""
    day = ("today" if sent.date() == now.date() else "yesterday" if (now.date() - sent.date()).days == 1
           else f"{sent:%A}")
    return f"{day} at {clock(sent)}"


def _quote(text: str) -> str:
    words = text.split()
    return text if len(words) <= QUOTE_MAX_WORDS else " ".join(words[:QUOTE_MAX_WORDS]) + "…"


def system_prompt(t, guidance: list[tuple[str, str]] = ()) -> str:
    """The shared prompt, this turn's skill guidance [(name, body)], then time, facts and module summaries."""
    lines = [load_prompt("agent")]
    if guidance:
        lines += ["", f"What to do for this message ({', '.join(name for name, _ in guidance)}):"]
        for name, body in guidance:
            lines += [f"[{name}]", body]
    lines += ["", f"Now: {t.now:%A %Y-%m-%d %H:%M}. Today (by their sleep): {t.day:%A %Y-%m-%d}."]
    previous = chat_sessions.previous_question(t.user, t.session_id, t.now) if t.session_id else None
    if previous:
        lines.append(f"Your last message before this conversation (sent {previous['sent']:%a} "
                     f"{clock(previous['sent'])}): {previous['text'][:400]!r}. If their message answers it, act on "
                     "that answer.")
    facts = memory.facts(t.user, t.day)
    lines.append("Saved facts, oldest first (id: fact):" if facts else "Saved facts: none.")
    lines += [f"- {f['id']}: {memory.describe(f)}" for f in facts]
    for module in enabled_modules(t.user):
        lines.append(f"{module.name.capitalize()}: {module.context(t.user, t.day)}")
    return "\n".join(lines)


def _trace(messages: list) -> list[dict]:
    return [{"role": m.type, "content": m.content, **({"tool_calls": m.tool_calls} if getattr(m, "tool_calls", None)
                                                      else {})} for m in messages]


# --- nodes ------------------------------------------------------------------

def ingest(state: AgentState, config) -> dict:
    t = turn(config)
    # A "still want HIIT?" question lasts 2 hours, across sessions; unanswered, it expires with no plan.
    db.expire_consults(t.user.id, t.now, timedelta(minutes=CONSULT_TTL_MIN))
    # Days whose cutoff has passed get their final score now, before this message can change them.
    score.finalize_quietly(t.user, t.now, t.use_fixtures)
    pending = db.pending_plan(t.user.id)
    expired = False
    if pending and pending["chat_session_id"] != t.session_id:
        # The question was asked in an earlier session: drop it, and ask once more if it is still today.
        db.abandon_pending(t.user.id)
        expired = pending["date"] == t.day.isoformat()
    message = _text(state["messages"][-1].content)
    first_message = sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1
    previous = chat_sessions.previous_question(t.user, t.session_id, t.now) if t.session_id and first_message else None
    return {"greeting": bool(GREETING.match(message)), "expired_checkin": expired, "code_reply": False,
            "confirm_previous": chat_sessions.needs_confirming(previous, message, t.now)}


def greet(state: AgentState, config) -> dict:
    """One line with today's readiness. Code only: no LLM, and never an error message."""
    t = turn(config)
    readiness = None
    try:
        health = Health(t.use_fixtures)
        health.oura.raw(t.user, t.now.date(), now=t.now)      # fills the cache day_of reads
        readiness = health.readiness(t.user, day_of(t.user, t.now))["score"]
    except Exception:
        pass
    first = f"Hi! Readiness {readiness} today." if readiness is not None else "Hi! No readiness yet today."
    return {"messages": [AIMessage(f"{first} {GREETING_TAIL}")], "code_reply": True}


def confirm_previous(state: AgentState, config) -> dict:
    """A bare "yes" more than 12 hours after the question it may answer: ask which one (code, no tools),
    so a stale "yes" never starts something else. Their next message is answered with the question in view."""
    t = turn(config)
    previous = chat_sessions.previous_question(t.user, t.session_id, t.now)
    text = CONFIRM_PREVIOUS.format(when=_when(previous["sent"], t.now), question=_quote(previous["question"]))
    return {"messages": [AIMessage(text)], "code_reply": True}


def _conversation_before(messages: list) -> list[tuple[str, str]]:
    """(role, text) of their messages and the coach's replies before their latest message."""
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    said = [("them" if isinstance(m, HumanMessage) else "coach", _text(m.content)) for m in messages[:last_human]
            if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and not m.tool_calls and _text(m.content))]
    return said[-RECENT_FOR_CLASSIFIER:]


def _waiting_for_training(t) -> list[str]:
    """What the training module is waiting for, in plain words (empty when nothing is)."""
    notes = []
    pending = db.pending_plan(t.user.id)
    if pending and pending["date"] == t.day.isoformat():
        notes.append("A check-in answer (energy 1-5 and soreness 0-5) is awaited.")
    consult = db.pending_confirm(t.user.id)
    if consult:
        question = consult["requested_json"]["question"].splitlines()[-1]
        notes.append(f"A yes/no answer is awaited to the coach's question: {question!r}")
    elif db.expired_consult(t.user.id, t.day):
        notes.append("A yes/no workout question from earlier today expired unanswered.")
    return notes


def classify(state: AgentState, config) -> dict:
    """Pick this turn's categories. A failure never blocks the message: it goes to "other"."""
    t = turn(config)
    message = _text(state["messages"][-1].content)
    training_notes = _waiting_for_training(t)
    notes = list(training_notes)
    previous = chat_sessions.previous_question(t.user, t.session_id, t.now) if t.session_id else None
    if previous:
        notes.append(f"The coach's last message before this conversation: {previous['question']!r}")
    try:
        route = classifier.classify(message, _conversation_before(state["messages"]), notes, t.user.id,
                                    skill_files.load())
    except Exception as e:
        log.warning("Classifying the message failed: %s", type(e).__name__)
        route = {"categories": [], "reason": f"classifier failed ({type(e).__name__})"}
    categories = route["categories"]
    if categories and training_notes and PENDING_OWNER not in categories and categories != ["off_topic"]:
        categories = categories + [PENDING_OWNER]
    if not categories:
        db.add_unclassified(t.user.id, t.now, message, route["reason"])
        categories = [skill_files.OTHER]
    return {"categories": categories}


def decline(state: AgentState, config) -> dict:
    """A category with a fixed reply (off_topic): sent by code, no agent call, no tools."""
    [skill] = skill_files.chosen(skill_files.load(), state["categories"])
    return {"messages": [AIMessage(skill.decline)], "code_reply": True}


def turn_setup(t, categories: list[str]) -> tuple[list, list[tuple[str, str]], str | None]:
    """(tools, guidance, model override) for this turn's categories. Only the user's enabled modules' tools
    are ever offered; "other" gets all of them and every skill's guidance."""
    skills = skill_files.load()
    enabled = CORE_TOOLS + [tool for m in enabled_modules(t.user) for tool in m.tools()]
    picked = skill_files.chosen(skills, categories)
    if not picked:
        return enabled, [(s.name, s.body) for s in skills.values() if not s.decline], None
    names = {name for s in picked for name in s.tools}
    return ([tool for tool in enabled if tool.name in names], [(s.name, s.body) for s in picked],
            next((s.model for s in picked if s.model), None))


def agent(state: AgentState, config) -> dict:
    t = turn(config)
    tools, guidance, override = turn_setup(t, state.get("categories") or [skill_files.OTHER])
    messages = [SystemMessage(system_prompt(t, guidance))] + recent(state["messages"])
    started = time.perf_counter()
    model = make_model(override)
    response = (model.bind_tools(tools) if tools else model).invoke(messages)
    _log("agent", messages, _trace([response]), t, response, started, override)
    return {"messages": [response]}


def _within_rules(draft: str, tools_called: set[str], t, offered: set[str] | None = None) -> str:
    """The draft if it follows the reply rules; else one rewrite by the model; else trimmed by code.
    `offered` = the tools the agent had this turn (an offer to add or log needs one of them)."""
    problems = reply_rules.violations(draft, tools_called, offered)
    if not problems:
        return draft
    request = (f"Rewrite this reply so it follows the rules. Problems: {'; '.join(problems)}. "
               f"Keep the facts, do not claim anything new. Reply with the text only.\n\n{draft}")
    messages = [SystemMessage(load_prompt("agent")), HumanMessage(request)]
    started = time.perf_counter()
    response = make_model().invoke(messages)
    rewrite = _text(response.content)
    _log("agent_rewrite", messages, {"content": rewrite}, t, response, started)
    if rewrite and not reply_rules.violations(rewrite, tools_called, offered):
        return rewrite
    return (reply_rules.enforce(rewrite or draft, tools_called, offered)
            or reply_rules.enforce(draft, tools_called, offered))


def respond(state: AgentState, config) -> dict:
    """The reply rules, in code. Text built by tools (plans, the check-in question) is sent as is."""
    t = turn(config)
    turn_messages = _this_turn(state["messages"])
    final = turn_messages[-1]
    tools_called = {m.name for m in turn_messages if isinstance(m, ToolMessage)}
    verbatim = [m.artifact["verbatim"] for m in turn_messages
                if isinstance(m, ToolMessage) and isinstance(m.artifact, dict) and m.artifact.get("verbatim")]
    draft = _text(final.content)
    offered = None if state.get("code_reply") else {
        tool.name for tool in turn_setup(t, state.get("categories") or [skill_files.OTHER])[0]}

    parts = []
    if state.get("code_reply"):
        parts.append(draft)                     # built by code (greeting, confirmation), sent as is
    elif verbatim:
        parts += verbatim                       # code-built text first, exactly as built
        built_by = {m.name for m in turn_messages if isinstance(m, ToolMessage) and isinstance(m.artifact, dict)
                    and m.artifact.get("verbatim")}
        # A tool that wrote nothing this time (mark_done with nothing to mark) is not a write.
        no_write = {m.name for m in turn_messages if isinstance(m, ToolMessage) and isinstance(m.artifact, dict)
                    and m.artifact.get("wrote") is False}
        writes = (tools_called & WRITE_TOOLS) - no_write
        their_message = next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
        asked = "?" in _text(their_message.content)             # they also asked something: keep the answer
        # The agent's line confirms a write or answers the rest; not needed when code text did both
        # ("done, what should I eat?" = mark_done's confirmation + suggest_meal's ideas).
        covered = writes <= built_by and bool(built_by - WRITE_TOOLS)
        repeats = any(draft and draft in v for v in verbatim)
        if draft and (writes or (no_write and asked)) and not covered and not repeats:
            parts.append(_within_rules(draft, tools_called, t, offered))
    elif draft:
        parts.append(_within_rules(draft, tools_called, t, offered))
    reply = "\n\n".join(p for p in parts if p) or NO_REPLY
    if state.get("expired_checkin"):
        reply += f"\n{EXPIRED_CHECKIN}"
    return {"messages": [AIMessage(content=reply, id=final.id)]}   # same id: replaces the draft in the thread


def after_ingest(state: AgentState) -> str:
    if state["greeting"]:
        return "greeting"
    return "stale yes" if state["confirm_previous"] else "anything else"


def after_classify(state: AgentState) -> str:
    picked = skill_files.chosen(skill_files.load(), state["categories"])
    return "fixed reply" if len(picked) == 1 and picked[0].decline else "agent"


def build_agent(checkpointer=None):
    skill_files.validate(skill_files.load(), {tool.name for tool in all_tools()})
    graph = StateGraph(AgentState)
    graph.add_node("ingest", ingest)
    graph.add_node("greet", greet)
    graph.add_node("confirm_previous", confirm_previous)
    graph.add_node("classify", classify)
    graph.add_node("decline", decline)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(all_tools()))
    graph.add_node("respond", respond)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", after_ingest, {"greeting": "greet", "stale yes": "confirm_previous",
                                                         "anything else": "classify"})
    graph.add_conditional_edges("classify", after_classify, {"fixed reply": "decline", "agent": "agent"})
    graph.add_edge("greet", "respond")
    graph.add_edge("confirm_previous", "respond")
    graph.add_edge("decline", "respond")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "respond"})
    graph.add_edge("tools", "agent")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
