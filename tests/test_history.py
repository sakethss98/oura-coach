"""History over a range (M4 Part A1): pure summaries for food, training and device data."""
from datetime import date, datetime, timedelta

from core import history as health_history
from core.config import TZ
from modules.nutrition import history as food_history
from modules.training import history as training_history

MONDAY = date(2026, 10, 5)
RULES = {"hard_oura": {"running": {"min_minutes": 60}}, "walk_counts": {"min_minutes": 20, "not_on_bands": ["PUSH"]}}


def totals(protein=None, calories=600):
    """One day's food totals (baseline.food_totals shape); protein None = nothing logged."""
    if protein is None:
        return {"entries": 0, "protein_g": 0, "fiber_g": 0, "carbs_g": 0, "calories": 0, "caffeine_mg": 0,
                "alcohol_drinks": 0}
    return {"entries": 2, "protein_g": protein, "fiber_g": 10, "carbs_g": 200, "calories": calories, "caffeine_mg": 90,
            "alcohol_drinks": 0}


def test_food_averages_skip_unlogged_days_and_today_is_not_the_lowest():
    days = {(MONDAY + timedelta(days=i)).isoformat(): t
            for i, t in enumerate([totals(90), totals(None), totals(60), totals(10)])}
    today = MONDAY + timedelta(days=3)
    result = food_history.summarize(days, ["protein_g"], today, per_day=True)
    assert result["total"]["protein_g"] == 160 and result["days_logged"] == 3
    assert result["average_per_logged_day"]["protein_g"] == 53                  # 160 / 3, never / 4
    assert result["not_logged"] == ["Tue Oct 6"]
    assert result["lowest_day"]["protein_g"] == {"day": "Wed Oct 7", "value": 60}  # today's 10 g is left out
    assert result["today_so_far"]["protein_g"] == 10
    assert result["per_day"][1] == {"day": "Tue Oct 6", "logged": False}


def test_food_by_week_and_weekday():
    days = {(MONDAY + timedelta(days=i)).isoformat(): totals(50 + i) for i in range(14)}
    result = food_history.summarize(days, ["protein_g"], MONDAY + timedelta(days=20), group_by="week")
    assert [w["week_of"] for w in result["by_week"]] == ["Mon Oct 5", "Mon Oct 12"]
    assert [w["protein_g"] for w in result["by_week"]] == [53, 60]
    weekly = food_history.summarize(days, ["protein_g"], MONDAY + timedelta(days=20), group_by="weekday")
    assert weekly["by_weekday"]["Monday"] == {"days_logged": 2, "protein_g": 54}   # (50 + 57) / 2, rounded


def item(day: date, label="Strength", minutes=40, hour=7, km=None, activity="strength", hard=False, walk=False):
    start = datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour)
    return {"source": "manual", "activity": activity, "label": label, "workout_type": activity, "start": start,
            "end": start + timedelta(minutes=minutes), "minutes": minutes, "km": km, "is_walk": walk, "hard": hard,
            "feeling": None}


def test_training_totals_longest_run_and_weekday_patterns():
    days = [MONDAY + timedelta(days=i) for i in range(14)]
    items = {d.isoformat(): [] for d in days}
    for d in days:
        if d.weekday() not in (0, 4):                                             # never Mondays or Fridays
            items[d.isoformat()].append(item(d))
    items[days[5].isoformat()].append(item(days[5], "Run", 70, km=11.2, activity="running", hard=True))
    items[days[6].isoformat()].append(item(days[6], "Walk", 15, activity="walking", walk=True))   # too short to count
    result = training_history.summarize(items, {}, RULES, today=days[-1], group_by="weekday")
    assert result["longest_run"] == {"day": "Sat Oct 10", "minutes": 70, "km": 11.2}
    assert result["workouts_by_type"] == {"Strength": 10, "Run": 1} and result["hard_days"] == 1
    assert result["by_weekday"]["most_often_without_a_workout"] == ["Monday", "Friday"]   # ties are all listed
    assert result["days_without"] == 4


def test_training_today_never_counts_as_a_day_without():
    items = {MONDAY.isoformat(): []}
    result = training_history.summarize(items, {}, RULES, today=MONDAY)
    assert result["days_without"] == 0 and result["per_day"][0]["note"] == "today, still going"


def night(day: date, score: int, bedtime: str, steps=6000):
    hour, minute = map(int, bedtime.split(":"))
    bed_day = day - timedelta(days=1) if hour >= 12 else day
    bed = datetime.combine(bed_day, datetime.min.time(), TZ).replace(hour=hour, minute=minute)
    return {"sleep_score": score, "readiness_score": score, "sleep_hours": 7.0, "hrv_ms": 40, "resting_hr": 55,
            "steps": steps, "bedtime_start": bed.isoformat()}


def test_best_night_comes_with_what_was_different():
    days = [MONDAY + timedelta(days=i) for i in range(6)]
    metrics = {d.isoformat(): night(d, 70, "23:30") for d in days}
    metrics[days[3].isoformat()] = night(days[3], 92, "21:45")                   # early to bed: the best night
    metrics[days[4].isoformat()] = night(days[4], 55, "23:30")                   # after a hard day with a late meal
    training = {d.isoformat(): {"minutes": 30, "hard": False} for d in days}
    training[days[3].isoformat()] = {"minutes": 75, "hard": True}
    food = {d.isoformat(): {"entries": 3, "calories": 2000, "caffeine_mg": 100, "alcohol_drinks": 0} for d in days}
    features = {d.isoformat(): health_history.features(d, metrics, training, food) for d in days[1:]}
    result = health_history.summarize(metrics, days[1], days[-1], ["sleep_score"], features_by_day=features)
    best, worst = result["best_and_worst"]["sleep_score"]["best"], result["best_and_worst"]["sleep_score"]["worst"]
    assert (best["day"], best["value"]) == ("Thu Oct 8", 92)
    assert best["what_was_different"] == ["went to bed at 9:45pm, 84 min earlier than usual (11:09pm)"]
    assert (worst["day"], worst["value"]) == ("Fri Oct 9", 55)
    assert worst["what_was_different"] == ["a hard workout the day before (75 min)"]


def test_nothing_stood_out_and_too_few_days_for_a_best():
    days = [MONDAY + timedelta(days=i) for i in range(4)]
    metrics = {d.isoformat(): night(d, 70 + i, "23:00") for i, d in enumerate(days)}
    features = {d.isoformat(): health_history.features(d, metrics, None, None) for d in days[1:]}
    result = health_history.summarize(metrics, days[1], days[-1], ["sleep_score"], features_by_day=features)
    assert result["best_and_worst"]["sleep_score"]["best"]["what_was_different"] == ["nothing stood out from the usual"]
    short = health_history.summarize(metrics, days[2], days[3], ["sleep_score"])
    assert "best_and_worst" not in short and short["average"]["sleep_score"] == 72.5
