"""What I actually did on a day: Oura workouts merged with sessions I reported in chat.

Pure functions. `rules` is the `activity` section of goals.yaml:
  hard_oura:   {oura_activity: {min_minutes: N}}   Oura workouts that count as a hard day
  walk_counts: {min_minutes: N, not_on_bands: [...]}   when a walk counts as a light workout

A session and an Oura workout whose times overlap are the same activity and appear once.
Only sessions marked done (or logged as unplanned) count; a plan I never confirmed does not,
unless Oura recorded a matching workout around the planned time (`match_open_plans`).
"""
from datetime import date, datetime, time, timedelta

from config import TZ
from labels import clock, oura_label, workout_label

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4
RUN_TYPES = {"easy_run", "long_run"}
OURA_RUN = "running"
OURA_WALK = "walking"
# A done session with no known length gets this much time before the report, for overlap only.
UNKNOWN_DURATION = timedelta(minutes=60)
# An Oura workout completes an open plan if it overlaps the planned slot widened by this much.
OURA_MATCH_SLACK = timedelta(minutes=60)
# Which Oura activities can complete a planned type (None = any activity).
OURA_MATCHES = {"easy_run": {"running"}, "long_run": {"running"}, "walk": {"walking"},
                "strength": {"strengthTraining"}, "HIIT": None, "mobility": None}


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def oura_items(workouts: list[dict], rules: dict) -> list[dict]:
    """Oura workouts (from oura_client.daily_metrics) as activity items."""
    items = []
    for w in workouts:
        hard_rule = rules.get("hard_oura", {}).get(w["activity"])
        items.append({
            "source": "oura",
            "activity": w["activity"],
            "label": oura_label(w["activity"]),
            "workout_type": None,
            "start": _dt(w["start"]),
            "end": _dt(w["end"]),
            "minutes": w["minutes"],
            "is_walk": w["activity"] == OURA_WALK,
            "hard": bool(hard_rule) and w["minutes"] >= hard_rule["min_minutes"],
            "feeling": None,
        })
    return items


def session_items(sessions: list[dict], rules: dict) -> list[dict]:
    """Done sessions (planned and marked done, or unplanned) as activity items."""
    run_rule = rules.get("hard_oura", {}).get(OURA_RUN)
    items = []
    for s in sessions:
        if s["completed"] != 1:
            continue
        plan = s["plan_json"] or {}
        workout_type = plan.get("workout_type", "other")
        start, end = _dt(s["actual_start"]), _dt(s["actual_end"])
        minutes = round((end - start).total_seconds() / 60) if start and end else plan.get("duration_min")
        long_enough_run = (workout_type in RUN_TYPES and run_rule is not None and minutes is not None
                           and minutes >= run_rule["min_minutes"])
        items.append({
            "source": "session",
            "session_id": s["id"],
            "activity": workout_type,
            "label": workout_label(workout_type, plan.get("split_day")),
            "workout_type": workout_type,
            "start": start,
            "end": end,
            "minutes": minutes,
            "is_walk": workout_type == "walk",
            "hard": (workout_type in HARD_TYPES or (plan.get("intensity") or 0) >= HARD_INTENSITY
                     or long_enough_run),
            "feeling": s["feeling"],
        })
    return items


def _overlaps(a: dict, b: dict) -> bool:
    if None in (a["start"], a["end"], b["start"], b["end"]):
        return False
    return a["start"] < b["end"] and b["start"] < a["end"]


def merge_activity(oura: list[dict], sessions: list[dict]) -> list[dict]:
    """One list per day: each session absorbs the Oura workouts it overlaps, the rest are kept."""
    remaining = list(oura)
    merged = []
    for s in sessions:
        matches = [o for o in remaining if _overlaps(s, o)]
        if not matches:
            merged.append(s)
            continue
        remaining = [o for o in remaining if o not in matches]
        merged.append({
            **s,
            "source": "both",
            "activity": f"{s['workout_type']} (Oura: {', '.join(o['activity'] for o in matches)})",
            "label": s["label"],
            "start": min(o["start"] for o in matches),
            "end": max(o["end"] for o in matches),
            "minutes": sum(o["minutes"] for o in matches),
            "hard": s["hard"] or any(o["hard"] for o in matches),
        })
    merged.extend(remaining)
    return sorted(merged, key=lambda i: (i["start"] is None, i["start"] or 0))


def counts_as_workout(item: dict, band: str, rules: dict) -> bool:
    """Non-walks always count. A walk counts if long enough and today's band allows it."""
    if not item["is_walk"]:
        return True
    walk = rules["walk_counts"]
    excluded_bands = {b.lower() for b in walk.get("not_on_bands", [])}
    return (item["minutes"] or 0) >= walk["min_minutes"] and band.lower() not in excluded_bands


def describe(item: dict) -> str:
    """Plain words, e.g. "35-min run at 7:00am"."""
    label = item["label"] if item["label"].isupper() else item["label"].lower()
    when = f" at {clock(item['start'])}" if item["start"] else ""
    minutes = f"{item['minutes']}-min " if item["minutes"] is not None else ""
    return f"{minutes}{label}{when}"


def worked_out(items: list[dict], band: str, rules: dict) -> str | None:
    """The reason I already count as having worked out today, or None."""
    counting = [i for i in items if counts_as_workout(i, band, rules)]
    return ", ".join(describe(i) for i in counting) or None


def hard_reason(items: list[dict]) -> str | None:
    """Why the day counts as hard, or None."""
    hard = [i for i in items if i["hard"]]
    return ", ".join(describe(i) for i in hard) or None


def match_open_plans(open_sessions: list[dict], oura: list[dict]) -> list[tuple[dict, dict]]:
    """(session, Oura item) pairs where Oura recorded a planned-but-unconfirmed workout.

    Match = a compatible Oura activity overlapping the planned slot widened by 60 min each side.
    Each Oura workout completes at most one plan.
    """
    pairs, used = [], set()
    for s in open_sessions:
        plan = s["plan_json"] or {}
        slot = plan.get("time_slot") or "none"
        if s["completed"] == 1 or plan.get("workout_type") not in OURA_MATCHES or slot == "none":
            continue
        day = date.fromisoformat(s["date"])
        start, end = (datetime.combine(day, time.fromisoformat(t), TZ) for t in slot.split("-"))
        allowed = OURA_MATCHES[plan["workout_type"]]
        for i, o in enumerate(oura):
            if i in used or (allowed is not None and o["activity"] not in allowed):
                continue
            if o["start"] < end + OURA_MATCH_SLACK and start - OURA_MATCH_SLACK < o["end"]:
                pairs.append((s, o))
                used.add(i)
                break
    return pairs


def next_split_day(sessions: list[dict], split: list[str]) -> str:
    """The split day after the last done strength session that recorded one (oldest-first list)."""
    for s in reversed(sessions):
        plan = s["plan_json"] or {}
        if s["completed"] == 1 and plan.get("workout_type") == "strength" and plan.get("split_day") in split:
            return split[(split.index(plan["split_day"]) + 1) % len(split)]
    return split[0]
