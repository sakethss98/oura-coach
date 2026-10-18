"""Rules for every free-form reply (plan messages are built by code and exempt). Pure functions.

- At most 3 lines and about 60 words.
- Plain words: no internal terms (band labels, intensity numbers, snake_case, "window", "null"),
  and no tacked-on "anything else?" (answer what was asked, then stop).
- Never claim an action no tool performed this turn. Only first-person claims count
  ("I logged", "I've updated", "I'll resend"), never the bare word ("logged 2 eggs" is fine
  after log_food ran, and "you logged" is not a claim at all).
"""
import re

MAX_LINES = 3
MAX_WORDS = 60

BANNED = [
    (re.compile(r"\bbands?\b", re.I), "the word 'band'"),
    (re.compile(r"\b(PUSH|MAINTAIN|RECOVER)\b"), "a band label"),
    (re.compile(r"\bintensity\s*(of\s*)?\d", re.I), "an intensity number"),
    (re.compile(r"\bwindow\b", re.I), "the word 'window'"),
    (re.compile(r"\bnull\b", re.I), "the word 'null'"),
    (re.compile(r"\b[a-z]+_[a-z0-9_]+\b"), "a snake_case term"),
    (re.compile(r"\banything else\b", re.I), "an extra offer ('anything else')"),
    (re.compile(r"\boverrides?\b", re.I), "the word 'override'"),
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


def _sentence_problems(sentence: str, tools_called: set[str]) -> list[str]:
    problems = [f"uses {what}" for pattern, what in BANNED if pattern.search(sentence)]
    for pattern, needs in CLAIMS:
        match = pattern.search(sentence)
        if match and not (needs & tools_called):
            problems.append(f'claims "{match.group(0).strip()}" but no tool did that this turn')
    return problems


def violations(text: str, tools_called: set[str]) -> list[str]:
    """Every broken rule, in plain words (fed back to the model when it rewrites)."""
    problems = _sentence_problems(text, tools_called)
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LINES:
        problems.append(f"has {len(lines)} lines (max {MAX_LINES})")
    if _words(text) > MAX_WORDS:
        problems.append(f"has {_words(text)} words (max {MAX_WORDS})")
    return problems


def enforce(text: str, tools_called: set[str]) -> str:
    """Last resort: drop sentences that break a rule, then cut to 3 lines and 60 words."""
    kept_lines = []
    for line in text.splitlines():
        sentences = re.split(r"(?<=[.!?])\s+", line.strip())
        kept = [s for s in sentences if s and not _sentence_problems(s, tools_called)]
        if kept:
            kept_lines.append(" ".join(kept))
    kept_lines = kept_lines[:MAX_LINES]
    out, count = [], 0
    for line in kept_lines:
        words = line.split()
        if count + len(words) > MAX_WORDS:
            words = words[:MAX_WORDS - count]
            if words:
                out.append(" ".join(words).rstrip(",;:") + "…")
            break
        out.append(line)
        count += len(words)
    return "\n".join(out)
