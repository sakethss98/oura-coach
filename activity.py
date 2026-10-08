"""What I actually did on a day: Oura workouts merged with sessions I reported in chat.

Pure functions. `rules` is the `activity` section of goals.yaml:
  hard_oura:   {oura_activity: {min_minutes: N}}   Oura workouts that count as a hard day
  walk_counts: {min_minutes: N, not_on_bands: [...]}   when a walk counts as a light workout

A session and an Oura workout whose times overlap are the same activity and appear once.
Only sessions marked done (or logged as unplanned) count; a plan I never confirmed does not.
"""
from datetime import datetime, timedelta

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4
RUN_TYPES = {"easy_run", "long_run"}
OURA_RUN = "running"
OURA_WALK = "walking"
# A done session with no known length gets this much time before the report, for overlap only.
UNKNOWN_DURATION = timedelta(minutes=60)


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
    when = f" at {item['start']:%H:%M}" if item["start"] else ""
    minutes = f"{item['minutes']} min " if item["minutes"] is not None else ""
    return f"{minutes}{item['activity']}{when}"


def worked_out(items: list[dict], band: str, rules: dict) -> str | None:
    """The reason I already count as having worked out today, or None."""
    counting = [i for i in items if counts_as_workout(i, band, rules)]
    return ", ".join(describe(i) for i in counting) or None


def hard_reason(items: list[dict]) -> str | None:
    """Why the day counts as hard, or None."""
    hard = [i for i in items if i["hard"]]
    return ", ".join(describe(i) for i in hard) or None
