"""Nutrition tools for the agent: log, list and remove food.

Editing an entry arrives in Phase 4 (edit_food). Until then a correction must never become a second
entry: log_food refuses a message that reads as a correction once any food was logged today.
remove_food only deletes entries whose ids the agent has seen in this conversation (list_food /
log_food results), so it never guesses.
"""
import re
from datetime import timedelta

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from core import db
from core.day import day_bounds
from core.labels import clock
from core.timeparse import parse_clock_time
from core.tools import Messages, as_json, ids_shown, turn
from modules.nutrition.food import log_food as save_food
from modules.nutrition.messages import confirm_entry

CORRECTION = re.compile(
    r"\bcorrection\b|\b(edit|fix|change|update|correct)\s+(that|it|this|the)\b"
    r"|\b(it'?s|it\s+is|it\s+was|that\s+was|that'?s)\b[^.?!]*\d+\s*(k?cals?|g)\b",
    re.I,
)
FOOD_ID_TOOLS = {"log_food", "list_food"}


def _last_human(messages: list) -> str:
    return next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")


def _removed_this_turn(messages: list) -> bool:
    """remove_food ran since their last message (the "remove and log it again" path)."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return False
        if isinstance(m, ToolMessage) and m.name == "remove_food":
            return True
    return False


def _food_logged_today(t) -> bool:
    return bool(db.food_between(t.user.id, *day_bounds(t.user, t.day)))


def _entry(e: dict) -> dict:
    return {"id": e["id"], "time": clock(e["timestamp"][11:16]), "text": e["raw_text"],
            "kcal": round(e["est_calories"] or 0), "protein_g": round(e["est_protein_g"] or 0)}


@tool
def log_food(text: str, time_text: str | None, state: Messages, config: RunnableConfig) -> str:
    """Log something they ate or drank (meal, snack, drink, coffee, alcohol, supplement) with a rough estimate.
    text: what they had, in their words, without the time. time_text: a clock time exactly as written, or null."""
    t = turn(config)
    if CORRECTION.search(_last_human(state)) and _food_logged_today(t) and not _removed_this_turn(state):
        return as_json({"saved": False, "reason": "This is a correction to food already logged, not new food. "
                        "Editing is not available yet. Offer to remove that entry and log it again with "
                        "their numbers. Nothing was saved."})
    eaten_at = parse_clock_time(time_text, t.now)
    row = save_food(t.user, text, eaten_at or t.now)
    result = {"saved": True, "id": row["id"], "confirmation": confirm_entry(row, time_stated=eaten_at is not None)}
    if time_text and eaten_at is None:
        result["note"] = f"Could not read the time {time_text!r}; used the message time."
    return as_json(result)


@tool
def list_food(day: str, config: RunnableConfig) -> str:
    """Food logged today or yesterday, with ids. day: "today" or "yesterday"."""
    t = turn(config)
    d = t.day - timedelta(days=1) if day.strip().lower() == "yesterday" else t.day
    return as_json({"day": d.isoformat(), "entries": [_entry(e) for e in db.food_between(t.user.id, *day_bounds(t.user, d))]})


@tool
def remove_food(entry_ids: list[int], state: Messages, config: RunnableConfig) -> str:
    """Delete food entries by id. Only ids from list_food or log_food results in this conversation.
    If more than one entry could match what they said, ask which one instead of calling this."""
    t = turn(config)
    unseen = set(entry_ids) - ids_shown(state, FOOD_ID_TOOLS)
    if unseen:
        return as_json({"removed": [], "reason": f"Ids {sorted(unseen)} were not shown in this conversation. "
                                                 "Call list_food first and ask which entry if several match."})
    removed = db.delete_food(t.user.id, entry_ids)
    return as_json({"removed": [_entry(e) for e in removed]})


TOOLS = [log_food, list_food, remove_food]
