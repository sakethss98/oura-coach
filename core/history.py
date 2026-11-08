"""Device data over a range of days (readiness, sleep, HRV, resting HR, steps): per-day values, averages,
patterns by weekday or week, and the best and worst day with what was different about it. Pure functions.

"What was different" compares the things that usually explain a night with the range's usual: that night's
bedtime, and the day before it, the training (minutes, hard or not), the food (kcal, caffeine, alcohol) and
the steps. Each difference is a plain fact built here; the agent only words it. Oura's `day` of a sleep or
readiness record is the morning you wake up, so "the day before" is the day that led into that night.
"""
from datetime import date, datetime, timedelta

from core.labels import clock
from core.score import evening_minutes, usual_bedtime

# tool name -> field of connectors.oura.daily_metrics
METRICS = {"readiness": "readiness_score", "sleep_score": "sleep_score", "sleep_hours": "sleep_hours",
           "hrv": "hrv_ms", "resting_hr": "resting_hr", "steps": "steps"}
LOWER_IS_BETTER = {"resting_hr"}
NIGHT_METRICS = {"readiness", "sleep_score", "sleep_hours", "hrv", "resting_hr"}
# A feature differs from the usual when it is at least this far from the range's average.
NOTABLE = {"bedtime_min": 30, "training_min": 20, "calories": 300, "caffeine_mg": 100, "alcohol_drinks": 1,
           "steps": 2000}
PER_DAY_MAX = 31
MIN_DAYS_FOR_BEST = 3
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def day_label(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _value(metrics_by_day: dict, d: date, metric: str):
    return (metrics_by_day.get(d.isoformat()) or {}).get(METRICS[metric])


def _avg(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _span(minutes: int) -> str:
    """45 -> "45 min"; 206 -> "about 3.5 hours"."""
    return f"{minutes} min" if minutes < 90 else f"about {round(minutes / 30) / 2:g} hours"


def features(d: date, metrics_by_day: dict, training: dict | None, food: dict | None) -> dict:
    """What led into the night that ends on the morning of `d` (training and food: their day summaries)."""
    before = (d - timedelta(days=1)).isoformat()
    bedtime = (metrics_by_day.get(d.isoformat()) or {}).get("bedtime_start")
    t = (training or {}).get(before)
    f = (food or {}).get(before)
    logged = bool(f and f.get("entries"))
    return {
        "bedtime": datetime.fromisoformat(bedtime) if bedtime else None,
        "training_min": t["minutes"] if t else None,
        "hard": t["hard"] if t else None,
        "calories": f["calories"] if logged else None,
        "caffeine_mg": f["caffeine_mg"] if logged else None,
        "alcohol_drinks": f["alcohol_drinks"] if logged else None,
        "steps": (metrics_by_day.get(before) or {}).get("steps"),
    }


def usual(all_features: list[dict]) -> dict:
    """The range's usual for each feature (bedtime as a clock time; hard = the share of hard days)."""
    hard = [f["hard"] for f in all_features if f["hard"] is not None]
    return {
        "bedtime": usual_bedtime([f["bedtime"] for f in all_features if f["bedtime"]]),
        "hard_share": sum(hard) / len(hard) if hard else None,
        **{k: _avg([f[k] for f in all_features]) for k in ("training_min", "calories", "caffeine_mg",
                                                            "alcohol_drinks", "steps")},
    }


def differences(f: dict, typical: dict) -> list[str]:
    """Plain facts about how this day's lead-in differed from the usual (empty when nothing stood out)."""
    found = []
    if f["bedtime"] and typical["bedtime"]:
        off = evening_minutes(f["bedtime"]) - evening_minutes(typical["bedtime"])
        if abs(off) >= NOTABLE["bedtime_min"]:
            found.append(f"went to bed at {clock(f['bedtime'])}, {_span(abs(off))} {'earlier' if off < 0 else 'later'} "
                         f"than usual ({clock(typical['bedtime'])})")
    if f["training_min"] is not None and typical["training_min"] is not None:
        if f["hard"] and (typical["hard_share"] or 0) < 0.5:
            found.append(f"a hard workout the day before ({f['training_min']} min)")
        elif f["training_min"] == 0 and typical["training_min"] >= NOTABLE["training_min"]:
            found.append(f"no workout the day before (usually about {round(typical['training_min'])} min)")
        elif abs(f["training_min"] - typical["training_min"]) >= NOTABLE["training_min"]:
            found.append(f"{f['training_min']} min of training the day before "
                         f"(usually about {round(typical['training_min'])} min)")
    for key, unit in (("calories", "kcal"), ("caffeine_mg", "mg caffeine"), ("alcohol_drinks", "drinks")):
        if f[key] is not None and typical[key] is not None and abs(f[key] - typical[key]) >= NOTABLE[key]:
            found.append(f"about {round(f[key])} {unit} the day before (usually about {round(typical[key])})")
    if f["steps"] is not None and typical["steps"] is not None and abs(f["steps"] - typical["steps"]) >= NOTABLE["steps"]:
        found.append(f"{f['steps']:,} steps the day before (usually about {round(typical['steps']):,})")
    return found


def summarize(metrics_by_day: dict, start: date, end: date, metrics: list[str], group_by: str | None = None,
              features_by_day: dict | None = None) -> dict:
    """Averages, per-day rows (up to a month), best and worst day per metric (with what differed when
    `features_by_day` is given, for night metrics) and, with group_by, averages per weekday or week."""
    metrics = [m for m in metrics if m in METRICS] or ["readiness", "sleep_score", "sleep_hours"]
    days = _days(start, end)
    with_data = [d for d in days if any(_value(metrics_by_day, d, m) is not None for m in metrics)]
    result = {
        "days": len(days),
        "days_with_data": len(with_data),
        "average": {m: _avg([_value(metrics_by_day, d, m) for d in days]) for m in metrics},
    }
    if len(days) <= PER_DAY_MAX:
        result["per_day"] = [{"day": day_label(d), **{m: _value(metrics_by_day, d, m) for m in metrics}}
                             for d in days]
    typical = usual([features_by_day[d.isoformat()] for d in days]) if features_by_day else None
    best_worst = {}
    for m in metrics:
        scored = [d for d in days if _value(metrics_by_day, d, m) is not None]
        if len(scored) < MIN_DAYS_FOR_BEST:
            continue
        pick = min if m in LOWER_IS_BETTER else max
        other = max if m in LOWER_IS_BETTER else min
        best_worst[m] = {}
        for name, chooser in (("best", pick), ("worst", other)):
            d = chooser(scored, key=lambda x: _value(metrics_by_day, x, m))
            row = {"day": day_label(d), "value": _value(metrics_by_day, d, m)}
            if typical and m in NIGHT_METRICS:
                row["what_was_different"] = (differences(features_by_day[d.isoformat()], typical)
                                             or ["nothing stood out from the usual"])
            best_worst[m][name] = row
    if best_worst:
        result["best_and_worst"] = best_worst
    if group_by == "weekday":
        result["by_weekday"] = {name: {m: _avg([_value(metrics_by_day, d, m) for d in mine]) for m in metrics}
                                for name in WEEKDAYS
                                if (mine := [d for d in days if WEEKDAYS[d.weekday()] == name])}
    elif group_by == "week":
        weeks: dict[date, list[date]] = {}
        for d in days:
            weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)
        result["by_week"] = [{"week_of": day_label(monday), **{m: _avg([_value(metrics_by_day, d, m) for d in mine])
                                                               for m in metrics}}
                             for monday, mine in sorted(weeks.items())]
    return result
