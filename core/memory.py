"""Saved memory: facts the person explicitly asked to keep (diet, goals, preferences, instructions,
and temporary context with dates such as "traveling Thu-Sat").

Written only on an explicit request ("remember..."); never extracted automatically.
Every agent turn sees the facts that apply today.
"""
from datetime import date

from core import db

KINDS = ("goal", "diet", "preference", "instruction", "context")


def remember(user, text: str, kind: str = "preference", start: date | None = None, end: date | None = None) -> dict:
    if kind not in KINDS:
        kind = "preference"
    return db.add_memory(user.id, text.strip(), kind, start, end)


def forget(user, memory_ids: list[int]) -> list[dict]:
    """Remove these facts; returns what was removed (so the reply can say exactly what)."""
    return db.delete_memory(user.id, memory_ids) if memory_ids else []


def facts(user, day: date | None = None) -> list[dict]:
    """All facts, or the ones that apply on `day` (expired and future dated facts left out)."""
    return db.list_memory(user.id, day)


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
