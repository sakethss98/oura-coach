"""Nutrition tools for the agent: log, edit, list and remove food; meal ideas and recipes.

A correction is never a new entry: edit_food changes the row in place, and log_food refuses a message
that reads as a correction once food was logged today. edit_food and remove_food only touch entries
whose ids the agent has seen in this conversation (list_food / log_food / edit_food results), so they
never guess. Numbers the person states always win over estimates. Meal ideas and recipes are formatted
by code and sent as they are (artifact {"verbatim": text}).
"""
import re

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from core import db
from core.day import day_bounds
from core.labels import clock
from core.timeparse import parse_clock_time, resolve_past_day
from core.tools import Messages, as_json, ids_shown, turn
from modules.nutrition import meals
from modules.nutrition.food import edit_food as change_food, log_food as save_food
from modules.nutrition.messages import confirm_entry
from modules.nutrition.targets import dish_key
from modules.training.baseline import food_totals

CORRECTION = re.compile(
    r"\bcorrection\b|\b(edit|fix|change|update|correct)\s+(that|it|this|the)\b"
    r"|\b(it'?s|it\s+is|it\s+was|that\s+was|that'?s)\b[^.?!]*\d+\s*(k?cals?|g)\b",
    re.I,
)
FOOD_ID_TOOLS = {"log_food", "list_food", "edit_food"}
# Words that single out entries when several are the same food ("the 3pm one", "both", "the last two").
SINGLES_OUT = re.compile(r"\d|\b(both|all|every|each|them|these|those|two|three|first|second|third|last|latest|"
                         r"newest|oldest|earlier|other|morning|afternoon|evening|breakfast|lunch|dinner)\b", re.I)


def _last_human(messages: list) -> str:
    return next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")


def _same_food_unclear(t, entry_ids: list[int], message: str) -> list[str]:
    """Foods with several entries today where their message doesn't say which ("remove the pizza" with two
    pizzas logged): the agent must ask instead of guessing."""
    today_ = db.food_between(t.user.id, *day_bounds(t.user, t.day))
    picked = {dish_key(e["raw_text"]) for e in today_ if e["id"] in entry_ids}
    unclear = [k for k in picked if sum(dish_key(e["raw_text"]) == k for e in today_) > 1]
    return [] if SINGLES_OUT.search(message) else sorted(unclear)


def _food_logged_today(t) -> bool:
    return bool(db.food_between(t.user.id, *day_bounds(t.user, t.day)))


def _entry(e: dict) -> dict:
    return {"id": e["id"], "time": clock(e["timestamp"][11:16]), "text": e["raw_text"],
            "kcal": round(e["est_calories"] or 0), "protein_g": round(e["est_protein_g"] or 0)}


def _numbers(calories, protein_g, carbs_g, fiber_g, caffeine_mg) -> dict:
    return {"calories": calories, "protein_g": protein_g, "carbs_g": carbs_g, "fiber_g": fiber_g,
            "caffeine_mg": caffeine_mg}


@tool
def log_food(text: str, time_text: str | None, state: Messages, config: RunnableConfig,
             calories: float | None = None, protein_g: float | None = None, carbs_g: float | None = None,
             fiber_g: float | None = None, caffeine_mg: float | None = None) -> str:
    """Log something they ate or drank (meal, snack, drink, coffee, alcohol, supplement) with a rough estimate.
    Only what they already had: not a question about a food, not something they plan to eat later.
    text: what they had, in their words, without the time. time_text: a clock time exactly as written, or null.
    calories, protein_g, carbs_g, fiber_g, caffeine_mg: only numbers they stated (they replace the estimate),
    else null."""
    t = turn(config)
    if CORRECTION.search(_last_human(state)) and _food_logged_today(t):
        return as_json({"saved": False, "reason": "This corrects food already logged, so it is not new food. Use "
                        "edit_food on that entry (its id is in an earlier log_food or list_food result; call "
                        "list_food if you don't have it). Nothing was saved."})
    eaten_at = parse_clock_time(time_text, t.now)
    row = save_food(t.user, text, eaten_at or t.now, _numbers(calories, protein_g, carbs_g, fiber_g, caffeine_mg))
    result = {"saved": True, "id": row["id"], "confirmation": confirm_entry(row, time_stated=eaten_at is not None)}
    if time_text and eaten_at is None:
        result["note"] = f"Could not read the time {time_text!r}; used the message time."
    return as_json(result)


@tool
def edit_food(entry_id: int, state: Messages, config: RunnableConfig, new_text: str | None = None,
              feedback: str | None = None, time_text: str | None = None, calories: float | None = None,
              protein_g: float | None = None, carbs_g: float | None = None, fiber_g: float | None = None,
              caffeine_mg: float | None = None) -> str:
    """Correct an entry already logged, in place ("correction, it's 30 cal", "it was cheese pizza, edit that").
    Never log a correction as new food. entry_id: from a log_food or list_food result in this conversation.
    new_text: the corrected description in full, keeping the amount unless they changed it ("5 slices of
    pizza" + "it was cheese pizza with olives" = "5 slices of cheese pizza with olives"), or null if only numbers
    change; the fields they never stated are estimated again for it. feedback: what they said about the old
    estimate without giving a number ("I don't think it's 60g protein"), or null. time_text: a corrected clock time exactly as written, or null.
    calories, protein_g, carbs_g, fiber_g, caffeine_mg: only numbers they stated, else null."""
    t = turn(config)
    if entry_id not in ids_shown(state, FOOD_ID_TOOLS):
        return as_json({"updated": False, "reason": f"Entry {entry_id} was not shown in this conversation. "
                                                    "Call list_food first and ask which entry if several match."})
    row = db.get_food(t.user.id, entry_id)
    if row is None:
        return as_json({"updated": False, "reason": f"No entry {entry_id}."})
    eaten_at = parse_clock_time(time_text, t.now) if time_text else None
    updated = change_food(t.user, row, new_text, _numbers(calories, protein_g, carbs_g, fiber_g, caffeine_mg),
                          eaten_at, feedback)
    return as_json({"updated": True, "id": entry_id, "was": confirm_entry(row, verb="Was"),
                    "confirmation": confirm_entry(updated, time_stated=eaten_at is not None, verb="Updated")})


@tool
def list_food(day: str, config: RunnableConfig) -> str:
    """Food logged on one day, with ids and the day's totals. day: the day exactly as they said it
    ("today", "yesterday", "Oct 10", "last Monday", "3 days ago")."""
    t = turn(config)
    d = resolve_past_day(day, t.day)
    if d is None:
        return as_json({"entries": [], "reason": f"Could not read the day {day!r} (or it is in the future). "
                                                  "Ask which day, like Oct 10 or last Monday."})
    entries = db.food_between(t.user.id, *day_bounds(t.user, d))
    totals = food_totals(entries)
    return as_json({"day": f"{d:%A %b} {d.day}", "entries": [_entry(e) for e in entries],
                    "totals": {"kcal": round(totals["calories"]), "protein_g": round(totals["protein_g"]),
                               "carbs_g": round(totals["carbs_g"]), "fiber_g": round(totals["fiber_g"])}})


@tool
def remove_food(entry_ids: list[int], state: Messages, config: RunnableConfig) -> str:
    """Delete food entries by id. Only ids from list_food or log_food results in this conversation.
    If more than one entry could match what they said, ask which one instead of calling this."""
    t = turn(config)
    unseen = set(entry_ids) - ids_shown(state, FOOD_ID_TOOLS)
    if unseen:
        return as_json({"removed": [], "reason": f"Ids {sorted(unseen)} were not shown in this conversation. "
                                                 "Call list_food first and ask which entry if several match."})
    unclear = _same_food_unclear(t, entry_ids, _last_human(state))
    if unclear:
        return as_json({"removed": [], "reason": f"Several entries today are {', '.join(unclear)} and they didn't "
                                                 "say which. Ask which one (give the times). Nothing was removed."})
    removed = db.delete_food(t.user.id, entry_ids)
    return as_json({"removed": [_entry(e) for e in removed]})


@tool(response_format="content_and_artifact")
def suggest_meal(meal: str | None, ingredients: list[str] | None, constraints: str | None,
                 config: RunnableConfig) -> tuple[str, dict]:
    """Meal ideas ("dinner ideas", "what should I eat now?"): 1-2 dishes with portions, sized for one meal,
    different from recent ideas. meal: "breakfast", "lunch", "dinner", "snack", or null. ingredients: what
    they said they have, or null. constraints: what they asked for ("quick", "high protein"), or null."""
    t = turn(config)
    ideas = meals.suggest(t.user, t.day, t.now, t.use_fixtures, meal, ingredients, constraints)
    dishes = [o["dish"] for o in ideas["options"]]
    return (as_json({"sent_as_is": True, "options_in_order": dishes,
                     "note": "Sent to them as is: do not repeat it. For 'the second one' or a change "
                             "('make it higher protein'), call meal_recipe with that dish's name."}),
            {"verbatim": ideas["text"]})


@tool(response_format="content_and_artifact")
def meal_recipe(dish: str, change: str | None, ingredients: list[str] | None,
                config: RunnableConfig) -> tuple[str, dict]:
    """The recipe for one dish, usually one of the meal ideas ("give me the second one"), with portions and
    steps. dish: its name exactly as in the ideas. change: how to revise the same dish ("higher protein",
    "no rice"), or null. ingredients: what they said they have, or null."""
    t = turn(config)
    result = meals.recipe(t.user, t.day, t.now, t.use_fixtures, dish, change, ingredients)
    return (as_json({"sent_as_is": True, "dish": result["dish"], "note": "Sent to them as is: do not repeat it."}),
            {"verbatim": result["text"]})


TOOLS = [log_food, edit_food, list_food, remove_food, suggest_meal, meal_recipe]
