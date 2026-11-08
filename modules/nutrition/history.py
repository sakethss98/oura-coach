"""Food over a range of days: totals, per-day averages, the lowest and highest day, patterns by weekday
or week. Pure functions over per-day totals (modules/training/baseline.food_totals of each day's entries).

Days with nothing logged are "not logged": never counted as 0 in an average. Today is still going, so it
is shown "so far" and left out of the lowest/highest day.
"""
from datetime import date, timedelta

NUTRIENTS = ["calories", "protein_g", "carbs_g", "fiber_g", "caffeine_mg", "alcohol_drinks"]
PER_DAY_MAX = 31
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def day_label(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _round(value: float, nutrient: str) -> float:
    return round(value, 1) if nutrient == "alcohol_drinks" else round(value)


def _average(days: list[date], totals: dict, nutrient: str) -> float | None:
    values = [totals[d.isoformat()][nutrient] for d in days]
    return _round(sum(values) / len(values), nutrient) if values else None


def summarize(totals_by_day: dict[str, dict], nutrients: list[str], today: date, per_day: bool = False,
              group_by: str | None = None) -> dict:
    nutrients = [n for n in nutrients if n in NUTRIENTS] or ["calories", "protein_g"]
    days = sorted(date.fromisoformat(k) for k in totals_by_day)
    logged = [d for d in days if totals_by_day[d.isoformat()]["entries"]]
    finished = [d for d in logged if d != today]
    result = {
        "days": len(days),
        "days_logged": len(logged),
        "not_logged": [day_label(d) for d in days if d not in logged and d != today],
        "total": {n: _round(sum(totals_by_day[d.isoformat()][n] for d in logged), n) for n in nutrients},
        "average_per_logged_day": {n: _average(logged, totals_by_day, n) for n in nutrients},
    }
    if today in logged:
        result["today_so_far"] = {n: _round(totals_by_day[today.isoformat()][n], n) for n in nutrients}
    if len(finished) >= 2:
        result["lowest_day"], result["highest_day"] = {}, {}
        for n in nutrients:
            low = min(finished, key=lambda d: totals_by_day[d.isoformat()][n])
            high = max(finished, key=lambda d: totals_by_day[d.isoformat()][n])
            result["lowest_day"][n] = {"day": day_label(low), "value": _round(totals_by_day[low.isoformat()][n], n)}
            result["highest_day"][n] = {"day": day_label(high), "value": _round(totals_by_day[high.isoformat()][n], n)}
        result["lowest_highest_note"] = "Today is left out (still going)." if today in logged else None
    if per_day and len(days) <= PER_DAY_MAX:
        result["per_day"] = [{"day": day_label(d), **({n: _round(totals_by_day[d.isoformat()][n], n) for n in nutrients}
                                                      if d in logged else {"logged": False})}
                             | ({"note": "today, so far"} if d == today else {}) for d in days]
    if group_by == "weekday":
        result["by_weekday"] = {
            name: {"days_logged": len(mine), **{n: _average(mine, totals_by_day, n) for n in nutrients}}
            for name in WEEKDAYS if (mine := [d for d in finished if WEEKDAYS[d.weekday()] == name])}
    elif group_by == "week":
        weeks: dict[date, list[date]] = {}
        for d in logged:
            weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)
        result["by_week"] = [{"week_of": day_label(monday), "days_logged": len(mine),
                              **{n: _average(mine, totals_by_day, n) for n in nutrients}}
                             for monday, mine in sorted(weeks.items())]
    return result
