"""Deterministic comparisons: today vs the 7-day baseline, readiness band, food sums.

Pure functions only (no I/O), so they are easy to unit test.
"""
from labels import PLAIN_METRIC

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


def _plain_list(metrics: list[str]) -> str:
    names = [PLAIN_METRIC[m] for m in metrics]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def readiness_band(comparison: dict, readiness_floor: int) -> dict:
    """Deterministic training band from the baseline comparison.

    recover:  readiness below the floor, or 2+ metrics worse than their 7-day average
    push:     readiness and HRV normal or above, and no metric worse than average
    maintain: everything in between (including missing readiness or HRV)
    `reason` is technical (logs, run.py); `plain` is for chat.
    """
    readiness = comparison["readiness_score"]["today"]
    worse = [m for m, status in WORSE_STATUS.items() if comparison[m]["status"] == status]

    if readiness is not None and readiness < readiness_floor:
        return {"band": "recover", "reason": f"readiness {readiness} is below the floor of {readiness_floor}",
                "plain": f"Your readiness is {readiness}, below your floor of {readiness_floor}."}
    if len(worse) >= 2:
        return {"band": "recover", "reason": f"{len(worse)} metrics worse than baseline: {', '.join(worse)}",
                "plain": f"Your {_plain_list(worse)} are worse than usual."}
    if not worse and all(comparison[m]["status"] in {"normal", "above"} for m in ("readiness_score", "hrv_ms")):
        return {"band": "push", "reason": "readiness and HRV normal or above, nothing worse than baseline",
                "plain": "Your readiness and HRV look good."}
    if worse:
        return {"band": "maintain", "reason": f"mixed: {worse[0]} worse than baseline",
                "plain": f"Your {_plain_list(worse[:1])} is worse than usual."}
    return {"band": "maintain", "reason": "mixed: readiness or HRV missing",
            "plain": "Some of your recovery data is missing."}


BAND_ORDER = ["recover", "maintain", "push"]   # lowest to highest


def effective_band(oura_band: dict, checkin: dict | None, rules: dict) -> dict:
    """The Oura band, lowered (never raised) by the check-in.

    rules = goals.yaml `checkin`: recover_if / down_one_if, each {energy_at_most, soreness_at_least}.
    Either value meeting recover_if -> recover; else either meeting down_one_if -> one level down.
    Values that were not given are ignored.
    """
    checkin = checkin or {}
    energy, soreness = checkin.get("energy"), checkin.get("soreness")

    def hits(rule: dict) -> list[str]:
        found = []
        if energy is not None and energy <= rule["energy_at_most"]:
            found.append(f"energy {energy}")
        if soreness is not None and soreness >= rule["soreness_at_least"]:
            found.append(f"soreness {soreness}")
        return found

    oura = oura_band["band"]
    level = BAND_ORDER.index(oura)
    reasons = hits(rules["recover_if"])
    if reasons:
        level = 0
    else:
        reasons = hits(rules["down_one_if"])
        if reasons:
            level = max(level - 1, 0)
    band = BAND_ORDER[level]
    changed = band != oura
    said = " and ".join(reasons)
    return {
        "band": band,
        "oura_band": oura,
        "changed_by_checkin": changed,
        "reason": f"check-in ({said}) lowers {oura} to {band}" if changed else oura_band["reason"],
        "plain": (f"You said {said}, so " + ("today is a recovery day." if band == "recover"
                                               else "no hard session today.")) if changed else oura_band["plain"],
    }


def food_totals(entries: list[dict]) -> dict:
    """Sum the estimated nutrition of a list of food_log rows."""
    totals = {"entries": len(entries)}
    for key, column in FOOD_SUM_FIELDS.items():
        totals[key] = round(sum(e.get(column) or 0 for e in entries), 1)
    return totals


def compute_baseline(today: dict, history: list[dict], readiness_floor: int) -> dict:
    comparison = compare(today, history)
    return {"comparison": comparison, "band": readiness_band(comparison, readiness_floor)}
