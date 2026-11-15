"""The goal engine's only door to device data: the neutral day (connectors/base.NEUTRAL_DAY_KEYS), never a device's
own names. A person with no wearable has only the workouts they add in chat, and everything still works. Pure.

signals_by_day = {YYYY-MM-DD: {"readiness", "sleep_hours", "hrv_ms", "resting_hr", "steps",
                               "workouts": [{"kind", "start", "end", "minutes", "distance_km", "source"}]}}
"""
from datetime import date, timedelta

from programs.schema import Template

LOOKBACK_DAYS = 30
WEEKS_BEFORE = 3


def _amount(workout: dict, measure: str) -> float | None:
    if measure == "km":
        return workout.get("distance_km")
    if measure == "min":
        return workout.get("minutes")
    return None                       # elevation: no device gives it yet


def workouts(signals_by_day: dict, kinds: list[str], start: date, end: date) -> list[dict]:
    """Their workouts of these kinds from start to end (inclusive), each with its "day"."""
    found = []
    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        for w in (signals_by_day.get(day.isoformat()) or {}).get("workouts", []):
            if w["kind"] in kinds:
                found.append({**w, "day": day})
    return found


def done(day_signal: dict | None, kinds: list[str], measure: str) -> float:
    """How much of a discipline they did that day, in its measure (km, minutes)."""
    return sum(_amount(w, measure) or 0 for w in (day_signal or {}).get("workouts", []) if w["kind"] in kinds)


def history(signals_by_day: dict, start: date, t: Template) -> dict[str, dict]:
    """Per discipline, what they did before `start`: {"weekly": the 3 weeks' loads (oldest first),
    "longest_30d": the longest session in the 30 days before}. A discipline with nothing recorded is left out."""
    out = {}
    for name, d in t.disciplines.items():
        recent = [w for w in workouts(signals_by_day, d.kinds, start - timedelta(days=LOOKBACK_DAYS),
                                      start - timedelta(days=1)) if _amount(w, d.measure)]
        if not recent:
            continue
        weekly = []
        for k in range(WEEKS_BEFORE, 0, -1):
            first, last = start - timedelta(days=7 * k), start - timedelta(days=7 * (k - 1) + 1)
            weekly.append(sum(_amount(w, d.measure) for w in recent if first <= w["day"] <= last))
        out[name] = {"weekly": weekly, "longest_30d": max(_amount(w, d.measure) for w in recent)}
    return out


def runs(signals_by_day: dict, kinds: list[str], start: date, end: date) -> list[dict]:
    """[{"minutes", "distance_km", "day"}] for paces and finish predictions."""
    return [{"minutes": w["minutes"], "distance_km": w.get("distance_km"), "day": w["day"]}
            for w in workouts(signals_by_day, kinds, start, end)]


def facts(t: Template, history_: dict, days_per_week: int) -> dict[str, float]:
    """What a variant's `choose_when` reads: their longest recent session per measure and their days a week."""
    out = {"days_per_week": days_per_week}
    for name, h in history_.items():
        key = f"long_session_{t.disciplines[name].measure}"
        out[key] = max(out.get(key, 0), h["longest_30d"] or 0)
    return out
