"""The nutrition share of the daily score (pure function, config/score.yaml)."""
import pytest

from modules.nutrition.score import calorie_fraction, load_rules, score_day

TARGETS = {"calories": 2000, "protein_g": 120, "carbs_g": 250, "fat_g": 55, "fiber_g": 28}
RULES = load_rules()


def meal(calories, protein, entry_type="meal"):
    return {"entry_type": entry_type, "est_calories": calories, "est_protein_g": protein, "est_carbs_g": 0,
            "est_fiber_g": 0, "est_caffeine_mg": 0, "alcohol_drinks": 0}


def three_meals(calories=2000, protein=120):
    return [meal(calories / 3, protein / 3) for _ in range(3)]


def test_on_target_with_three_meals_is_full():
    s = score_day(three_meals(), TARGETS, RULES)
    assert s.fraction == 1.0
    assert s.note == "protein 120 of 120 g · 2000 of 2000 kcal, on target · 3 meals logged"


def test_nothing_logged_is_zero_and_just_says_not_logged():
    s = score_day([], TARGETS, RULES)
    assert (s.fraction, s.note) == (0.0, "not logged")


@pytest.mark.parametrize("calories, expected", [(2000, 1.0), (2200, 1.0), (1800, 1.0), (2400, 0.5), (1600, 0.5),
                                                (2600, 0.0), (1400, 0.0), (1000, 0.0)])
def test_calories_are_full_within_10_percent_and_zero_at_30(calories, expected):
    assert calorie_fraction(calories, 2000, 10, 30) == expected


def test_rigor_changes_the_calorie_band():
    assert score_day(three_meals(1720), TARGETS, RULES, "light").details["parts"][1]["fraction"] == 1.0  # -14%
    assert score_day(three_meals(1720), TARGETS, RULES, "intense").details["parts"][1]["fraction"] < 1.0


def test_under_the_floor_after_the_cutoff_never_scores_calories(monkeypatch):
    rules = {**RULES, "calories_zero_pct": {"moderate": 60}}       # a band wide enough to "reward" 1100 kcal
    before = score_day(three_meals(1100), TARGETS, rules, after_cutoff=False)
    after = score_day(three_meals(1100), TARGETS, rules, after_cutoff=True)
    assert before.details["parts"][1]["fraction"] > 0
    assert after.details["parts"][1]["fraction"] == 0.0
    assert "1100 of 2000 kcal, well under your target" in after.note


def test_protein_is_linear_to_the_target_and_meals_count_up_to_three():
    s = score_day([meal(2000, 60)], TARGETS, RULES)
    parts = {p["name"]: p["fraction"] for p in s.details["parts"]}
    assert parts == {"protein": 0.5, "calories": 1.0, "meals": pytest.approx(1 / 3)}
    assert s.fraction == pytest.approx(0.4 * 0.5 + 0.4 * 1.0 + 0.2 / 3, abs=0.001)


def test_drinks_and_supplements_are_not_meals():
    s = score_day([meal(10, 0, "drink"), meal(5, 0, "supplement")], TARGETS, RULES)
    assert s.details["parts"][-1] == {"name": "meals", "fraction": 0.0, "note": "0 meals logged"}


def test_without_targets_only_meals_count():
    s = score_day(three_meals(), None, RULES)
    assert (s.fraction, s.note) == (1.0, "3 meals logged")


def test_same_inputs_same_score():
    assert score_day(three_meals(1700, 90), TARGETS, RULES) == score_day(three_meals(1700, 90), TARGETS, RULES)


# --- the module, on fixtures (targets from Oura body data) ----------------------------------

from datetime import time  # noqa: E402

from connectors.health import Health  # noqa: E402
from core import db  # noqa: E402
from modules.nutrition.module import NutritionModule  # noqa: E402
from tests.conftest import needs_fixtures  # noqa: E402
from today import fixtures_now  # noqa: E402


@needs_fixtures
def test_module_score_uses_the_days_food_and_targets(user):
    module, day = NutritionModule(Health(use_fixtures=True)), fixtures_now(time(12)).date()
    assert module.score(user, day, fixtures_now(time(20))).note == "not logged"
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, fixtures_now(time(8)))
    evening = module.score(user, day, fixtures_now(time(20)))                     # before the cutoff
    night = module.score(user, day, fixtures_now(time(21, 30)))                   # after: the floor applies
    assert evening.note.startswith("protein 12 of 126 g · 400 of 2218 kcal")
    assert "well under your target" not in evening.note and "well under your target" in night.note
