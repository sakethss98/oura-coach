"""Rules for every free-form reply (plan messages are built by code and exempt). Pure functions.

- At most 3 lines and about 60 words.
- Plain words: no internal terms (band labels, intensity numbers, snake_case, "window", "null"),
  and no tacked-on "anything else?" (answer what was asked, then stop).
- Never offer what no tool can do ("want me to look it up?", "I can search online"): answer instead.
- An offer to add, log, save or mark something ("Want me to add it to today's food?") is allowed only when a
  tool that can do it is offered this turn.
- Never claim an action no tool performed this turn. Only first-person claims count
  ("I logged", "I've updated", "I'll resend"), never the bare word ("logged 2 eggs" is fine
  after log_food ran, and "you logged" is not a claim at all).
"""
import re

MAX_LINES = 3
MAX_WORDS = 60

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
    (re.compile(f"(?:{_OFFER_ME}|{_OFFER_I})(save|remember|note)\\b", re.I), {"remember"}),
    (re.compile(f"(?:{_OFFER_ME}|{_OFFER_I})mark\\b", re.I), {"mark_done"}),
]

_I = r"\bI(?:['’]ve|\s+have)?\s+(?:just\s+|already\s+|now\s+)?"
_I_WILL = r"\bI(?:['’]ll|\s+will)\s+"
# (pattern, tools that make the claim true). An empty set means the claim is never allowed.
CLAIMS = [
    (re.compile(_I + r"(logged|saved|added|recorded|noted|stored)\b", re.I),
     {"log_food", "remember", "add_manual_workout"}),
    (re.compile(_I + r"(removed|deleted|cleared|forgotten|forgot)\b", re.I), {"remove_food", "forget"}),
    (re.compile(_I + r"(updated|changed|edited|corrected|fixed)\b", re.I), {"edit_food"}),
    (re.compile(_I + r"(synced|refreshed|pulled|fetched)\b", re.I), {"refresh_health"}),
    (re.compile(_I + r"marked\b", re.I), {"mark_done"}),
    (re.compile(_I_WILL + r"(remember|save|note)\b", re.I), {"remember"}),
    (re.compile(_I_WILL + r"log\b", re.I), {"log_food", "add_manual_workout"}),
    (re.compile(_I_WILL + r"(resend|send|remind|check back|ping|follow up|text you|message you)\b", re.I), set()),
]


def _words(text: str) -> int:
    return len(text.split())


def _sentence_problems(sentence: str, tools_called: set[str], tools_offered: set[str] | None = None) -> list[str]:
    problems = [f"uses {what}" for pattern, what in BANNED if pattern.search(sentence)]
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


def violations(text: str, tools_called: set[str], tools_offered: set[str] | None = None) -> list[str]:
    """Every broken rule, in plain words (fed back to the model when it rewrites). `tools_offered` = the tools
    the agent had this turn (None = don't check offers to add/log/save/mark)."""
    problems = _sentence_problems(text, tools_called, tools_offered)
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LINES:
        problems.append(f"has {len(lines)} lines (max {MAX_LINES})")
    if _words(text) > MAX_WORDS:
        problems.append(f"has {_words(text)} words (max {MAX_WORDS})")
    return problems


def enforce(text: str, tools_called: set[str], tools_offered: set[str] | None = None) -> str:
    """Last resort: drop sentences that break a rule, keep 3 lines, then keep whole sentences in order while
    they fit in 60 words (a sentence that doesn't fit is left out, so a short closing question can still
    make it). Only a first sentence longer than 60 words on its own is cut, with "…"."""
    lines = []
    for line in text.splitlines():
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", line.strip())
                     if s and not _sentence_problems(s, tools_called, tools_offered)]
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
