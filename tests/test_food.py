from modules.training.baseline import food_totals
from modules.training.policy import policy_check
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
    assert food_totals([]) == {"entries": 0, "protein_g": 0, "fiber_g": 0, "carbs_g": 0,
                               "calories": 0, "caffeine_mg": 0, "alcohol_drinks": 0}


def test_no_food_day_plans_normally():
    # Food is left out of the decision: a hard plan on a good day is not capped.
    proposed = plan("HIIT", 5, "06:00-06:40", 40)
    final, overrides = policy_check(proposed, ctx(readiness=88, recent_food=[]))
    assert final == proposed
    assert overrides == []


# --- stated numbers and edits in place (M3.2 Phase 4) ------------------------------------

from datetime import datetime  # noqa: E402

from core import db  # noqa: E402
from core.config import TZ  # noqa: E402
from modules.nutrition.food import edit_food, log_food, user_numbers  # noqa: E402
from modules.nutrition.messages import confirm_entry  # noqa: E402

AT_10 = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)


def count_rows():
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM food_log").fetchone()[0]


def test_stated_numbers_replace_the_estimate_and_are_recorded(user, fake_llm):
    row = log_food(user, "poppi soda", AT_10, {"calories": 30, "carbs_g": 9, "protein_g": None})
    assert (row["est_calories"], row["est_carbs_g"], row["est_protein_g"]) == (30, 9, 20)
    assert user_numbers(row) == {"est_calories", "est_carbs_g"}
    assert "calories = 30, carbs_g = 9" in fake_llm.calls[-1][1]                 # the estimate is told
    assert confirm_entry(row) == "Logged poppi soda: 30 kcal, 9 g carbs (your numbers); about 20 g protein."


def test_numbers_only_edit_changes_the_same_row_without_a_new_estimate(user, fake_llm):
    row = log_food(user, "poppi strawberry lemon soda small can", AT_10)
    updated = edit_food(user, row, numbers={"fiber_g": 3, "carbs_g": 9, "calories": 30})
    assert updated["id"] == row["id"] and count_rows() == 1
    assert (updated["est_fiber_g"], updated["est_carbs_g"], updated["est_calories"]) == (3, 9, 30)
    assert updated["updated_at"] and fake_llm.kinds() == ["food"]                 # no second estimate


def test_a_new_description_re_estimates_only_what_they_never_stated(user, fake_llm):
    row = log_food(user, "5 slices of pizza", AT_10, {"calories": 1500})
    fake_llm.food_queue = [{"est_protein_g": 38, "est_calories": 1200}]
    updated = edit_food(user, row, new_text="5 slices of cheese pizza with olives and onions")
    assert updated["raw_text"] == "5 slices of cheese pizza with olives and onions" and count_rows() == 1
    assert updated["est_protein_g"] == 38                                         # re-estimated
    assert updated["est_calories"] == 1500                                        # theirs, kept
    assert "calories = 1500" in fake_llm.calls[-1][1]


def test_their_doubt_alone_re_estimates_with_it(user, fake_llm):
    row = log_food(user, "5 slices of pizza", AT_10)
    fake_llm.food_queue = [{"est_protein_g": 40}]
    updated = edit_food(user, row, feedback="I don't think it's 60g protein")
    assert updated["est_protein_g"] == 40 and updated["raw_text"] == "5 slices of pizza" and count_rows() == 1
    assert "I don't think it's 60g protein" in fake_llm.calls[-1][1]


def test_an_edit_can_move_the_time(user, fake_llm):
    row = log_food(user, "chai", AT_10)
    updated = edit_food(user, row, eaten_at=datetime(2026, 10, 7, 8, 30, tzinfo=TZ))
    assert updated["timestamp"].startswith("2026-10-07T08:30")
