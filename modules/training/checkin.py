"""Their answer to the check-in question, read into numbers and a request.

A bare "4 2" or "skip" is read by code (no LLM call). Anything else ("5 0 give me run + strength",
"energy 3, legs heavy, only 30 min") is one structured LLM call that also reads the workout they asked
for before the question, if any. Code checks the ranges: energy 1-5, soreness 0-5 (0 = not sore).
"""
import re

from core.llm import load_prompt, structured_call
from modules.training.schemas import CheckinParse

BARE = re.compile(r"^\s*(\d)\s*[,/ ]\s*(\d)\s*[.!]?\s*$")
SKIP = re.compile(r"^\s*(skip|skip it|pass)\s*[.!]?\s*$", re.I)
RANGE_ERROR = "Energy must be 1-5 and soreness 0-5. Ask again."


class CheckinError(ValueError):
    """The answer has numbers out of range; the message says what to ask."""


def _result(energy, soreness, request=None, note=None, constraints=()) -> dict:
    if energy is not None and not 1 <= energy <= 5 or soreness is not None and not 0 <= soreness <= 5:
        raise CheckinError(RANGE_ERROR)
    return {"energy": energy, "soreness": soreness, "request": request or None, "note": note or None,
            "constraints": list(constraints)}


def parse(answer: str | None, request_text: str | None = None, user_id: int | None = None) -> dict:
    """{"energy", "soreness", "request": [{workout_type, duration_min}] | None, "note", "constraints"}.
    `request_text`: what they asked for when the check-in started ("want HIIT"), or None."""
    answer = (answer or "").strip()
    if not request_text:
        if not answer or SKIP.match(answer):
            return _result(None, None)
        bare = BARE.match(answer)
        if bare:
            return _result(int(bare.group(1)), int(bare.group(2)))
    message = f"What they asked for before the question: {request_text or 'nothing'}\nTheir answer: {answer or '(none)'}"
    parsed = structured_call("checkin", CheckinParse, load_prompt("checkin"), message, user_id)
    request = [b.model_dump() for b in parsed.request.blocks] if parsed.request else None
    return _result(parsed.energy, parsed.soreness, request, parsed.feeling, parsed.constraints)
