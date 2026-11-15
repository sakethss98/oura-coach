"""The rubric checks a model answers (`by: judge`), job `judge` in config/models.yaml (a stronger model on purpose).

One call per graded reply answers every judge check, each yes / no (or n/a when the check's own condition doesn't
apply to this message) with a one-line reason; no scores. It sees what the coach saw: their message and the
conversation before it, this turn's tool results, the rubric and the reply. The citation check (id `citations`) is
its own call with the passages the coach was given, one per reply that has a [n] marker.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from core.llm import structured_call
from evals.checks import Turn

TOOL_CHARS = 2500        # each tool result, cut to this for the judge (the code-built text is given in full)


class Answer(BaseModel):
    n: int = Field(description="the check's number")
    answer: Literal["yes", "no", "n/a"] = Field(description="n/a only when the check's own condition doesn't "
                                                            "apply to this message")
    reason: str = Field(description="one short line")


class Grades(BaseModel):
    answers: list[Answer]


JUDGE = """You grade one reply from a running and nutrition coaching chatbot against yes/no checks.
For each check, answer yes or no about the reply, with a one-line reason. Answer n/a only when the check says when it applies and that isn't this message (e.g. "If they ask about muscle…" when they didn't).
The tool results are what the coach knew this turn. Text under "Built by code" was written by code from the tool results and sent as part of the reply: it counts as part of the reply. Judge the reply as the person would read it; don't grade the tools."""


def _tools(t: Turn) -> str:
    parts = []
    for r in t.tool_results:
        content = r.get("content")
        content = content if isinstance(content, str) else json.dumps(content, default=str)
        parts.append(f"- {r['name']}: {content[:TOOL_CHARS]}")
    return "\n".join(parts) or "(no tool ran)"


def prompt(t: Turn, checks: list[dict]) -> str:
    earlier = "\n".join(f"{who}: {text}" for who, text in t.earlier) or "(nothing before)"
    built = "\n---\n".join(t.built_texts()) or "(none)"
    numbered = "\n".join(f"{i}. {c['check']}" for i, c in enumerate(checks, 1))
    return (f"Conversation before:\n{earlier}\n\nTheir message: {t.message}\n\nTool results this turn:\n{_tools(t)}"
            f"\n\nBuilt by code:\n{built}\n\nThe reply:\n{t.reply}\n\nChecks:\n{numbered}")


def grade(t: Turn, checks: list[dict], user_id: int | None = None) -> list[tuple[str, str]]:
    """(answer, reason) for each check, in order. A check the judge skipped comes back "no", so it shows."""
    if not checks:
        return []
    found = {a.n: a for a in structured_call("judge", Grades, JUDGE, prompt(t, checks), user_id, job="judge").answers}
    return [(found[i].answer, found[i].reason) if i in found else ("no", "the judge gave no answer")
            for i in range(1, len(checks) + 1)]


# --- citations ----------------------------------------------------------------------------------------------

class CitationVerdict(BaseModel):
    supported: bool = Field(description="true if every claim marked [n] in the reply is supported by passage n")
    unsupported: list[str] = Field(description="the [n]-marked claims that passage n does not support (empty if none)")
    reason: str = Field(description="one short sentence")


CITATION_JUDGE = ("You check citations in one reply from a coaching chatbot. Claims in the reply are marked with [n]; "
                  "passage n is given. For each marked claim, decide whether passage n supports it (it says so, or it "
                  "clearly follows). Advice about the person's own day (readiness, their week) needs no support. "
                  "A claim attributed to the source that the passage doesn't contain is unsupported.")


def judge_citations(reply: str, sources: list[dict], user_id: int | None = None) -> CitationVerdict:
    """Is every [n]-cited claim supported by passage n? (every passage of each source is shown)."""
    passages = "\n\n".join(f"[{s['n']}] {s['title']}: {s['passage']}" for s in sources)
    return structured_call("judge", CitationVerdict, CITATION_JUDGE, f"Passages:\n{passages}\n\nThe reply: {reply}",
                           user_id, job="judge")


def citations(t: Turn, user_id: int | None = None) -> tuple[str, str]:
    body = "\n".join(line for line in t.reply.splitlines() if not line.startswith(("Source", "Amounts:")))
    if not re.search(r"\[\d+\]", body):
        return "n/a", "no [n] marker in the reply"
    verdict = judge_citations(body, t.sources(), user_id)
    if verdict.supported:
        return "yes", verdict.reason
    return "no", ("unsupported: " + " | ".join(verdict.unsupported))[:300] or verdict.reason
