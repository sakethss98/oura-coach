from baseline import compute_baseline, food_totals
from policy import policy_check
from tests.test_policy import ctx, plan


def entry(protein=0, fiber=0, carbs=0, calories=0, caffeine=0, alcohol=0):
    return {"est_protein_g": protein, "est_fiber_g": fiber, "est_carbs_g": carbs,
            "est_calories": calories, "est_caffeine_mg": caffeine, "alcohol_drinks": alcohol}


def test_daily_food_sum():
    entries = [
        entry(protein=30, fiber=5, carbs=40, calories=450),           # meal
        entry(calories=5, caffeine=95),                               # coffee
        entry(protein=24, carbs=3, calories=120),                     # protein shake
        entry(protein=30, fiber=5, carbs=40, calories=450),           # same meal again, still counted
        entry(carbs=13, calories=150, alcohol=1),                     # beer
    ]
    assert food_totals(entries) == {
        "entries": 5, "protein_g": 84, "fiber_g": 10, "carbs_g": 96,
        "calories": 1175, "caffeine_mg": 95, "alcohol_drinks": 1,
    }


def test_no_food_logged_gives_zero_entries():
    assert food_totals([])["entries"] == 0
    baseline = compute_baseline({}, [], [], "morning", 70, food_today=[], food_yesterday=[])
    assert baseline["food"] == {"yesterday": food_totals([]), "today": food_totals([])}


def test_no_food_day_plans_normally():
    # Food is left out of the decision: a hard plan on a good day is not capped.
    proposed = plan("HIIT", 5, "06:00-06:40", 40)
    final, overrides = policy_check(proposed, ctx(readiness=88))
    assert final == proposed
    assert overrides == []
