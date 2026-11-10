"""Rules for every free-form reply (plan messages are built by code and exempt). Pure functions.

- At most 3 lines and about 60 words.
- Plain words: no internal terms (band labels, intensity numbers, snake_case, "window", "null"),
  and no tacked-on "anything else?" (answer what was asked, then stop).
- Never offer what no tool can do ("want me to look it up?", "I can search online"): answer instead.
- An offer to add, log, save or mark something ("Want me to add it to today's food?") is allowed only when a
  tool that can do it is offered this turn.
- When a knowledge search ran (search_gssi), the reply cites a result with its number ([1]) and never copies a
  passage: 12 or more words in a row the same as a returned passage is a copy (core/citations.py adds the
  source lines).
- Never claim an action no tool performed this turn. Only first-person claims count
  ("I logged", "I've updated", "I'll resend"), never the bare word ("logged 2 eggs" is fine
  after log_food ran, and "you logged" is not a claim at all).
"""
import json
import re
from dataclasses import dataclass

from core import citations

MAX_LINES = 3
MAX_WORDS = 60
COPY_WORDS = 12

# Blame words: never about the person ("bad" is left out: "a bad night" is about the night, not them).
GUILT = re.compile(r"\b(missed|failed|should\s+have|lazy|behind)\b", re.I)

BANNED = [
    (re.compile(r"\bbands?\b", re.I), "the word 'band'"),
    (re.compile(r"\b(PUSH|MAINTAIN|RECOVER)\b"), "a band label"),
    (re.compile(r"\bintensity\s*(of\s*)?\d", re.I), "an intensity number"),
    (re.compile(r"\bwindow\b", re.I), "the word 'window'"),
    (re.compile(r"\bnull\b", re.I), "the word 'null'"),
    (re.compile(r"\b[a-z]+_[a-z0-9_]+\b"), "a snake_case term"),
    (re.compile(r"\banything else\b", re.I), "an extra offer ('anything else')"),
    (re.compile(r"\boverrides?\b", re.I), "the word 'override'"),
    (GUILT, "a guilt word (missed, failed, should have, behind)"),
]

# Offers of an action the coach has no tool for (searching, browsing, looking things up). Never allowed:
# with a tool it should just use it, without one it should answer from what it knows.
OFFERS = [
    re.compile(r"\b(want|would you like|shall|should|do you want)\s+me\s+to\s+(look|search|check|browse|google|find)\b",
               re.I),
    re.compile(r"\bI\s+(can|could)\s+(search|browse|google|look\s+(it|that|this|them)?\s*up|check\s+online|"
               r"find\s+out\s+online)\b", re.I),
    re.compile(r"\bI['’]ll\s+(check|look|search)\s+(online|the\s+web|it\s+up|that\s+up)\b", re.I),
    re.compile(r"\blet\s+me\s+(search|google|browse|look\s+(it|that|this)\s+up|check\s+online)\b", re.I),
]

# Offers to write something: (pattern, tools that could do it). Allowed only if one is offered this turn.
_OFFER_ME = r"\b(?:want|would\s+you\s+like|shall|should|do\s+you\s+want)\s+me\s+to\s+"
_OFFER_I = r"\bI(?:\s+can|['’]ll|\s+will|\s+could)\s+"
WRITE_OFFERS = [
    (re.compile(f"(?:{_OFFER_ME}|{_OFFER_I})(add|log|record|track)\\b", re.I), {"log_food", "add_manual_workout"}),
    (re.compile(f"(?:{_OFFER_ME}|{_OFFER_I})(save|remember|note)\\b", re.I), {"remember", "save_body_weight"}),
    (re.compile(f"(?:{_OFFER_ME}|{_OFFER_I})mark\\b", re.I), {"mark_done"}),
]

_I = r"\bI(?:['’]ve|\s+have)?\s+(?:just\s+|already\s+|now\s+)?"
_I_WILL = r"\bI(?:['’]ll|\s+will)\s+"
# (pattern, tools that make the claim true). An empty set means the claim is never allowed.
CLAIMS = [
    (re.compile(_I + r"(logged|saved|added|recorded|noted|stored)\b", re.I),
     {"log_food", "remember", "add_manual_workout", "save_body_weight"}),
    (re.compile(_I + r"(removed|deleted|cleared|forgotten|forgot)\b", re.I), {"remove_food", "forget"}),
    (re.compile(_I + r"(updated|changed|edited|corrected|fixed)\b", re.I), {"edit_food"}),
    (re.compile(_I + r"(synced|refreshed|pulled|fetched)\b", re.I), {"refresh_health"}),
    (re.compile(_I + r"marked\b", re.I), {"mark_done"}),
    (re.compile(_I_WILL + r"(remember|save|note)\b", re.I), {"remember", "save_body_weight"}),
    (re.compile(_I_WILL + r"log\b", re.I), {"log_food", "add_manual_workout"}),
    (re.compile(_I_WILL + r"(resend|send|remind|check back|ping|follow up|text you|message you|get back|recalc\w*|"
                          r"calculate|work out|redo)\b", re.I), set()),
]


def _words(text: str) -> int:
    return len(text.split())


# An amount of food or drink: a number (or range) with its unit ("60–250 g", "100 mg", "500 ml"), per kg or per
# hour too ("1.0–1.2 g/kg/h", "30–60 g/h"). Not kg alone: that is their body weight ("Saved 62 kg.").
AMOUNT = re.compile(r"\d[\d.,]*\s*(?:[–-]\s*\d[\d.,]*\s*)?(?:g|mg|ml|mL|L|kcal)\b(?:\s*/\s*(?:kg|h|hr|hour)\b)*")


def without_amounts(text: str) -> str:
    """The text without its sentences that state an amount: used for the coach's own line next to a code-built
    plan, which already carries every amount (so a recap can never differ from it)."""
    lines = []
    for line in text.splitlines():
        kept = [s for s in re.split(r"(?<=[.!?])\s+", line.strip()) if s and not AMOUNT.search(s)]
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines)


# A sentence that restates a part of a code-built plan ("Day-before: carb-rich meals.", "During: nothing.").
RECAP = re.compile(r"^(the\s+)?(day[- ]before|before|during|drink\w*|after|caffeine|fluids?|mon|tue|wed|thu|fri|sat|"
                   r"sun)\w*\b[^.!?]{0,40}:", re.I)


def _amount_key(amount: str) -> str:
    return re.sub(r"\s+", "", amount).replace("-", "–")


def amounts_from(text: str, tool_texts: str) -> str:
    """The text without its sentences that state an amount no tool returned this turn (the model never does
    fueling math: "1.2 g/kg is 74 g an hour" from it is dropped; "20–25 g protein" from the plan stays)."""
    known = {_amount_key(m.group(0)) for m in AMOUNT.finditer(tool_texts)}
    lines = []
    for line in text.splitlines():
        kept = [s for s in re.split(r"(?<=[.!?])\s+", line.strip())
                if s and all(_amount_key(m.group(0)) in known for m in AMOUNT.finditer(s))]
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines)


OWN_LINE_SENTENCES = 2
# A sentence about the message itself, not the person ("Caffeine guidance sent (see plan).").
# Instruction-like text in a tool result: these fields, or a sentence that starts like an order. Tool results
# carry facts only (tests/conftest.py fails a scripted test on any of this); the echo guard compares the
# reply against it only, so facts the coach restates ("Logged 2 eggs, toast and chai at 8:10am") always pass.
INSTRUCTION_FIELDS = {"note", "guidance", "hint", "instructions", "how_to_reply"}
IMPERATIVE = re.compile(r"^(don['’]t|do not|say|never|answer|ask|call|use|stop|reply|list|tell|include|cite|add|"
                        r"offer|start)\b", re.I)


def instructions(tool_content) -> list[str]:
    """The instruction-like strings in one tool result (JSON or plain text)."""
    try:
        data = json.loads(tool_content) if isinstance(tool_content, str) else tool_content
    except ValueError:
        data = tool_content
    found = []

    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and value.strip():
            if key in INSTRUCTION_FIELDS:
                found.append(value)
            else:
                found.extend(s for s in re.split(r"(?<=[.!?])\s+", value) if IMPERATIVE.match(s.strip()))
    walk(data)
    return found


def without_echo(text: str, instruction_texts: list[str]) -> str:
    """The text without its sentences that share 8 words in a row with an instruction left in a tool result."""
    if not instruction_texts:
        return text
    lines = []
    for line in text.splitlines():
        kept = [s for s in re.split(r"(?<=[.!?])\s+", line.strip())
                if s and not copied(s, instruction_texts, ECHO_WORDS)]
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines)


ABOUT_THE_PLAN = re.compile(r"\b(sent|see (the )?(plan|above)|(plan|amounts|text) above|(read|computed) the plan|plan "
                            r"computed|(tell|let) me (if|know|your)|want (any|me|more)|sound good|same plan|re-?run|"
                            r"update the plan|what do you weigh)\b", re.I)
ECHO_WORDS = 8          # a sentence sharing 8 words in a row with a tool result is an echo of it, not a reply


def own_line(text: str) -> str:
    """The coach's own line next to a code-built plan: at most two sentences, in order, with no amount, no recap
    of the plan and no question ("about 30-60 min before (approx.). Low doses work [1]." keeps both)."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", without_amounts(text).strip())
                 if s and not RECAP.match(s) and not s.rstrip().endswith("?") and not ABOUT_THE_PLAN.search(s)]
    return " ".join(sentences[:OWN_LINE_SENTENCES])


def _word_list(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def copied(text: str, passages: list[str], n: int = COPY_WORDS) -> str | None:
    """The first run of n words in a row that the text shares with a passage, or None."""
    words = _word_list(text)
    runs = {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}
    for passage in passages:
        found = _word_list(passage)
        for i in range(len(found) - n + 1):
            if tuple(found[i:i + n]) in runs:
                return " ".join(found[i:i + n])
    return None


def cites(text: str, sources: list[dict]) -> bool:
    """The text cites a result: its number in brackets ([1]), or its url (code turns that into the number)."""
    return citations.has_marker(text, sources) or any(s["url"] in text for s in sources)


@dataclass(frozen=True)
class TurnFacts:
    """What this turn's tools settled, which the reply must not undo."""
    after_log: bool = False          # something was logged and they asked nothing: confirm and stop
    carb_load: bool | None = None    # fueling_plan's day before: False = no extra carbs (None = not in the plan)


# After a log the reply confirms and stops: a question or an offer ("want me to correct it?") makes their next
# message read as an answer to it (scenarios 17-18: breakfast became an edit of the 8am coffee).
LOG_OFFERS = re.compile(r"\?\s*(\[\d{1,2}\])?\s*$|\b(want me to|would you like|if you('d)? (want|like)|let me know|"
                        r"tell me|should I|shall I|happy to|I can (also|help))\b", re.I)
# More or loaded carbs, said next to a plan that says no extra carbs are needed.
MORE_CARBS = re.compile(r"\b(load(ing)?|boost(ing)?|extra|increase|more|higher|top(ping)? up|up your)\b[^.!?]{0,30}"
                        r"\bcarb(s|ohydrates?)?\b|\bcarb(ohydrate)?[- ]?(load|loading)\b", re.I)


def _sentence_problems(sentence: str, tools_called: set[str], tools_offered: set[str] | None = None,
                       passages: list[str] = (), facts: TurnFacts | None = None) -> list[str]:
    problems = [f"uses {what}" for pattern, what in BANNED if pattern.search(sentence)]
    if facts and facts.after_log and LOG_OFFERS.search(sentence):
        problems.append("asks or offers something after logging: confirm what was logged and stop")
    if facts and facts.carb_load is False and MORE_CARBS.search(sentence):
        problems.append("tells them to add or load carbs, but the plan says no extra carbs are needed")
    copy = copied(sentence, list(passages))
    if copy:
        problems.append(f'copies a passage word for word ("{copy}…"): say it in your own words')
    if tools_offered is not None:
        for pattern, could in WRITE_OFFERS:
            match = pattern.search(sentence)
            if match and not (could & tools_offered):
                problems.append(f'offers "{match.group(0).strip()}", but no tool for that is available now: '
                                "don't offer it")
    for pattern in OFFERS:
        match = pattern.search(sentence)
        if match:
            problems.append(f'offers "{match.group(0).strip()}", which no tool can do: answer from what you know')
    for pattern, needs in CLAIMS:
        match = pattern.search(sentence)
        if match and not (needs & tools_called):
            problems.append(f'claims "{match.group(0).strip()}" but no tool did that this turn')
    return problems


def _passages(sources: list[dict] | None) -> list[str]:
    return [s["passage"] for s in sources or [] if s.get("passage")]


def violations(text: str, tools_called: set[str], tools_offered: set[str] | None = None,
               sources: list[dict] | None = None, facts: TurnFacts | None = None) -> list[str]:
    """Every broken rule, in plain words (fed back to the model when it rewrites). `tools_offered` = the tools
    the agent had this turn (None = don't check offers to add/log/save/mark). `sources` = what search_gssi
    returned this turn ([{url, passage}]): the reply cites one url and copies no passage."""
    problems = list(dict.fromkeys(p for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()
                                  for p in _sentence_problems(s, tools_called, tools_offered, _passages(sources),
                                                              facts)))
    copy = copied(text, _passages(sources))                  # a copy can run across two sentences
    if copy and not any("copies a passage" in p for p in problems):
        problems.append(f'copies a passage word for word ("{copy}…"): say it in your own words')
    if sources and not cites(text, sources):
        numbers = sorted({s["n"] for s in sources})[:3]
        problems.append(f"cites no source: put a result's number in brackets after the claim it supports "
                        f"({', '.join(f'[{n}]' for n in numbers)})")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LINES:
        problems.append(f"has {len(lines)} lines (max {MAX_LINES})")
    if _words(text) > MAX_WORDS:
        problems.append(f"has {_words(text)} words (max {MAX_WORDS})")
    return problems


def enforce(text: str, tools_called: set[str], tools_offered: set[str] | None = None,
            sources: list[dict] | None = None, facts: TurnFacts | None = None) -> str:
    """Last resort: drop sentences that break a rule, keep 3 lines, then keep whole sentences in order while
    they fit in 60 words (a sentence that doesn't fit is left out, so a short closing question can still
    make it). Only a first sentence longer than 60 words on its own is cut, with "…"."""
    lines = []
    for line in text.splitlines():
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", line.strip())
                     if s and not _sentence_problems(s, tools_called, tools_offered, _passages(sources), facts)]
        if sentences:
            lines.append(sentences)
    out, count = [], 0
    for sentences in lines[:MAX_LINES]:
        kept = []
        for sentence in sentences:
            words = len(sentence.split())
            if count + words <= MAX_WORDS:
                kept.append(sentence)
                count += words
        if kept:
            out.append(" ".join(kept))
    if not out and lines:
        out = [" ".join(lines[0][0].split()[:MAX_WORDS]).rstrip(",;:") + "…"]
    return "\n".join(out)
