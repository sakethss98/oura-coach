"""Nutrition tools for the agent: log, edit, list and remove food; meal ideas and recipes.

A correction is never a new entry: edit_food changes the row in place, and log_food refuses a message
that reads as a correction once food was logged today. edit_food and remove_food only touch entries
whose ids the agent has seen in this conversation (list_food / log_food / edit_food results), so they
never guess. Numbers the person states always win over estimates. Meal ideas and recipes are formatted
by code and sent as they are (artifact {"verbatim": text}).
"""
import json
import re
from datetime import timedelta
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from connectors.health import Health
from core import db, memory
from core.day import bounds_of_days, day_bounds
from core.labels import clock
from core.timeparse import parse_clock_time, resolve_dates, resolve_past_day
from core.tools import GroupBy, Messages, as_json, ids_shown, range_for, range_label, turn, with_range_args
from modules.nutrition import fueling, history, meals
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
# An entry is changed or removed only when their message says so ("actually", "correction", "change that to",
# "it was", "remove", "delete", "wrong"...), or answers the coach's question about which entry to change.
# Otherwise a food they name is new food: "had a banana" right after a log is a new row, not an edit.
EDIT_INTENT = re.compile(r"\b(actually|correction|correct(ed)?|change|changed|it was|that was|it wasn'?t|wrong|edit|"
                         r"fix|update|instead|not right|i don'?t think|was really|undo)\b", re.I)
REMOVE_INTENT = re.compile(r"\b(remove|delete|undo|wrong|take (it|that|them) off|get rid of|didn'?t (have|eat)|"
                           r"never (had|ate)|scratch that|cancel)\b", re.I)
ASKED_WHICH = re.compile(r"\b(remove|delete|edit|change|correct|fix|which (one|entry))\b[^?]*\?", re.I)
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


def _asked_which(messages: list) -> bool:
    """The coach's last reply asked which entry to change or remove (their answer: "the 3pm one", "both")."""
    replies = [m for m in messages if isinstance(m, AIMessage) and not m.tool_calls and str(m.content).strip()]
    return bool(replies) and bool(ASKED_WHICH.search(str(replies[-1].content)))


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
        return as_json({"saved": False, "reason": "this corrects food already logged, so it is not new food; "
                        "nothing was saved (edit_food changes an entry in place; ids are in earlier log_food or "
                        "list_food results)"})
    eaten_at = parse_clock_time(time_text, t.now)
    row = save_food(t.user, text, eaten_at or t.now, _numbers(calories, protein_g, carbs_g, fiber_g, caffeine_mg))
    result = {"saved": True, "id": row["id"], "confirmation": confirm_entry(row, time_stated=eaten_at is not None)}
    if time_text and eaten_at is None:
        result["time_read"] = f"could not read the time {time_text!r}; used the message time"
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
    if not (EDIT_INTENT.search(_last_human(state)) or _asked_which(state)):
        return as_json({"updated": False, "reason": "their message doesn't say it corrects an entry, so nothing was "
                                                    "changed; food they name is new food (log_food)"})
    if entry_id not in ids_shown(state, FOOD_ID_TOOLS):
        return as_json({"updated": False, "reason": f"entry {entry_id} was not shown in this conversation "
                                                    "(list_food shows today's entries with their ids)"})
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
        return as_json({"entries": [], "could_not_read": day, "reason": "not a day that can be read, or it is "
                                                                          "in the future"})
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
    if not (REMOVE_INTENT.search(_last_human(state)) or _asked_which(state)):
        return as_json({"removed": [], "reason": "their message doesn't ask to remove anything, so nothing was "
                                                 "removed"})
    unseen = set(entry_ids) - ids_shown(state, FOOD_ID_TOOLS)
    if unseen:
        return as_json({"removed": [], "reason": f"ids {sorted(unseen)} were not shown in this conversation "
                                                 "(list_food shows today's entries with their ids)"})
    unclear = _same_food_unclear(t, entry_ids, _last_human(state))
    if unclear:
        return as_json({"removed": [], "unclear_which": unclear, "reason": f"several entries today are "
                        f"{', '.join(unclear)} and the message doesn't say which; nothing was removed"})
    removed = db.delete_food(t.user.id, entry_ids)
    return as_json({"removed": [_entry(e) for e in removed]})


Nutrient = Literal["calories", "protein_g", "carbs_g", "fiber_g", "caffeine_mg", "alcohol_drinks"]


@tool("food_totals")
@with_range_args
def food_range(config: RunnableConfig, nutrients: list[Nutrient] | None = None, days_back: int | None = None,
               start_text: str | None = None, end_text: str | None = None, per_day: bool = False,
               group_by: GroupBy = None) -> str:
    """Food over a range of days: totals, the average per logged day, and the lowest and highest day ("how much
    protein did I have over the last week?", "which day this week was I lowest on protein?"). nutrients: which
    ones, or null for calories and protein. per_day: true to list each day. At most the last 90 days."""
    t = turn(config)
    found = range_for(t, days_back, start_text, end_text)
    if isinstance(found, dict):
        return as_json(found)
    start, end, note = found
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    totals = {d.isoformat(): food_totals(db.food_between(t.user.id, *bounds))
              for d, bounds in bounds_of_days(t.user, days).items()}
    result = history.summarize(totals, list(nutrients or []), t.day, per_day, group_by)
    return as_json({"range": range_label(start, end), **({"coverage": note} if note else {}), **result})


@tool(response_format="content_and_artifact")
def suggest_meal(meal: str | None, ingredients: list[str] | None, constraints: str | None,
                 config: RunnableConfig) -> tuple[str, dict]:
    """Meal ideas ("dinner ideas", "what should I eat now?"): 1-2 dishes with portions, sized for one meal,
    different from recent ideas. meal: "breakfast", "lunch", "dinner", "snack", or null. ingredients: what
    they said they have, or null. constraints: what they asked for ("quick", "high protein"), or null."""
    t = turn(config)
    ideas = meals.suggest(t.user, t.day, t.now, t.use_fixtures, meal, ingredients, constraints)
    dishes = [o["dish"] for o in ideas["options"]]
    return (as_json({"sent_as_is": True, "options_in_order": dishes}),
            {"verbatim": ideas["text"]})


@tool(response_format="content_and_artifact")
def meal_recipe(dish: str, change: str | None, ingredients: list[str] | None,
                config: RunnableConfig) -> tuple[str, dict]:
    """The recipe for one dish, usually one of the meal ideas ("give me the second one"), with portions and
    steps. dish: its name exactly as in the ideas. change: how to revise the same dish ("higher protein",
    "no rice"), or null. ingredients: what they said they have, or null."""
    t = turn(config)
    result = meals.recipe(t.user, t.day, t.now, t.use_fixtures, dish, change, ingredients)
    return (as_json({"sent_as_is": True, "dish": result["dish"]}),
            {"verbatim": result["text"]})


FuelPart = Literal["day_before", "before", "during", "fluids", "caffeine", "after"]
RUN_TYPES = {"running", "easy_run", "long_run"}      # Oura activity / manual workout types that are runs
RUNS_LOOK_BACK_DAYS = 28


def _body_weight(t) -> float | None:
    """kg from goals.yaml (an override), else Oura personal_info, else their saved weight. Never estimated."""
    override = t.user.goals()["nutrition"].get("weight_kg")
    if override:
        return override
    try:
        oura_kg = Health(t.use_fixtures).bundle(t.user, t.day, now=t.now, allow_stale=True)["body"]["weight_kg"]
    except Exception:
        oura_kg = None
    return oura_kg or memory.body_weight(t.user)


def _recent_runs(t) -> list[dict]:
    """[{"minutes", "distance_km"}] of their runs in the last 4 weeks (Oura and added in chat)."""
    start = t.day - timedelta(days=RUNS_LOOK_BACK_DAYS)
    health = Health(t.use_fixtures)
    try:
        oura = [w for d in health.history(t.user, start, t.day, t.now).values() for w in d["workouts"]]
    except Exception:
        oura = []
    manual = [w for ws in health.manual_by_day(t.user, start, t.day).values() for w in ws]
    return ([{"minutes": w["minutes"], "distance_km": w["distance_km"]} for w in oura if w["activity"] in RUN_TYPES]
            + [{"minutes": w["minutes"], "distance_km": None} for w in manual
               if w["workout_type"] in RUN_TYPES and w["minutes"]])


def _carbs_lately(t) -> dict | None:
    days = [t.day - timedelta(days=i) for i in range(fueling.RECENT_DAYS, 0, -1)]
    entries = {d: db.food_between(t.user.id, *bounds) for d, bounds in bounds_of_days(t.user, days).items()}
    return fueling.recent_carbs({d.isoformat(): food_totals(e)["carbs_g"] if e else None for d, e in entries.items()})


def _they_said(number: float, messages: list) -> bool:
    """The number appears in one of their messages (so "as you said" is true)."""
    said = " ".join(str(m.content) for m in messages if isinstance(m, HumanMessage))
    return re.search(rf"(?<![\d.]){number:g}(?![\d.])", said) is not None


@tool(response_format="content_and_artifact")
def fueling_plan(state: Messages, config: RunnableConfig, day_text: str | None = None, minutes: int | None = None,
                 distance_km: float | None = None, parts: list[FuelPart] | None = None,
                 race: bool = False) -> tuple[str, dict]:
    """Amounts for eating and drinking around a run (a long run, a race, a 10k): carbs the day before, the meal
    before, carbs during, drinking and sodium, caffeine, protein after. Code computes them from GSSI ranges and
    their body weight; the text is sent to them as is.
    day_text: the run's day exactly as they said it ("Saturday", "tomorrow"), or null.
    minutes, distance_km: only numbers THEY wrote in their messages ("a 10k" = distance_km 10, "a half marathon" =
    21.1, "a 90 min run" = minutes 90), never numbers from an earlier plan; else null (code finds their usual run).
    parts: only what they asked about (["caffeine"] for coffee or energy drinks, ["fluids"] for water or
    electrolytes), or null for the whole plan. race: true for a race."""
    return _fueling(turn(config), state, day_text, minutes, distance_km, parts, race)


def _fueling(t, state: list, day_text: str | None = None, minutes: int | None = None,
             distance_km: float | None = None, parts: list[str] | None = None, race: bool = False) -> tuple[str, dict]:
    run_day = None
    if day_text:
        dates = resolve_dates(day_text, None, t.day)
        if dates is None:
            return as_json({"could_not_read": day_text, "reason": "the run's day can't be read"}), {}
        run_day = dates[0]
    weight = _body_weight(t)
    if minutes and not _they_said(minutes, state):
        minutes = None                      # a number from an earlier plan, not from them: code finds the length
    run_min, how = fueling.run_minutes(minutes, distance_km, _recent_runs(t))
    result = fueling.plan(run_min, how, weight, parts, run_day, t.day, _carbs_lately(t), race,
                          ask_weight=weight is None and db.ask_once(t.user.id, "body_weight"), distance_km=distance_km)
    # Facts only: instructions in a tool result get echoed back as the reply (the skill body says what to do).
    return (as_json({"sent_as_is": True, "run_day": f"{run_day:%a %b %d}" if run_day else None, "minutes": run_min,
                     "weight_known": weight is not None, "carb_load": result["carb_load"],
                     "asked_for_weight": result["text"].endswith(fueling.ASK_WEIGHT)}),
            {"verbatim": result["text"], "code_sources": result["source_list"]})


def _last_fueling_args(messages: list) -> dict | None:
    """The arguments of the last fueling_plan call in this conversation, or None."""
    for m in reversed(messages):
        for call_ in reversed(getattr(m, "tool_calls", None) or []):
            if call_["name"] == "fueling_plan":
                return call_["args"]
    return None


LB_PER_KG = 2.20462


@tool(response_format="content_and_artifact")
def save_body_weight(weight: float, unit: Literal["kg", "lb"], state: Messages,
                     config: RunnableConfig) -> tuple[str, dict]:
    """Save their body weight when they tell you it ("I'm 62 kg", "about 140 lb"), exactly as they said it:
    weight = the number, unit = "kg" or "lb" (code converts). Used for fueling amounts. Replaces a saved weight.
    Never guess a weight they didn't give. A fueling plan from earlier in this conversation is redone in grams."""
    t = turn(config)
    kg = weight / LB_PER_KG if unit == "lb" else weight
    low, high = memory.WEIGHT_RANGE_KG
    if not low <= kg <= high:
        return as_json({"saved": False, "reason": f"{weight:g} {unit} doesn't look like a body weight"}), {}
    fact = memory.save_body_weight(t.user, kg)
    saved = {"saved": True, "id": fact["id"], "fact": fact["text"]}
    earlier = _last_fueling_args(state)
    if earlier is None:
        return as_json(saved), {}
    # The plan they asked for, again, now in grams: code-built and sent as is (never the model's own math).
    content, artifact = _fueling(t, state, **earlier)
    redone = json.loads(content)
    return (as_json({**saved, "fueling_plan_redone_in_grams": True, "sent_as_is": True,
                     "carb_load": redone.get("carb_load")}), artifact)


TOOLS = [log_food, edit_food, list_food, remove_food, suggest_meal, meal_recipe, food_range, fueling_plan,
         save_body_weight]
