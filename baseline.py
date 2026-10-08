"""Deterministic comparisons: today vs the 7-day baseline, slot choice, food sums.

Pure functions only (no I/O), so they are easy to unit test.
"""

# How far from the 7-day average counts as "above"/"below".
# ("points", n) = absolute difference, ("percent", n) = relative difference.
THRESHOLDS = {
    "readiness_score": ("points", 5),
    "sleep_score": ("points", 5),
    "hrv_ms": ("percent", 10),
    "resting_hr": ("points", 3),
}

# The status that means "worse than usual" for each metric. Lower resting HR is better.
WORSE_STATUS = {"readiness_score": "below", "sleep_score": "below", "hrv_ms": "below", "resting_hr": "above"}

MORNING_ENDS = "12:00"
FOOD_SUM_FIELDS = {
    "protein_g": "est_protein_g",
    "fiber_g": "est_fiber_g",
    "carbs_g": "est_carbs_g",
    "calories": "est_calories",
    "caffeine_mg": "est_caffeine_mg",
    "alcohol_drinks": "alcohol_drinks",
}


def metric_status(value, avg, kind: str, amount: float) -> str:
    if value is None or avg is None:
        return "missing"
    margin = amount if kind == "points" else avg * amount / 100
    if value > avg + margin:
        return "above"
    if value < avg - margin:
        return "below"
    return "normal"


def compare(today: dict, history: list[dict]) -> dict:
    """For each tracked metric: today's value, 7-day average (None values skipped), delta, status."""
    result = {}
    for metric, (kind, amount) in THRESHOLDS.items():
        values = [d[metric] for d in history if d.get(metric) is not None]
        avg = round(sum(values) / len(values), 1) if values else None
        value = today.get(metric)
        result[metric] = {
            "today": value,
            "avg_7d": avg,
            "delta": round(value - avg, 1) if value is not None and avg is not None else None,
            "days_in_avg": len(values),
            "status": metric_status(value, avg, kind, amount),
        }
    return result


def readiness_band(comparison: dict, readiness_floor: int) -> dict:
    """Deterministic training band from the baseline comparison.

    recover:  readiness below the floor, or 2+ metrics worse than their 7-day average
    push:     readiness and HRV normal or above, and no metric worse than average
    maintain: everything in between (including missing readiness or HRV)
    """
    readiness = comparison["readiness_score"]["today"]
    worse = [m for m, status in WORSE_STATUS.items() if comparison[m]["status"] == status]

    if readiness is not None and readiness < readiness_floor:
        return {"band": "recover", "reason": f"readiness {readiness} is below the floor of {readiness_floor}"}
    if len(worse) >= 2:
        return {"band": "recover", "reason": f"{len(worse)} metrics worse than baseline: {', '.join(worse)}"}
    if not worse and all(comparison[m]["status"] in {"normal", "above"} for m in ("readiness_score", "hrv_ms")):
        return {"band": "push", "reason": "readiness and HRV normal or above, nothing worse than baseline"}
    if worse:
        return {"band": "maintain", "reason": f"mixed: {worse[0]} worse than baseline"}
    return {"band": "maintain", "reason": "mixed: readiness or HRV missing"}


def pick_slot(free_slots: list[list[str]], preferred_time: str = "morning") -> list[str] | None:
    """Earliest morning slot if mornings are preferred, else the earliest slot of the day.

    Slots are ["HH:MM", "HH:MM"] pairs in local time, already sorted.
    """
    if not free_slots:
        return None
    if preferred_time == "morning":
        morning = [s for s in free_slots if s[0] < MORNING_ENDS]
        if morning:
            return morning[0]
    return free_slots[0]


def food_totals(entries: list[dict]) -> dict:
    """Sum the estimated nutrition of a list of food_log rows."""
    totals = {"entries": len(entries)}
    for key, column in FOOD_SUM_FIELDS.items():
        totals[key] = round(sum(e.get(column) or 0 for e in entries), 1)
    return totals


def compute_baseline(today: dict, history: list[dict], free_slots: list[list[str]], preferred_time: str,
                     readiness_floor: int, food_today: list[dict], food_yesterday: list[dict]) -> dict:
    comparison = compare(today, history)
    return {
        "comparison": comparison,
        "band": readiness_band(comparison, readiness_floor),
        "recommended_slot": pick_slot(free_slots, preferred_time),
        "food": {"yesterday": food_totals(food_yesterday), "today": food_totals(food_today)},
    }
