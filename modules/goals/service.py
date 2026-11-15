"""The goals module's glue: the database and device data in, the pure engine (programs/, feasibility, generator,
adjust) in the middle, code-built text out. Tools (modules/goals/tools.py) and ingest call these.
"""
import copy
import logging
from datetime import date, datetime, timedelta

from connectors.health import Health
from core import db, memory
from programs import library
from programs.schema import Template
from modules.goals import adjust as adjusting
from modules.goals import direction, feasibility, messages, profile as profiles
from modules.goals.generator import apply_limits as apply_limits_to, generate
from programs.validate import validate_plan

log = logging.getLogger(__name__)
PROFILE_DAYS = 42          # the device data a profile reads: 6 weeks before the plan starts
ADJUST_DAYS = 60           # adjustments look this far back (older days are already marked)


def template_for(text: str) -> Template | None:
    """The offered template whose `matches` words appear in their goal ("half marathon"), or None."""
    lowered = text.lower()
    return next((t for t in library.offered() if any(m.lower() in lowered for m in t.matches)), None)


def signals_for(user, use_fixtures: bool, start: date, end: date, now: datetime) -> dict:
    try:
        return Health(use_fixtures).signals(user, start, end, now)
    except Exception as e:           # no device data must never block a goal: chat workouts only
        log.warning("goal signals unavailable (%s)", type(e).__name__)
        manual = Health(use_fixtures).manual_by_day(user, start, end)
        from connectors.health import neutral_day
        return {d: neutral_day(date.fromisoformat(d), None, w) for d, w in manual.items()}


def answers(goal: dict) -> dict:
    return {k: v["value"] for k, v in db.goal_answers(goal["id"]).items()}


def refresh_data_answers(goal: dict, t: Template, sig: dict, start: date) -> None:
    """Data answers what it can, and beats an earlier answer."""
    for key, value in profiles.from_data(t, sig, start).items():
        db.set_goal_answer(goal["id"], key, value, "data")


def profile_for(user, goal: dict, t: Template, start: date, now: datetime, use_fixtures: bool):
    sig = signals_for(user, use_fixtures, start - timedelta(days=PROFILE_DAYS), start - timedelta(days=1), now)
    return profiles.build(t, answers(goal), sig, start, db.active_limits(user.id)), sig


def _start(day: date) -> date:
    return day + timedelta(days=1)      # a plan starts tomorrow


def proposal(user, goal: dict, day: date, now: datetime, use_fixtures: bool) -> tuple[str, dict]:
    """(text, preview) once every question is answered: the plan they'd get, or why it doesn't fit yet."""
    t = library.load(goal["template"])
    start = _start(day)
    event = date.fromisoformat(goal["event_date"]) if goal["event_date"] else None
    p, sig = profile_for(user, goal, t, start, now, use_fixtures)
    variant = feasibility.choose_variant(t, profiles_facts(t, p))
    a = feasibility.assess(t, variant, p.history, start, event, p.anchor_weekday)
    if not a["feasible"]:
        db.update_goal(goal["id"], status="not_feasible", variant=variant, preview_json=a, asked_at=now.isoformat())
        return messages.not_feasible(t, a, event), a
    plan = generate(t, variant, p, start, event, a["entry_week"], a["extra_weeks"],
                    direction.strength_by_phase(db.confirmed_direction_rules(user.id)),
                    max_days=direction.days_by_discipline(db.confirmed_direction_rules(user.id)))
    if plan["errors"]:
        db.update_goal(goal["id"], status="not_feasible", variant=variant, preview_json={**a, "errors": plan["errors"]})
        return ("I couldn't make a plan that keeps every safety check with these answers: "
                + _plain_error(plan["errors"][0]) + " Can you give me a bit more time per session or day?"), a
    finish = None
    if t.event and t.event.measure == "km":
        kinds = sorted({k for d in t.disciplines.values() if d.measure == "km" for k in d.kinds})
        from modules.goals import signals as signals_
        runs = signals_.runs(sig, kinds, start - timedelta(days=PROFILE_DAYS), start - timedelta(days=1))
        target = answers(goal).get("target_time")
        finish = feasibility.predict_finish(runs, t.event.amount, target if isinstance(target, (int, float)) else None)
    db.update_goal(goal["id"], status="proposed", variant=variant, preview_json=a, asked_at=now.isoformat())
    note = messages.strength_short(plan["strength_short"], db.confirmed_direction_rules(user.id), p.days_per_week)
    return messages.preview(t, variant, a, event, plan["days"], p, finish, note), a


def _plain_error(error: str) -> str:
    """A validator error in plain words for them (the time per session is the one they can change)."""
    if "min they have" in error:
        return error.split(": ", 1)[1].replace("they have", "you have") + "."
    return "it would grow too fast for the time left."


def profiles_facts(t: Template, p) -> dict:
    from modules.goals.signals import facts
    return facts(t, p.history, p.days_per_week)


def activate(user, goal: dict, day: date, now: datetime, use_fixtures: bool) -> str:
    """Generate the plan they said yes to and save its days."""
    t = library.load(goal["template"])
    start = _start(day)
    event = date.fromisoformat(goal["event_date"]) if goal["event_date"] else None
    p, _ = profile_for(user, goal, t, start, now, use_fixtures)
    a = goal["preview_json"]
    plan = generate(t, goal["variant"], p, start, event, a["entry_week"], a["extra_weeks"],
                    direction.strength_by_phase(db.confirmed_direction_rules(user.id)),
                    max_days=direction.days_by_discipline(db.confirmed_direction_rules(user.id)))
    if plan["errors"]:
        return "Something changed since I showed you the plan, so nothing was set up. Ask me again to rebuild it."
    db.save_program_days(goal["id"], plan["days"])
    db.update_goal(goal["id"], status="active", asked_at=None)
    if plan["changes"]:
        db.add_program_adjustments(goal["id"], [{"day": start, "kind": "built", "reason": c} for c in plan["changes"]])
    return messages.set_up(t, plan["days"], event)


def active(user) -> tuple[dict, Template, list[dict]] | None:
    goal = db.current_goal(user.id)
    if not goal or goal["status"] != "active":
        return None
    return goal, library.load(goal["template"]), db.program_days(goal["id"])


def paused_ranges(user, day: date) -> list[tuple[date, date, str]]:
    """Dated saved facts that ask to keep it light ("traveling Thu-Sat, keep it light") pause those days."""
    return [(date.fromisoformat(f["start_date"]), date.fromisoformat(f["end_date"]), f["text"])
            for f in memory.current(user, day) if f.get("intensity_cap") and f.get("start_date") and f.get("end_date")]


def apply_limits(user, goal: dict, t: Template, days: list[dict], day: date, now: datetime,
                 use_fixtures: bool) -> list[str]:
    """A limit saved after the plan was made: future days swap or drop what it rules out (the generator's rule),
    and the plan must still pass validate_plan; returns the changes (nothing is saved on an error)."""
    p, _ = profile_for(user, goal, t, days[0]["date"], now, use_fixtures)
    future = copy.deepcopy(days)
    changes = []
    for d in future:
        if d["date"] > day and d["sessions"]:
            week = _OneDay(d)
            changes += apply_limits_to(t, p, week)
            d["sessions"] = week.days[d["date"]]
    errors = validate_plan(future, p, t, goal["variant"])
    if errors:
        return []
    db.save_program_days(goal["id"], future)
    if changes:
        db.add_program_adjustments(goal["id"], [{"day": day, "kind": "limit", "reason": c} for c in changes])
    return changes


class _OneDay:
    """A day in the shape the generator's limit rule reads."""
    def __init__(self, d: dict):
        self.days = {d["date"]: [x for x in d["sessions"]]}


def adjust_quietly(user, now: datetime, day: date, use_fixtures: bool) -> None:
    """Run the adjustment rules on the active plan (from ingest, like the daily score). Never blocks a message."""
    try:
        found = active(user)
        if not found:
            return
        goal, t, days = found
        if not days:
            return
        start = max(days[0]["date"], day - timedelta(days=ADJUST_DAYS))
        sig = signals_for(user, use_fixtures, start, day, now)
        p, _ = profile_for(user, goal, t, days[0]["date"], now, use_fixtures)
        result = adjusting.adjust(days, sig, day, t, p, goal["variant"], paused=paused_ranges(user, day))
        if result["errors"]:
            if goal.get("options_json") != result["options"]:
                db.update_goal(goal["id"], options_json=result["options"])
            return
        if result["changes"]:
            db.save_program_days(goal["id"], result["days"])
            db.add_program_adjustments(goal["id"], result["changes"])
        elif result["days"] != days:
            db.save_program_days(goal["id"], result["days"])
    except Exception as e:
        log.warning("goal adjustments skipped (%s)", type(e).__name__)


def today_of(days: list[dict], day: date) -> dict | None:
    return next((d for d in days if d["date"] == day), None)


def today_request(user, day: date) -> list[dict] | None:
    """Today's program sessions as the planner's request blocks ([{workout_type, duration_min}]), or None."""
    found = active(user)
    if not found:
        return None
    goal, t, days = found
    today = today_of(days, day)
    if not today:
        return None
    blocks = []
    for x in today["sessions"]:
        st = t.session_type(x["discipline"], x["type"])
        if x.get("status", "planned") != "planned" or not st.plan_as:
            continue
        minutes = x.get("minutes") or (x["amount"] if t.disciplines[x["discipline"]].measure == "min" else None)
        if minutes is None and x.get("amount") and t.disciplines[x["discipline"]].measure == "km":
            pace = answers(goal).get("easy_pace")
            minutes = round(x["amount"] * pace) if pace else None
        blocks.append({"workout_type": st.plan_as, "duration_min": round(minutes) if minutes else None})
    return blocks or None


def copy_days(days: list[dict]) -> list[dict]:
    return copy.deepcopy(days)


def rebuild(user, goal: dict, option: dict, day: date, now: datetime, use_fixtures: bool) -> str:
    """Their pick after a break: the rest of the plan again from the last week they completed, with a later date
    or without the lighter weeks. Days already past stay as they were. Validated before anything is saved."""
    t = library.load(goal["template"])
    old = db.program_days(goal["id"])
    start = _start(day)
    event = option["event"] if option["kind"] == "later_date" else date.fromisoformat(goal["event_date"])
    if isinstance(event, str):
        event = date.fromisoformat(event)
    p, _ = profile_for(user, goal, t, start, now, use_fixtures)
    skip = tuple(option.get("skip_weeks") or ())
    kept_weeks = len([n for n in range(option["resume_week"], len(t.variants[goal["variant"]].weeks) + 1)
                      if n not in skip])
    extra = max(0, feasibility.weeks_until(start, event, p.anchor_weekday) - kept_weeks)
    plan = generate(t, goal["variant"], p, start, event, option["resume_week"], extra,
                    direction.strength_by_phase(db.confirmed_direction_rules(user.id)), skip,
                    direction.days_by_discipline(db.confirmed_direction_rules(user.id)))
    past = [d for d in old if d["date"] <= day]
    offset = max((d["week"] for d in past), default=0)
    future = [{**d, "week": d["week"] + offset} for d in plan["days"] if d["date"] > day]
    merged = past + future
    if plan["errors"] or validate_plan(merged, p, t, goal["variant"]):
        return "I couldn't fit the rest of the plan safely that way, so nothing changed. Want the other option?"
    db.save_program_days(goal["id"], merged)
    db.update_goal(goal["id"], event_date=event, options_json=None)
    db.add_program_adjustments(goal["id"], [{"day": day, "kind": option["kind"],
                                             "reason": f"back after a break: from week {option['resume_week']}"}])
    first = next((d for d in future if d["sessions"]), None)
    when = f" Next session: {messages.day_label(first['date'])}, {messages.sessions_line(t, first['sessions'])}." \
        if first else ""
    return (f"Done: the plan picks up from week {option['resume_week']}"
            + (f", with the date moved to {messages.day_label(event)}." if option["kind"] == "later_date"
               else ", skipping the lighter weeks.") + when)
