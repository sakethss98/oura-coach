"""Everything known about today, at a given moment: Oura, calendar, food, activity, targets.

Shared by the graph (fetch_data / gather_today), /today, and food questions, so they all
see the same numbers. Oura and the calendar are re-fetched on every call; the database is
read fresh too, so nothing depends on leftovers from an earlier run.
"""
from datetime import date, datetime, time, timedelta

import yaml

import activity
import db
from baseline import compute_baseline, food_totals
from calendar_client import fetch_calendar, fixture_calendar, plan_window, schedule_for
from config import DAY_END_HOUR, DAY_START_HOUR, GOALS_PATH, TZ
from nutrition import daily_targets, recent_food, remaining
from oura_client import OuraClient, body_metrics, daily_metrics, latest_day, load_fixtures

DAILY_ACTIVITY_KEYS = ["activity_score", "steps", "active_calories", "high_activity_min", "medium_activity_min"]


def load_goals() -> dict:
    return yaml.safe_load(GOALS_PATH.read_text())


def fixtures_now(at: time | None = None) -> datetime:
    """Fixtures mode: "today" is the newest fixture day; the clock time is now (or `at`)."""
    clock = at or datetime.now(TZ).time().replace(second=0, microsecond=0)
    return datetime.combine(latest_day(load_fixtures()), clock, TZ)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


# --- remote data (Oura + calendar) ------------------------------------------

def fetch_oura(day: date, use_fixtures: bool) -> dict:
    raw = load_fixtures() if use_fixtures else OuraClient().fetch_range(day - timedelta(days=7),
                                                                         day + timedelta(days=1))
    today = daily_metrics(raw, day)
    workouts = {}
    for i in range(7):  # today + the 6 days before (the weekly hard-day window)
        d = day - timedelta(days=i)
        workouts[d.isoformat()] = daily_metrics(raw, d)["workouts"]
    return {
        "today": today,
        "history": [daily_metrics(raw, day - timedelta(days=i)) for i in range(7, 0, -1)],
        "body": body_metrics(raw),
        "workouts_by_day": workouts,
        "workout_records_fetched": len(raw["workout"]["data"]),
    }


def fetch_schedule(day: date, use_fixtures: bool) -> dict:
    calendar, source = fixture_calendar() if use_fixtures else (fetch_calendar(), "live")
    sched = schedule_for(calendar, day)
    return {
        "busy": [[_hm(s), _hm(e), title] for s, e, title in sched["busy"]],
        "all_day": sched["all_day"],
        "free": [[_hm(s), _hm(e)] for s, e in sched["free"]],
        "source": source,
    }


# --- local data (database) + deterministic math ------------------------------

def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), TZ)


def window_now(schedule: dict, day: date, now: datetime) -> list[str] | None:
    busy = [(_at(day, s), _at(day, e)) for s, e, _ in schedule["busy"]]
    window = plan_window(busy, now, _at(day, f"{DAY_START_HOUR:02d}:00"), _at(day, f"{DAY_END_HOUR:02d}:00"))
    return [_hm(window[0]), _hm(window[1])] if window else None


def _public(item: dict) -> dict:
    """An activity item with times as HH:MM strings (for state, prompts and replies)."""
    return {
        "source": item["source"], "activity": item["activity"], "minutes": item["minutes"],
        "start": _hm(item["start"]) if item["start"] else None,
        "end": _hm(item["end"]) if item["end"] else None,
        "hard": item["hard"], "feeling": item["feeling"],
    }


def day_activity(day: date, oura_workouts_by_day: dict, rules: dict) -> dict[str, list[dict]]:
    """Merged activity items for today and the 6 days before, keyed by YYYY-MM-DD."""
    sessions = db.sessions_between(day - timedelta(days=6), day)
    result = {}
    for i in range(7):
        key = (day - timedelta(days=i)).isoformat()
        oura = activity.oura_items(oura_workouts_by_day.get(key, []), rules)
        mine = activity.session_items([s for s in sessions if s["date"] == key], rules)
        result[key] = activity.merge_activity(oura, mine)
    return result


def day_context(day: date, now: datetime, oura: dict, schedule: dict, band: str, goals: dict) -> dict:
    rules = goals["activity"]
    items = day_activity(day, oura["workouts_by_day"], rules)
    today_items = items[day.isoformat()]
    yesterday_items = items[(day - timedelta(days=1)).isoformat()]
    previous_days = [items[(day - timedelta(days=i)).isoformat()] for i in range(1, 7)]

    food_today = db.food_entries(day)
    totals = {"yesterday": food_totals(db.food_entries(day - timedelta(days=1))), "today": food_totals(food_today)}
    targets = daily_targets(oura["body"], goals["nutrition"])

    return {
        "now": _hm(now),
        "window": window_now(schedule, day, now),
        "activity_today": [_public(i) for i in today_items],
        "activity_yesterday": [_public(i) for i in yesterday_items],
        "worked_out_today": activity.worked_out(today_items, band, rules),
        "yesterday_hard": activity.hard_reason(yesterday_items),
        "hard_days_prev6": sum(1 for d in previous_days if activity.hard_reason(d)),
        "oura_activity_today": {k: oura["today"].get(k) for k in DAILY_ACTIVITY_KEYS},
        "workout_records_fetched": oura["workout_records_fetched"],
        "food_today": [
            {"time": e["timestamp"][11:16], "text": e["raw_text"], "calories": e["est_calories"],
             "protein_g": e["est_protein_g"]}
            for e in food_today
        ],
        "food_totals": totals,
        "targets": targets,
        "remaining": remaining(targets["targets"], totals["today"]),
        "recent_food": recent_food(food_today, now),
        "context_notes": db.active_context_notes(day),
        "first_plan_today": db.count_planned(day) == 0,
        "sessions_today": [s for s in db.sessions_between(day, day)],
    }


def snapshot(now: datetime, use_fixtures: bool) -> dict:
    """Fresh Oura + calendar + database view of today at `now` (for /today and questions)."""
    day = now.date()
    goals = load_goals()
    oura = fetch_oura(day, use_fixtures)
    schedule = fetch_schedule(day, use_fixtures)
    baseline = compute_baseline(oura["today"], oura["history"], goals["schedule"]["readiness_floor"])
    return {
        "day": day.isoformat(),
        "oura": oura,
        "schedule": schedule,
        "baseline": baseline,
        "goals": goals,
        **day_context(day, now, oura, schedule, baseline["band"]["band"], goals),
    }
