"""Saved memory: facts the person explicitly asked to keep (diet, goals, preferences, instructions,
and temporary context with dates such as "traveling Thu-Sat").

Written only on an explicit request ("remember..."); never extracted automatically.
Every agent turn sees the facts that apply today.
"""
import re
from datetime import date

from core import db

KINDS = ("goal", "diet", "preference", "instruction", "context", "body")
# "Keep it light" while a fact applies, enforced by the training rules (modules/training/policy.py).
# The model picks the word; the number is code's: light = no HIIT or long run, easy effort at most;
# very light = walk, mobility or rest only.
LIGHT_CAPS = {"light": 3, "very_light": 2}


def remember(user, text: str, kind: str = "preference", start: date | None = None, end: date | None = None,
             keep_light: str | None = None) -> dict:
    if kind not in KINDS:
        kind = "preference"
    return db.add_memory(user.id, text.strip(), kind, start, end, LIGHT_CAPS.get(keep_light))


# Body weight is a fact in a fixed form, written and read by code ("Body weight: 62 kg"), never estimated.
BODY_WEIGHT = "Body weight: {kg:g} kg"
BODY_WEIGHT_KG = re.compile(r"^Body weight: ([\d.]+) kg$")
WEIGHT_RANGE_KG = (30, 250)


def save_body_weight(user, kg: float) -> dict:
    """Save their stated weight as the only body-weight fact (an earlier one is replaced)."""
    old = [f["id"] for f in db.list_memory(user.id) if f["kind"] == "body"]
    if old:
        db.delete_memory(user.id, old)
    return db.add_memory(user.id, BODY_WEIGHT.format(kg=round(kg, 1)), "body")


def body_weight(user) -> float | None:
    """The saved body weight in kg, or None."""
    for f in reversed(db.list_memory(user.id)):
        found = BODY_WEIGHT_KG.match(f["text"]) if f["kind"] == "body" else None
        if found:
            return float(found.group(1))
    return None


def intensity_caps(user, day: date) -> list[dict]:
    """[{"text", "cap"}] of the facts that apply on `day` and ask to keep training light."""
    return [{"text": f["text"], "cap": f["intensity_cap"]} for f in facts(user, day) if f.get("intensity_cap")]


def forget(user, memory_ids: list[int]) -> list[dict]:
    """Remove these facts; returns what was removed (so the reply can say exactly what)."""
    return db.delete_memory(user.id, memory_ids) if memory_ids else []


def facts(user, day: date | None = None) -> list[dict]:
    """All facts, or the ones that apply on `day` (expired and future dated facts left out)."""
    return db.list_memory(user.id, day)


def current(user, day: date) -> list[dict]:
    """Facts that still matter on `day`: undated ones, and dated ones that haven't ended (upcoming included).
    A dated fact whose last day has passed is no longer loaded (it stays in the database)."""
    return [f for f in db.list_memory(user.id) if f["end_date"] is None or f["end_date"] >= day.isoformat()]


def describe(fact: dict) -> str:
    """One fact in plain words, with its dates if it has any."""
    if fact["start_date"] and fact["end_date"]:
        start, end = date.fromisoformat(fact["start_date"]), date.fromisoformat(fact["end_date"])
        span = f"{start:%a %b %d}" if start == end else f"{start:%a %b %d} - {end:%a %b %d}"
        return f"{fact['text']} ({span})"
    return fact["text"]


def for_prompt(user, day: date) -> list[str]:
    """The facts that apply on `day`, as plain lines for an LLM prompt."""
    return [describe(f) for f in facts(user, day)]
