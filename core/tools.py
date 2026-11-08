"""Core agent tools (saved memory, today's data, Oura refresh) and the helpers every tool uses.

Tools get who/when from the run config (`configurable`: user_id, now, session_id, use_fixtures),
never from globals, so one process can serve several users. Tool results are short JSON for the
model; text the person should see exactly as built goes in the artifact as {"verbatim": text}.
"""
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from connectors.health import Health
from core import db, history, memory, score, users
from core.day import day_of
from core.timeparse import resolve_dates, resolve_past_day, resolve_range
from modules.registry import enabled_modules
import today   # the shared day view (root module, also used by the training planner)


@dataclass(frozen=True)
class Turn:
    user: users.User
    now: datetime
    day: date
    session_id: int | None
    use_fixtures: bool


def turn(config: RunnableConfig) -> Turn:
    c = config["configurable"]
    user = users.get(c["user_id"])
    now = datetime.fromisoformat(c["now"])
    return Turn(user, now, day_of(user, now), c.get("session_id"), bool(c.get("use_fixtures")))


# A tool argument of this type receives the conversation so far (hidden from the model).
Messages = Annotated[list, InjectedState("messages")]


def as_json(data) -> str:
    return json.dumps(data, default=str)


def ids_shown(messages: list, tool_names: set[str]) -> set[int]:
    """Entry ids that appeared in this conversation's results of these tools."""
    found = set()
    for m in messages:
        if isinstance(m, ToolMessage) and m.name in tool_names:
            found |= {int(i) for i in re.findall(r'"id": (\d+)', str(m.content))}
    return found


GroupBy = Literal["weekday", "week"] | None
# How every range tool reads its days (the same words in each tool's description).
RANGE_ARGS = ("days_back: a number of days ending today (\"over the last week\" / \"past 7 days\" = 7), or null. "
              "start_text / end_text: the days exactly as they said them (\"this week\", \"last week\", \"this month\", "
              "\"Oct 1\" to \"Oct 10\"), or null. group_by: \"weekday\" or \"week\" for patterns, or null.")


def with_range_args(func):
    """Appends RANGE_ARGS to a tool's description (put it under @tool), so each range tool explains them."""
    func.__doc__ = func.__doc__.rstrip() + "\n    " + RANGE_ARGS
    return func


def range_for(t: Turn, days_back: int | None, start_text: str | None, end_text: str | None):
    """(start, end, note) of the range they asked about, or a result explaining it couldn't be read."""
    found = resolve_range(days_back, start_text, end_text, t.day)
    if found is None:
        return {"reason": f"Could not read the days {start_text!r} to {end_text!r} (or they are in the future). "
                          "Ask which days, like 'this week' or 'Oct 1 to Oct 10'."}
    return found


def range_label(start: date, end: date) -> str:
    return f"{start:%a %b} {start.day}" if start == end else f"{start:%a %b} {start.day} to {end:%a %b} {end.day}"


# --- saved memory -------------------------------------------------------------

@tool
def remember(fact: str, kind: str, start_text: str | None, end_text: str | None, config: RunnableConfig,
             keep_light: Literal["light", "very_light"] | None = None) -> str:
    """Save a fact the person explicitly asked you to remember.

    kind: goal, diet, preference, instruction, or context (temporary, usually with dates).
    start_text / end_text: the first and last day exactly as written ("Thu", "Sat", "tomorrow"), or null.
    keep_light: "light" when they want training kept light while it applies ("traveling, keep it light": no
    HIIT or long runs), "very_light" when only walking, mobility or rest makes sense ("I'm sick"), else null.
    """
    t = turn(config)
    start = end = None
    if start_text or end_text:
        dates = resolve_dates(start_text, end_text, t.day)
        if dates is None:
            return as_json({"saved": False, "reason": f"Could not read the dates {start_text!r}-{end_text!r}. "
                                                      "Ask them to say the days again, like Thu-Sat or Oct 12."})
        start, end = dates
    fact_row = memory.remember(t.user, fact, kind, start, end, keep_light)
    return as_json({"saved": True, "id": fact_row["id"], "fact": memory.describe(fact_row),
                    **({"workouts_kept_light": True} if fact_row["intensity_cap"] else {})})


# Words that make removing several facts at once clearly what they asked for.
FORGET_SEVERAL = re.compile(r"\b(both|all|every|everything|them)\b", re.I)


@tool
def forget(memory_ids: list[int], state: Messages, config: RunnableConfig) -> str:
    """Remove saved facts by id (ids are listed with the saved facts). Returns exactly what was removed.
    If several facts could match what they said, list them and ask which instead of calling this."""
    last = next((str(m.content) for m in reversed(state) if isinstance(m, HumanMessage)), "")
    if len(set(memory_ids)) > 1 and not FORGET_SEVERAL.search(last):
        return as_json({"removed": [], "reason": "Several facts match and they didn't say all of them. List them "
                                                 "and ask which one (or both). Nothing was removed."})
    removed = memory.forget(turn(config).user, memory_ids)
    return as_json({"removed": [memory.describe(f) for f in removed]} if removed
                   else {"removed": [], "reason": "No saved fact with those ids."})


@tool
def list_memory(config: RunnableConfig) -> str:
    """What is saved about the person: undated facts and dated ones that haven't ended (upcoming included).
    Facts whose dates have passed are not shown."""
    t = turn(config)
    return as_json([{"id": f["id"], "fact": memory.describe(f)} for f in memory.current(t.user, t.day)])


# --- today's data -------------------------------------------------------------

@tool
def get_today(include_health: bool, config: RunnableConfig) -> str:
    """Today so far: food eaten (with ids), totals, daily targets and what is left, today's plans and
    workouts. include_health: true to also get readiness, sleep, HRV, steps (fetches Oura if needed)."""
    t = turn(config)
    snap = today.snapshot(t.user, t.now, t.use_fixtures, allow_stale=not include_health)
    data = {
        "food_today": snap["food_today"],
        "food_totals_today": snap["food_totals"]["today"],
        "targets": snap["targets"]["targets"] or snap["targets"]["reason"],
        "left_today": snap["remaining"],
        "plans_today": [{"workout": (p["plan_json"] or {}).get("workout_type"),
                         "time": (p["plan_json"] or {}).get("time_slot"),
                         "done": p["completed"] == 1} for p in snap["plans_today"]],
        "activity_today": snap["activity_today"],
    }
    if include_health:
        o = snap["oura"]["today"]
        data["health"] = {
            "readiness": o["readiness_score"], "sleep_score": o["sleep_score"], "sleep_hours": o["sleep_hours"],
            "hrv_ms": o["hrv_ms"], "resting_hr": o["resting_hr"], "steps": o["steps"],
            "how_hard_today": snap["band"]["plain"],
            "vs_usual": {k: {"today": v["today"], "usual": v["avg_7d"]}
                         for k, v in snap["baseline"]["comparison"].items()},
        }
    return as_json(data)


@tool
def refresh_health(config: RunnableConfig) -> str:
    """Fetch Oura again right now. Says whether anything new came in, and today's readiness and workouts."""
    t = turn(config)
    health = Health(t.use_fixtures)
    changed = health.refresh(t.user, t.day, t.now)
    workouts = health.oura.workouts(t.user, t.day)
    result = {"anything_new": changed, "readiness": health.readiness(t.user, t.day)["score"],
              "oura_workouts_today": [{"activity": w["activity"], "start": w["start"][11:16],
                                       "end": w["end"][11:16]} for w in workouts]}
    if not workouts:
        result["note"] = ("If they said they worked out: Oura only shares workouts the ring recorded (Apple Watch "
                          "sessions don't come through). Say that and offer to add it (mark_done with what they said).")
    return as_json(result)


@tool
def get_score(day: str | None, config: RunnableConfig) -> str:
    """Their daily score ("how's my day?", "how did yesterday go?"): 0-100, the breakdown, and how it
    compares to their own recent average. day: the day exactly as they said it, or null for today."""
    t = turn(config)
    d = resolve_past_day(day, t.day)
    if d is None:
        return as_json({"score": None, "reason": f"Could not read the day {day!r}. Ask which day."})
    result = score.get_score(t.user, d, t.now, t.use_fixtures)
    return as_json({
        "day": f"{d:%A %b} {d.day}", "final": result["final"], "headline": result["headline"],
        "parts": result["parts"], "left_out": [f"{p['name']} ({p['note']})" for p in result["left_out"]],
        "still_helps_today": result["next"],
        "how_to_reply": "Start with the headline as given. Then at most one line on what went well and, if the "
                        "day isn't final, one kind suggestion from still_helps_today (e.g. 'a 20-min walk would "
                        "round today out'). Numbers only as given. No blame or guilt words.",
    })


HealthMetric = Literal["readiness", "sleep_score", "sleep_hours", "hrv", "resting_hr", "steps"]


@tool
@with_range_args
def health_range(config: RunnableConfig, metrics: list[HealthMetric] | None = None, days_back: int | None = None,
                 start_text: str | None = None, end_text: str | None = None, group_by: GroupBy = None,
                 compare_days: bool = False) -> str:
    """Readiness, sleep, HRV, resting heart rate and steps over a range of days, with averages ("how did I sleep
    this week?"). compare_days: true to get the best and worst day and what was different about each ("when did
    I sleep best this month, and what was different that day?"). metrics: which ones, or null for readiness and
    sleep. At most the last 90 days."""
    t = turn(config)
    found = range_for(t, days_back, start_text, end_text)
    if isinstance(found, dict):
        return as_json(found)
    start, end, note = found
    health = Health(t.use_fixtures)
    first = start - timedelta(days=1)           # the day before the range: what led into its first night
    by_day = health.history(t.user, first, end, t.now)
    features = None
    if compare_days:
        days = [first + timedelta(days=i) for i in range((end - first).days + 1)]
        summaries = {m.name: m.daily_summaries(t.user, days, by_day) for m in enabled_modules(t.user, health=health)}
        features = {d.isoformat(): history.features(d, by_day, summaries.get("training"), summaries.get("nutrition"))
                    for d in days[1:]}
    result = history.summarize(by_day, start, end, list(metrics or []), group_by, features)
    return as_json({"range": range_label(start, end), **({"note": note} if note else {}), **result})


@tool
def day_summary(day_text: str, config: RunnableConfig) -> str:
    """What happened on one day: food totals, workouts and plans, readiness and sleep, and the final score
    if there is one ("summarize Oct 6"). day_text: the day exactly as they said it."""
    t = turn(config)
    d = resolve_past_day(day_text, t.day)
    if d is None:
        return as_json({"day": None, "reason": f"Could not read the day {day_text!r}. Ask which day."})
    health = Health(t.use_fixtures)
    m = health.history(t.user, d, d, t.now)[d.isoformat()]
    summaries = {mod.name: mod.daily_summaries(t.user, [d], {d.isoformat(): m})[d.isoformat()]
                 for mod in enabled_modules(t.user, health=health)}
    data = {"day": range_label(d, d), **({"note": "today, still going"} if d == t.day else {}),
            "readiness": m["readiness_score"], "sleep_score": m["sleep_score"], "sleep_hours": m["sleep_hours"],
            "hrv_ms": m["hrv_ms"], "resting_hr": m["resting_hr"], "steps": m["steps"]}
    if "nutrition" in summaries:
        f = summaries["nutrition"]
        data["food"] = ({"entries": f["entries"], "kcal": round(f["calories"]), "protein_g": round(f["protein_g"]),
                         "carbs_g": round(f["carbs_g"]), "fiber_g": round(f["fiber_g"])}
                        if f["entries"] else "nothing logged")
    if "training" in summaries:
        data["training"] = summaries["training"]
    stored = db.get_daily_score(t.user.id, d)
    data["score"] = stored["score"] if stored and stored["final"] else None
    return as_json(data)


CORE_TOOLS = [remember, forget, list_memory, get_today, get_score, refresh_health, health_range, day_summary]
CORE_HELP = ['"remember I eat eggs", "forget that", "what do you know about me?"',
             '"how\'s my day?": your score so far, against your own recent average',
             '"what\'s left to eat today?", "update from Oura"']

