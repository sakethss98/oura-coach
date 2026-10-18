"""Everything known about one user's day, at a given moment: Oura, calendar, food, activity, targets.

Shared by the planner graph and chat, so they all see the same numbers. Device data comes from
the connector layer (connectors.health, cached 10 min); the database is read fresh.
"Today" is core.day.day_of(user, now): Oura's main sleep decides where a day starts.

The one write: `sync_oura_completions` marks a plan done when Oura recorded it (idempotent).
(Legacy view used by the M3 chat flow; Phase 2 splits it into module contexts.)
"""
from datetime import date, datetime, time, timedelta

from connectors.calendar_feed import fetch_calendar, fixture_calendar, plan_window, schedule_for
from connectors.health import Health
from connectors.oura import latest_day, load_fixtures
from core import db, memory
from core.config import DAY_END_HOUR, DAY_START_HOUR, TZ
from core.day import day_bounds, day_of
from modules.nutrition.targets import daily_targets, recent_food, remaining
from modules.training import activity
from modules.training.baseline import compute_baseline, effective_band, food_totals

DAILY_ACTIVITY_KEYS = ["activity_score", "steps", "active_calories", "high_activity_min", "medium_activity_min"]


def fixtures_now(at: time | None = None, tz=TZ) -> datetime:
    """Fixtures mode: "today" is the newest fixture day; the clock time is now (or `at`)."""
    clock = at or datetime.now(tz).time().replace(second=0, microsecond=0)
    return datetime.combine(latest_day(load_fixtures()), clock, tz)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


# --- remote data (connectors) -----------------------------------------------

def fetch_oura(user, day: date, use_fixtures: bool, now: datetime | None = None, allow_stale: bool = False) -> dict:
    """Oura metrics for today + the 7 days before, body data, Oura and manual workouts by day."""
    return Health(use_fixtures).bundle(user, day, now=now, allow_stale=allow_stale)


def fetch_schedule(user, day: date, use_fixtures: bool) -> dict:
    calendar, source = fixture_calendar() if use_fixtures else (fetch_calendar(), "live")
    sched = schedule_for(calendar, day)
    return {
        "busy": [[_hm(s), _hm(e), title] for s, e, title in sched["busy"]],
        "all_day": sched["all_day"],
        "free": [[_hm(s), _hm(e)] for s, e in sched["free"]],
        "source": source,
    }


# --- local data (database) + deterministic math ------------------------------

def _at(day: date, hhmm: str, tz) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tz)


def window_now(schedule: dict, day: date, now: datetime) -> list[str] | None:
    tz = now.tzinfo
    busy = [(_at(day, s, tz), _at(day, e, tz)) for s, e, _ in schedule["busy"]]
    window = plan_window(busy, now, _at(day, f"{DAY_START_HOUR:02d}:00", tz), _at(day, f"{DAY_END_HOUR:02d}:00", tz))
    return [_hm(window[0]), _hm(window[1])] if window else None


def _public(item: dict) -> dict:
    """An activity item with times as HH:MM strings (for state, prompts and replies)."""
    return {
        "source": item["source"], "activity": item["activity"], "label": item["label"], "minutes": item["minutes"],
        "start": _hm(item["start"]) if item["start"] else None,
        "end": _hm(item["end"]) if item["end"] else None,
        "hard": item["hard"], "feeling": item["feeling"],
    }


def day_activity(user, day: date, oura: dict, rules: dict) -> dict[str, list[dict]]:
    """Merged activity items (Oura + done plans + manual workouts) for today and the 6 days before."""
    plans = db.plans_between(user.id, day - timedelta(days=6), day)
    result = {}
    for i in range(7):
        key = (day - timedelta(days=i)).isoformat()
        from_oura = activity.oura_items(oura["workouts_by_day"].get(key, []), rules)
        mine = (activity.session_items([p for p in plans if p["date"] == key], rules)
                + activity.manual_items(oura.get("manual_by_day", {}).get(key, []), rules))
        result[key] = activity.merge_activity(from_oura, mine)
    return result


def sync_oura_completions(user, day: date, oura_workouts_by_day: dict, rules: dict) -> list[tuple[int, int]]:
    """Mark blocks of today's open plans done when Oura recorded a matching workout around the
    block's planned time, with Oura's times. Returns the (plan id, block index) pairs it marked."""
    from_oura = activity.oura_items(oura_workouts_by_day.get(day.isoformat(), []), rules)
    done = []
    for plan, index, workout in activity.match_open_plans(db.open_plans(user.id, day), from_oura):
        db.mark_block_done(plan["id"], index, workout["start"], workout["end"], workout["minutes"], "oura",
                           workout["end"])
        done.append((plan["id"], index))
    return done


def food_on(user, day: date) -> list[dict]:
    """Food eaten on `day` (Oura-defined day bounds)."""
    return db.food_between(user.id, *day_bounds(user, day))


def day_context(user, day: date, now: datetime, oura: dict, schedule: dict, band: str, goals: dict) -> dict:
    """`band` is today's effective band (after the check-in); the walk rule uses it."""
    rules = goals["activity"]
    sync_oura_completions(user, day, oura["workouts_by_day"], rules)
    items = day_activity(user, day, oura, rules)
    today_items = items[day.isoformat()]
    yesterday_items = items[(day - timedelta(days=1)).isoformat()]
    previous_days = [items[(day - timedelta(days=i)).isoformat()] for i in range(1, 7)]

    food_today = food_on(user, day)
    totals = {"yesterday": food_totals(food_on(user, day - timedelta(days=1))), "today": food_totals(food_today)}
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
            {"id": e["id"], "time": e["timestamp"][11:16], "text": e["raw_text"], "calories": e["est_calories"],
             "protein_g": e["est_protein_g"]}
            for e in food_today
        ],
        "food_totals": totals,
        "targets": targets,
        "remaining": remaining(targets["targets"], totals["today"]),
        "recent_food": recent_food(food_today, now),
        "saved_memory": memory.for_prompt(user, day),
        "first_plan_today": db.count_planned(user.id, day) == 0,
        "next_split_day": activity.next_split_day(db.done_strength_plans(user.id), goals["strength"]["split"]),
        "plans_today": db.plans_between(user.id, day, day),
        "manual_today": oura.get("manual_by_day", {}).get(day.isoformat(), []),
    }


def snapshot(user, now: datetime, use_fixtures: bool, allow_stale: bool = False) -> dict:
    """View of the user's day at `now` (for questions). `allow_stale` reuses any cached Oura data
    instead of fetching (for food questions, which only need body data for the targets)."""
    goals = user.goals()
    # Fetch first: the fetch fills the Oura cache that day_of reads to find where the day starts.
    oura = fetch_oura(user, now.date(), use_fixtures, now, allow_stale)
    day = day_of(user, now)
    if day != now.date():
        oura = fetch_oura(user, day, use_fixtures, now, allow_stale)
    schedule = fetch_schedule(user, day, use_fixtures)
    baseline = compute_baseline(oura["today"], oura["history"], goals["schedule"]["readiness_floor"])
    latest = db.latest_checkin(user.id, day)
    band = effective_band(baseline["band"], latest, goals["checkin"])
    return {
        "day": day.isoformat(),
        "oura": oura,
        "schedule": schedule,
        "baseline": baseline,
        "band": band,
        "goals": goals,
        **day_context(user, day, now, oura, schedule, band["band"], goals),
    }
