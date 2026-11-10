"""Training over a range of days: what was done each day (Oura + done plan blocks + manual workouts,
merged by modules/training/activity.py), totals, run distance, and patterns by weekday or week.

`activity_by_day` and `plans_by_day` read the database; everything else is pure. Numbers for the
`workouts` tool and the day summaries are computed here; the agent only words them.
"""
from collections import Counter
from datetime import date, timedelta

from core import db
from core.labels import clock
from modules.training import activity
from modules.training.activity import done_blocks
from modules.training.blocks import blocks_of
from modules.training.score import _label, counts

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PER_DAY_MAX = 31          # per-day rows are listed for ranges up to a month
RUN_ACTIVITIES = {"running", "easy_run", "long_run"}


def days_in(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def day_label(d: date) -> str:
    return f"{d:%a %b} {d.day}"


# --- gathering (database) -----------------------------------------------------

def plans_by_day(user, start: date, end: date) -> dict[str, list[dict]]:
    plans = db.plans_between(user.id, start, end)
    return {d.isoformat(): [p for p in plans if p["date"] == d.isoformat()] for d in days_in(start, end)}


def activity_by_day(user, start: date, end: date, metrics_by_day: dict, manual_by_day: dict,
                    rules: dict, plans: dict | None = None) -> dict[str, list[dict]]:
    """Merged activity items per day (the same merge the planner and the score use, over any range)."""
    plans = plans if plans is not None else plans_by_day(user, start, end)
    result = {}
    for d in days_in(start, end):
        key = d.isoformat()
        from_oura = activity.oura_items((metrics_by_day.get(key) or {}).get("workouts") or [], rules)
        mine = (activity.session_items(plans.get(key, []), rules)
                + activity.manual_items(manual_by_day.get(key, []), rules))
        result[key] = activity.merge_activity(from_oura, mine)
    return result


# --- pure ---------------------------------------------------------------------

def is_run(item: dict) -> bool:
    return item["activity"] in RUN_ACTIVITIES or item.get("workout_type") in RUN_ACTIVITIES


def item_row(item: dict) -> dict:
    row = {"workout": item["label"], "minutes": item["minutes"]}
    if item["start"] is not None:
        row["start"] = clock(item["start"])
    if item.get("km") is not None and (is_run(item) or item["is_walk"]):   # Oura gives a distance for any workout
        row["km"] = round(item["km"], 1)
    if item["source"] == "manual":
        row["added_in_chat"] = True
    return row


def plan_status(plan: dict) -> dict:
    """One plan of a day: what it was and whether it happened ("done", "partly done", "not done", "rest")."""
    p = plan["plan_json"] or {}
    if p.get("workout_type") == "rest":
        return {"plan": "rest", "status": "rest"}
    done = len(done_blocks(plan))
    status = "done" if done >= len(blocks_of(p)) else "partly done" if done else "not done"
    return {"plan": _label(plan), "status": status}


def skipped(plans: list[dict]) -> int:
    """Planned workouts (not rests) that nothing was done for."""
    return sum(1 for p in plans if plan_status(p)["status"] == "not done")


def day_training(items: list[dict], plans: list[dict], rules: dict) -> dict:
    """One day's training in numbers (for day summaries and best/worst-day comparisons)."""
    counting = [i for i in items if counts(i, rules)]
    return {"workouts": [item_row(i) for i in counting], "minutes": sum(i["minutes"] or 0 for i in counting),
            "hard": any(i["hard"] for i in counting), "plans": [plan_status(p) for p in plans]}


def summarize(items_by_day: dict[str, list[dict]], plans: dict[str, list[dict]], rules: dict, today: date,
              group_by: str | None = None) -> dict:
    """Totals over the range, the longest run, per-day rows (up to a month) and, with group_by, patterns.
    Today is still going, so it never counts as a day without a workout."""
    days = sorted(date.fromisoformat(k) for k in items_by_day)
    by_day = {d: [i for i in items_by_day[d.isoformat()] if counts(i, rules)] for d in days}
    counting = [i for d in days for i in by_day[d]]
    runs = [(d, i) for d in days for i in by_day[d] if is_run(i)]
    longest = max(runs, key=lambda r: (r[1].get("km") or 0, r[1]["minutes"] or 0), default=None)
    worked = [d for d in days if by_day[d]]
    without = [d for d in days if not by_day[d] and d != today]
    result = {
        "days": len(days),
        "days_with_a_workout": len(worked),
        "days_without": len(without),
        "workouts_by_type": dict(Counter(i["label"] for i in counting)),
        "total_minutes": sum(i["minutes"] or 0 for i in counting),
        "hard_days": sum(1 for d in days if any(i["hard"] for i in by_day[d])),
        "runs": len(runs),
        "run_km": round(sum(i["km"] for _, i in runs if i.get("km") is not None), 1),
        "longest_run": ({"day": day_label(longest[0]), "minutes": longest[1]["minutes"],
                         **({"km": round(longest[1]["km"], 1)} if longest[1].get("km") else {})}
                        if longest else None),
        "planned_but_not_done": sum(skipped(plans.get(d.isoformat(), [])) for d in days if d != today),
    }
    if len(days) <= PER_DAY_MAX:
        result["per_day"] = [{"day": day_label(d), "workouts": [item_row(i) for i in by_day[d]]}
                             | ({"status": "today, still going"} if d == today else {}) for d in days]
    if group_by == "weekday":
        result["by_weekday"] = by_weekday(days, by_day, plans, today)
    elif group_by == "week":
        result["by_week"] = by_week(days, by_day)
    return result


def by_weekday(days: list[date], by_day: dict, plans: dict, today: date) -> dict:
    """Per weekday: how many of them had a workout, had none, or had a plan that wasn't done; and the
    weekdays most often without a workout (ties all listed; only weekdays that came up at least twice)."""
    rows = {}
    for name in WEEKDAYS:
        mine = [d for d in days if WEEKDAYS[d.weekday()] == name and d != today]
        if not mine:
            continue
        rows[name] = {"days": len(mine), "with_a_workout": sum(1 for d in mine if by_day[d]),
                      "without": sum(1 for d in mine if not by_day[d]),
                      "planned_but_not_done": sum(skipped(plans.get(d.isoformat(), [])) for d in mine)}
    shares = {name: r["without"] / r["days"] for name, r in rows.items() if r["days"] >= 2 and r["without"]}
    top = max(shares.values(), default=None)
    return {"weekdays": rows,
            "most_often_without_a_workout": [name for name, share in shares.items() if share == top]}


def by_week(days: list[date], by_day: dict) -> list[dict]:
    weeks: dict[date, list[date]] = {}
    for d in days:
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)
    rows = []
    for monday, mine in sorted(weeks.items()):
        items = [i for d in mine for i in by_day[d]]
        rows.append({"week_of": day_label(monday), "days_covered": len(mine), "workouts": len(items),
                     "minutes": sum(i["minutes"] or 0 for i in items),
                     "run_km": round(sum(i["km"] for i in items if is_run(i) and i.get("km") is not None), 1),
                     "hard_days": sum(1 for d in mine if any(i["hard"] for i in by_day[d]))})
    return rows
