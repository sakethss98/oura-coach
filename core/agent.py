"""The coach agent: understand the message, act with tools, reply within the reply rules.

    ingest -> greet (whole-message greeting, code only) -------------> respond -> END
           -> agent <-> tools (ToolNode, the user's enabled modules) -> respond -> END

One LangGraph thread per chat session (`session-{user_id}-{start}`, core/chat_sessions.py), so the
agent sees this conversation and nothing older; saved memory and today's data come in through the
system message every turn. The LLM decides what to do; numbers, rules and plan messages are code.
Who and when (user_id, now, session_id, use_fixtures) come from the run config, never from the state.
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from connectors.health import Health
from core import db, memory, reply_rules
from core.config import OPENAI_MODEL
from core.day import day_of
from core.llm import load_prompt
from core.tools import CORE_TOOLS, turn
from modules.registry import AVAILABLE, enabled_modules, load

GREETING = re.compile(r"^\s*(hi+|hey+|hello+|hiya|yo|good\s+(morning|afternoon|evening))[\s!.]*$", re.I)
GREETING_TAIL = "Tell me what you ate, ask for a workout, or ask how your day is going."
EXPIRED_CHECKIN = "Still want to check in today?"
NO_REPLY = "Could you say that another way?"
HISTORY_HUMAN_TURNS = 5      # the agent sees the conversation from the 5th-last message they sent (~10 messages)
# Tools that change something. When one of them ran next to a tool whose text is sent as is,
# the agent's own line (e.g. "Logged 2 eggs.") is sent too.
WRITE_TOOLS = {"log_food", "remove_food", "remember", "forget", "add_manual_workout", "mark_done"}


def make_model():
    """The chat model (tests replace this function)."""
    return ChatOpenAI(model=OPENAI_MODEL)


class AgentState(MessagesState):
    greeting: bool          # this turn's message is only a greeting
    expired_checkin: bool   # a check-in question from an earlier session was left unanswered today


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


def system_prompt(t) -> str:
    lines = [load_prompt("agent"), "",
             f"Now: {t.now:%A %Y-%m-%d %H:%M}. Today (by their sleep): {t.day:%A %Y-%m-%d}."]
    facts = memory.facts(t.user, t.day)
    lines.append("Saved facts (id: fact):" if facts else "Saved facts: none.")
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
    pending = db.pending_plan(t.user.id)
    expired = False
    if pending and pending["chat_session_id"] != t.session_id:
        # The question was asked in an earlier session: drop it, and ask once more if it is still today.
        db.abandon_pending(t.user.id)
        expired = pending["date"] == t.day.isoformat()
    return {"greeting": bool(GREETING.match(_text(state["messages"][-1].content))), "expired_checkin": expired}


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
    return {"messages": [AIMessage(f"{first} {GREETING_TAIL}")]}


def agent(state: AgentState, config) -> dict:
    t = turn(config)
    tools = CORE_TOOLS + [tool for m in enabled_modules(t.user) for tool in m.tools()]
    messages = [SystemMessage(system_prompt(t))] + recent(state["messages"])
    response = make_model().bind_tools(tools).invoke(messages)
    db.log_run("agent", OPENAI_MODEL, _trace(messages), _trace([response]), t.user.id)
    return {"messages": [response]}


def _within_rules(draft: str, tools_called: set[str], t) -> str:
    """The draft if it follows the reply rules; else one rewrite by the model; else trimmed by code."""
    problems = reply_rules.violations(draft, tools_called)
    if not problems:
        return draft
    request = (f"Rewrite this reply so it follows the rules. Problems: {'; '.join(problems)}. "
               f"Keep the facts, do not claim anything new. Reply with the text only.\n\n{draft}")
    messages = [SystemMessage(load_prompt("agent")), HumanMessage(request)]
    rewrite = _text(make_model().invoke(messages).content)
    db.log_run("agent_rewrite", OPENAI_MODEL, _trace(messages), {"content": rewrite}, t.user.id)
    if rewrite and not reply_rules.violations(rewrite, tools_called):
        return rewrite
    return reply_rules.enforce(rewrite or draft, tools_called) or reply_rules.enforce(draft, tools_called)


def respond(state: AgentState, config) -> dict:
    """The reply rules, in code. Text built by tools (plans, the check-in question) is sent as is."""
    t = turn(config)
    turn_messages = _this_turn(state["messages"])
    final = turn_messages[-1]
    tools_called = {m.name for m in turn_messages if isinstance(m, ToolMessage)}
    verbatim = [m.artifact["verbatim"] for m in turn_messages
                if isinstance(m, ToolMessage) and isinstance(m.artifact, dict) and m.artifact.get("verbatim")]
    draft = _text(final.content)

    parts = []
    if verbatim:
        if draft and tools_called & WRITE_TOOLS:
            parts.append(_within_rules(draft, tools_called, t))
        parts += verbatim
    elif draft:
        parts.append(_within_rules(draft, tools_called, t))
    reply = "\n\n".join(p for p in parts if p) or NO_REPLY
    if state.get("expired_checkin"):
        reply += f"\n{EXPIRED_CHECKIN}"
    return {"messages": [AIMessage(content=reply, id=final.id)]}   # same id: replaces the draft in the thread


def after_ingest(state: AgentState) -> str:
    return "greeting" if state["greeting"] else "anything else"


def build_agent(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("ingest", ingest)
    graph.add_node("greet", greet)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(all_tools()))
    graph.add_node("respond", respond)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", after_ingest, {"greeting": "greet", "anything else": "agent"})
    graph.add_edge("greet", "respond")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "respond"})
    graph.add_edge("tools", "agent")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
