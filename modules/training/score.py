"""The training share of the daily score: did today's plan happen? Pure function, plain-words note.

Per plan:
- a workout plan: credited minutes = the sum over its blocks of min(done minutes, planned minutes), so a
  long run can't make up for a skipped strength block. >= 70% of the planned total = 1.0, anything = 0.5
  ("partly done"); nothing done but another workout after the plan was made = 0.5; else 0.
- a rest plan: 1.0, or 0 if a hard workout started after the rest plan was made. Only a rest the coach
  planned counts: the rest code gives when it's late or there's no free time left is not a rest day.
The day = the best of its plans, or 1.0 if a counting workout happened before the first plan was made
(e.g. HIIT in the morning, a walk plan in the evening). No plans: 1.0 for any counting workout, else 0.
A walk counts at 20+ min (goals.yaml activity.walk_counts); every other workout counts.
Abandoned plans never reach this function (db.plans_between leaves them out).
Before the day's cutoff an open plan is "still to do", and `next` says what would still help today.
"""
from datetime import datetime

from core.labels import workout_label
from modules.base import ScoreComponent
from modules.training.activity import describe, done_blocks
from modules.training.blocks import blocks_of

FULL_SHARE = 0.7      # done minutes / planned minutes that count as done as planned


def _counts(item: dict, rules: dict) -> bool:
    return not item["is_walk"] or (item["minutes"] or 0) >= rules["walk_counts"]["min_minutes"]


def _made_at(plan: dict) -> datetime:
    return datetime.fromisoformat(plan["checkin_at"])


def _label(plan: dict) -> str:
    """"easy run + strength (push)"; "HIIT" keeps its capitals."""
    p = plan["plan_json"] or {}
    labels = [workout_label(b["workout_type"], p.get("split_day")) for b in blocks_of(p)]
    return " + ".join(label if label.isupper() else label.lower() for label in labels)


def _no_time_rest(plan: dict) -> bool:
    """Rest because it was late or there was no free time left (code), not a planned rest day."""
    return ((plan["plan_json"] or {}).get("workout_type") == "rest"
            and (plan.get("details_json") or {}).get("plan_source") == "code")


def score_plan(plan: dict, items: list[dict], rules: dict, day_over: bool = True) -> tuple[float, str]:
    """(fraction, note) for one plan of the day; `items` = the day's merged activity."""
    p = plan["plan_json"] or {}
    made_at = _made_at(plan)
    after = [i for i in items if i["start"] is not None and i["start"] >= made_at and i.get("session_id") != plan["id"]]
    if p.get("workout_type") == "rest":
        hard = [i for i in after if i["hard"]]
        return (0.0, f"hard workout on a rest day ({describe(hard[0])})") if hard else (1.0, "rest day kept")

    planned = blocks_of(p)
    credited = sum(min(d["minutes"] or 0, block["duration_min"] or 0) for _, block, d in done_blocks(plan))
    total = sum(b["duration_min"] or 0 for b in planned)
    if total and credited >= FULL_SHARE * total:
        return 1.0, f"{_label(plan)} done"
    if credited:
        return 0.5, f"{_label(plan)} partly done ({credited} of {total} min)"
    other = [i for i in after if _counts(i, rules)]
    if other:
        return 0.5, f"{describe(other[0])} instead of the planned {_label(plan)}"
    return 0.0, f"{_label(plan)} {'not done' if day_over else 'still to do'}"


WALK_HINT = "a 20-minute walk (it counts as a workout)"


def score_day(plans: list[dict], items: list[dict], rules: dict, day_over: bool = True) -> ScoreComponent:
    """`plans`: the day's plans (status planned, with blocks_done); `items`: the day's merged activity
    (activity.merge_activity); `rules`: goals.yaml `activity`; `day_over`: the cutoff has passed."""
    plans = [p for p in plans if not _no_time_rest(p)]
    counting = [i for i in items if _counts(i, rules)]
    if not plans:
        if counting:
            return ScoreComponent("training", 1.0, describe(counting[0]), {"workouts": len(counting)})
        return ScoreComponent("training", 0.0, "no workout yet" if not day_over else "no workout", {},
                              None if day_over else WALK_HINT)

    per_plan = [(plan["id"], *score_plan(plan, items, rules, day_over)) for plan in plans]
    _, fraction, note = max(per_plan, key=lambda x: x[1])
    first_made = min(_made_at(p) for p in plans)
    before = [i for i in counting if i["start"] is not None and i["start"] < first_made and "session_id" not in i]
    if before and fraction < 1.0:
        fraction, note = 1.0, f"{describe(before[0])} done"
    hint = None
    if fraction < 1.0 and not day_over:
        latest = plans[-1]
        is_rest = (latest["plan_json"] or {}).get("workout_type") == "rest"
        hint = None if is_rest else f"your planned {_label(latest)}"
    return ScoreComponent("training", fraction, note,
                          {"plans": [{"id": i, "fraction": f, "note": n} for i, f, n in per_plan]}, hint)
