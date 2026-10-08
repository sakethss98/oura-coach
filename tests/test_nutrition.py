import json
from datetime import datetime

from baseline import food_totals
from config import FIXTURES_DIR, TZ
from nutrition import daily_targets, recent_food, remaining
from oura_client import body_metrics
from tests.conftest import needs_fixtures

GOALS = {"weight_kg": None, "activity_factor": 1.5, "deficit_kcal": 500, "protein_g_per_kg": 2.0,
         "fat_pct": 25, "fiber_g_per_1000kcal": 14}
MALE = {"age": 30, "weight_kg": 70, "height_cm": 175, "sex": "male"}


def test_mifflin_st_jeor_male():
    result = daily_targets(MALE, GOALS)
    # BMR = 700 + 1093.75 - 150 + 5 = 1648.75; calories = 1648.75 * 1.5 - 500 = 1973.1
    assert result["inputs"]["bmr"] == 1649
    assert result["targets"] == {"calories": 1973, "protein_g": 140, "fat_g": 55, "carbs_g": 230, "fiber_g": 28}


def test_mifflin_st_jeor_female():
    result = daily_targets({**MALE, "sex": "female"}, GOALS)
    assert result["inputs"]["bmr"] == 1483  # 1648.75 - 166


def test_calories_never_below_bmr():
    result = daily_targets(MALE, {**GOALS, "deficit_kcal": 5000})
    assert result["targets"]["calories"] == 1649


def test_weight_override_from_goals():
    result = daily_targets(MALE, {**GOALS, "weight_kg": 80})
    assert result["inputs"]["weight_source"] == "goals.yaml"
    assert result["targets"]["protein_g"] == 160


def test_missing_body_field_gives_no_targets():
    result = daily_targets({**MALE, "age": None}, GOALS)
    assert result["targets"] is None
    assert "age" in result["reason"]


@needs_fixtures
def test_body_metrics_from_fixture():
    raw = {"personal_info": json.loads((FIXTURES_DIR / "personal_info.json").read_text())}
    body = body_metrics(raw)
    assert set(body) == {"age", "weight_kg", "height_cm", "sex"}
    assert 100 < body["height_cm"] < 250            # meters converted to cm
    assert daily_targets(body, GOALS)["targets"] is not None


def entry(calories, protein, carbs=0, fiber=0, timestamp="2026-10-07T08:00:00-05:00"):
    return {"est_calories": calories, "est_protein_g": protein, "est_carbs_g": carbs, "est_fiber_g": fiber,
            "est_caffeine_mg": 0, "alcohol_drinks": 0, "timestamp": timestamp, "raw_text": "x"}


def test_remaining_after_two_meals():
    targets = {"calories": 2000, "protein_g": 140, "carbs_g": 220, "fat_g": 55, "fiber_g": 28}
    totals = food_totals([entry(500, 20, 60, 5), entry(700, 30, 90, 8)])
    assert remaining(targets, totals) == {"calories": 800, "protein_g": 90, "carbs_g": 70, "fiber_g": 15}


def test_remaining_never_negative():
    targets = {"calories": 1000, "protein_g": 10, "carbs_g": 10, "fat_g": 10, "fiber_g": 10}
    assert remaining(targets, food_totals([entry(1500, 50, 50, 50)]))["calories"] == 0


def test_no_food_leaves_full_targets():
    targets = {"calories": 2000, "protein_g": 140, "carbs_g": 220, "fat_g": 55, "fiber_g": 28}
    assert remaining(targets, food_totals([])) == {"calories": 2000, "protein_g": 140, "carbs_g": 220,
                                                   "fiber_g": 28}
    assert remaining(None, food_totals([])) is None


def test_recent_food_minutes_ago():
    now = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    result = recent_food([entry(500, 20), entry(100, 5, timestamp="2026-10-07T10:00:00-05:00")], now)
    assert result == [{"minutes_ago": 60, "calories": 500, "text": "x"}]   # future entry left out

