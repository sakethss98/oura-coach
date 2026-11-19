"""Meal ideas: code sizes them, keeps them varied (no dish from the last 5 days) and saves them."""
from datetime import date, datetime, time, timedelta

from core import db
from core.config import TZ
from modules.nutrition import meals
from modules.nutrition.targets import dish_key, fallback_meal, meal_budget
from tests.conftest import ideas, needs_fixtures
from today import fixtures_now

TARGETS = {"calories": 2000, "protein_g": 120}


def test_dish_key_is_the_dish_not_its_sides_or_portions():
    assert dish_key("Paneer bhurji (150 g, about 1 katori) with 2 chapatis") == "paneer bhurji"
    assert dish_key("Paneer bhurji wrap") == "paneer bhurji"
    assert dish_key("Quick dal palak and rice") == "dal palak"
    assert dish_key("Khichdi") == "khichdi"


def test_meal_budget_splits_what_is_left_over_the_meals_to_come():
    left = {"calories": 1500, "protein_g": 90}
    at = lambda h: datetime(2026, 10, 7, h, tzinfo=TZ)  # noqa: E731
    assert meal_budget(TARGETS, left, at(8)) == {"calories": 500, "protein_g": 30, "meals_left_today": 3}
    assert meal_budget(TARGETS, left, at(13)) == {"calories": 700, "protein_g": 42, "meals_left_today": 2}
    assert meal_budget(TARGETS, left, at(19))["calories"] == 700                 # capped at 35% of the day
    assert meal_budget(TARGETS, {"calories": 0, "protein_g": 0}, at(19))["calories"] == 200
    assert meal_budget(None, None, at(19)) is None


def test_fallback_meals_rotate_by_day_and_skip_recent_dishes():
    day = date(2026, 10, 7)
    first, nxt = fallback_meal({"protein_g": 80}, "vegetarian", day), fallback_meal({"protein_g": 80}, "vegetarian",
                                                                                    day + timedelta(days=1))
    assert dish_key(first) != dish_key(nxt)
    assert dish_key(fallback_meal({"protein_g": 80}, "vegetarian", day, avoid=[first])) != dish_key(first)
    assert fallback_meal({"protein_g": 5}, "vegan-not-in-the-file", day)          # unknown diet: vegetarian list


@needs_fixtures
def test_repeats_are_dropped_then_asked_again_then_code_picks(user, fake_llm):
    now = fixtures_now(time(19, 0))
    db.add_meal_suggestions(1, now.date() - timedelta(days=4), ["Paneer bhurji with 2 rotis"])
    fake_llm.meal_queue = [ideas("Paneer bhurji wrap", "Dal palak with rice")]
    result = meals.suggest(user, now.date(), now, True, "dinner")
    assert [o["dish"] for o in result["options"]] == ["Dal palak with rice"]       # the repeat is dropped
    assert '"Paneer bhurji with 2 rotis"' in fake_llm.calls[-1][1]                 # and was named as do-not

    fake_llm.meal_queue = [ideas("Dal palak bowl"), ideas("Dal palak again")]      # every option a repeat, twice
    result = meals.suggest(user, now.date(), now, True, "dinner")
    assert [k for k in fake_llm.kinds() if k == "meal"] == ["meal"] * 3
    assert '"rejected_as_repeats"' in fake_llm.calls[-1][1]
    [option] = result["options"]
    assert dish_key(option["dish"]) not in {"paneer bhurji", "dal palak"}          # code's pick, not a repeat
    assert [s["dish"] for s in db.meal_suggestions_since(1, now.date())] == ["Dal palak with rice", option["dish"]]


@needs_fixtures
def test_ideas_are_sized_for_one_meal_and_built_on_what_they_log(user, fake_llm):
    now = fixtures_now(time(19, 0))
    for h in (8, 9):
        db.add_food(1, "Poha with peanuts", {"est_calories": 300, "est_protein_g": 8}, now.replace(hour=h))
    result = meals.suggest(user, now.date(), now, True, "dinner", ["dal", "spinach"])
    sent = fake_llm.calls[-1][1]
    assert '"poha with peanuts"' in sent and '"spinach"' in sent and '"size_for_this_meal"' in sent
    assert result["text"].splitlines()[0].startswith("For dinner, about ")
    assert result["text"].splitlines()[1:] == ["1. Dal palak with jeera rice · ~25 g protein",       # one line each
                                               "2. Spinach moong dal khichdi · ~25 g protein", "Pick one for the recipe."]


@needs_fixtures
def test_higher_protein_puts_the_same_dish_first_and_drops_lower_ones(user, fake_llm):
    now = fixtures_now(time(19, 0))
    fake_llm.meal_queue = [ideas("Paneer tikka with roti", protein=48)]
    fake_llm.meal_queue[0].options += ideas("Masala egg bhurji + 100 g paneer", protein=55).options + \
        ideas("Moong dal chilla", protein=30).options            # 30 < 43: not higher than what they saw
    result = meals.suggest(user, now.date(), now, True, "dinner", improve={"dish": "Masala egg bhurji with rice",
                                                                         "protein_g": 43})
    assert [o["dish"] for o in result["options"]] == ["Masala egg bhurji + 100 g paneer", "Paneer tikka with roti"]
    assert '"improve"' in fake_llm.calls[-1][1] and "Masala egg bhurji with rice" not in str(
        __import__("json").loads(fake_llm.calls[-1][1])["do_not_suggest"])     # the dish itself isn't a repeat here


@needs_fixtures
def test_what_they_ate_yesterday_is_not_suggested_again(user, fake_llm):
    now = fixtures_now(time(19, 0))
    db.add_food(1, "Paneer bhurji and 2 rotis", {"est_calories": 600}, now - timedelta(days=1))
    fake_llm.meal_queue = [ideas("Paneer bhurji wrap", "Rajma chawal")]
    result = meals.suggest(user, now.date(), now, True, "dinner")
    assert [o["dish"] for o in result["options"]] == ["Rajma chawal"]
    assert '"Paneer bhurji and 2 rotis"' in fake_llm.calls[-1][1]


@needs_fixtures
def test_a_recipe_keeps_the_dish_they_picked(user, fake_llm):
    now = fixtures_now(time(19, 0))
    text = meals.recipe(user, now.date(), now, True, "Spinach moong dal khichdi", "higher protein")["text"]
    assert text.splitlines()[0] == "Spinach moong dal khichdi (higher protein) · about 520 kcal, 24 g protein"
    assert "moong dal 60 g (1/3 cup dry)" in text and "1. Rinse the dal and rice." in text


@needs_fixtures
def test_higher_protein_is_said_only_when_the_new_version_has_more(user, fake_llm):
    now = fixtures_now(time(19, 0))
    dish = "Spinach moong dal khichdi"
    fake_llm.recipe_queue = [fake_llm.recipe.model_copy(update={"est_protein_g": 31})]
    text = meals.recipe(user, now.date(), now, True, dish, "higher protein", previous_protein_g=24)["text"]
    assert text.startswith(f"{dish} (higher protein) · about 520 kcal, 31 g protein")
    assert '"previous_version_protein_g": 24' in fake_llm.calls[-1][1]


@needs_fixtures
def test_a_version_that_isnt_higher_in_protein_never_gets_the_label(user, fake_llm):
    now = fixtures_now(time(19, 0))
    dish = "Masala egg bhurji with rice"
    fake_llm.recipe_queue = [fake_llm.recipe.model_copy(update={"est_protein_g": p}) for p in (42, 43)]
    text = meals.recipe(user, now.date(), now, True, dish, "higher protein", previous_protein_g=43)["text"]
    assert text.startswith(f"{dish} · about 520 kcal, 43 g protein") and "(higher protein)" not in text
    assert text.endswith(meals.NOT_HIGHER.format(new=43, old=43))
    assert [k for k, _ in fake_llm.calls].count("recipe") == 2                 # asked once more first
    assert '"this_try_protein_g": 42' in fake_llm.calls[-1][1]


def test_the_previous_version_is_the_latest_recipe_else_its_meal_idea():
    from langchain_core.messages import ToolMessage
    idea = ToolMessage("{}", tool_call_id="1", name="suggest_meal",
                       artifact={"verbatim": "…", "protein_by_dish": {"Spinach moong dal khichdi": 20, "Rajma": 18}})
    first = ToolMessage("{}", tool_call_id="2", name="meal_recipe",
                        artifact={"verbatim": "…", "dish": "Spinach moong dal khichdi", "protein_g": 24})
    assert meals.last_protein([idea], "Spinach moong dal khichdi") == 20
    assert meals.last_protein([idea, first], "Spinach moong dal khichdi") == 24
    assert meals.last_protein([idea, first], "Paneer tikka") is None
