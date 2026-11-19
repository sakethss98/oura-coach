"""Part F scenarios (docs/scenarios.md F-1..F-3): USDA FoodData Central lookups through the agent, scripted (the
real responses saved in fixtures/usda/) and live (the pinned server)."""
import pytest

from core import db, mcp_tools
from modules.nutrition.schemas import FoodItem, FoodItems, ItemGuess
from tests.conftest import call, live, needs_fixtures, needs_usda_fixtures, say
from tests.test_replay import Chat, at, coach, food_today, real_food  # noqa: F401  (fixtures)

pytestmark = [needs_fixtures, needs_usda_fixtures]

CHOBANI_Q = "how much protein is in a Chobani plain Greek yogurt?"
CHOBANI = "a Chobani plain Greek yogurt"
LUNCH = "200 g chicken breast, a cup of rice and a tablespoon of olive oil for lunch"
SOURCE = "USDA FoodData Central"


def guess(protein, calories, carbs=0, fiber=0):
    return ItemGuess(protein_g=protein, fiber_g=fiber, carbs_g=carbs, calories=calories, caffeine_mg=0,
                     alcohol_drinks=0)


CHOBANI_ITEMS = FoodItems(entry_type="snack", confidence="high", items=[
    FoodItem(name=CHOBANI, usda_query="plain Greek yogurt", brand="Chobani", quantity=1, unit="container",
             grams_guess=150, guess=guess(15, 90, 6))])
LUNCH_ITEMS = FoodItems(entry_type="meal", confidence="high", items=[
    FoodItem(name="chicken breast", usda_query="chicken breast, meat only, roasted", brand=None, quantity=200,
             unit="g", grams_guess=200, guess=guess(60, 330)),
    FoodItem(name="rice", usda_query="rice, white, cooked, no added fat", brand=None, quantity=1, unit="cup",
             grams_guess=160, guess=guess(4, 200, 44)),
    FoodItem(name="olive oil", usda_query="olive oil", brand=None, quantity=1, unit="tbsp", grams_guess=14,
             guess=guess(0, 120))])


def test_f_1_a_branded_item_is_answered_and_logged_with_the_same_numbers(coach, user, chat_model, fake_llm, usda):
    fake_llm.food_items_queue = [CHOBANI_ITEMS]
    chat = Chat(coach, user, chat_model)
    answer = chat.send(CHOBANI_Q, 9, 0, [call("food_facts", text=CHOBANI), say("")])
    assert answer == ("A Chobani plain Greek yogurt (150 g): about 14 g protein, 81 kcal, 5 g carbs, 0 g fiber.\n"
                      "Numbers: USDA FoodData Central (Yogurt, Greek, nonfat, plain, CHOBANI); amount estimated for "
                      "a Chobani plain Greek yogurt")
    logged = chat.send("had one at 9", 9, 5, [call("log_food", text=CHOBANI, time_text="9"),
                                                say("Logged it: about 81 kcal, 14 g protein.")])
    [row] = food_today(user)
    assert row["est_protein_g"] == pytest.approx(14.3) and row["est_calories"] == pytest.approx(81.0)
    assert [k for k, _ in fake_llm.calls].count("food") == 1             # read once: the same items both times
    assert logged.endswith("Numbers: USDA FoodData Central (Yogurt, Greek, nonfat, plain, CHOBANI); amount "
                           "estimated for a Chobani plain Greek yogurt")


def test_f_2_a_mixed_dish_is_looked_up_item_by_item_and_summed_by_code(coach, user, chat_model, fake_llm, usda):
    fake_llm.food_items_queue = [LUNCH_ITEMS]
    reply = Chat(coach, user, chat_model).send(LUNCH, 12, 30, [call("log_food", text=LUNCH, time_text=None),
                                                               say("Logged your lunch.")])
    [row] = food_today(user)
    items = __import__("json").loads(row["nutrition_json"])
    assert [i["grams"] for i in items] == [200, 158, 14] and all(i["source"] == SOURCE for i in items)
    assert row["est_protein_g"] == pytest.approx(sum(i["numbers"]["protein_g"] for i in items), abs=0.11)
    assert row["est_calories"] == pytest.approx(sum(i["numbers"]["calories"] for i in items), abs=0.11)
    assert reply.endswith("Numbers: USDA FoodData Central (Chicken, broilers or fryers, breast, meat only, cooked, "
                          "roasted · Rice, white, cooked, no added fat · Olive oil)")


def test_f_3_the_server_down_gives_approximate_numbers_and_no_usda_line(coach, user, chat_model, fake_llm):
    fake_llm.food_items_queue = [CHOBANI_ITEMS]
    answer = Chat(coach, user, chat_model).send(CHOBANI_Q, 9, 0, [call("food_facts", text=CHOBANI), say("")])
    assert answer == ("A Chobani plain Greek yogurt (150 g): about 15 g protein, 90 kcal, 6 g carbs, 0 g fiber. "
                      "These are approximate (not from USDA FoodData Central).")
    assert "Numbers:" not in answer


def test_f_1_the_coach_never_names_the_source_in_its_own_words(coach, user, chat_model, fake_llm, usda):
    fake_llm.food_items_queue = [CHOBANI_ITEMS]
    reply = Chat(coach, user, chat_model).send(CHOBANI_Q, 9, 0, [
        call("food_facts", text=CHOBANI), say("According to USDA, it's a great protein snack.")])
    assert reply.count(SOURCE) == 1 and "According to" not in reply          # only the code's line names it


# --- live (the pinned server, the real model) --------------------------------------------------------------

@live
def test_live_f_1_branded_item(coach, user, real_food):
    chat = Chat(coach, user)
    answer = chat.send(CHOBANI_Q, 9, 0)
    chat.send("had one at 9", 9, 5)                      # the same conversation (sessions end after 15 quiet minutes)
    chat.print("scenario F-1: a branded item")
    assert "Numbers: USDA FoodData Central (" in answer
    [row] = food_today(user)
    assert f"{row['est_protein_g']:.0f} g protein" in answer                # the same numbers, answered and logged


@live
def test_live_f_2_mixed_dish(coach, user, real_food):
    chat = Chat(coach, user)
    reply = chat.send(LUNCH, 12, 30)
    chat.print("scenario F-2: a mixed dish with weights")
    [row] = food_today(user)
    items = __import__("json").loads(row["nutrition_json"])
    assert len(items) == 3 and sum(1 for i in items if i["source"] == SOURCE) >= 2
    assert "Numbers: USDA FoodData Central (" in reply


@live
def test_live_f_3_server_down(coach, user, real_food, monkeypatch):
    real = mcp_tools.call
    monkeypatch.setattr(mcp_tools, "call", lambda server, tool, args: None if server == "usda"
                        else real(server, tool, args))
    chat = Chat(coach, user)
    reply = chat.send(CHOBANI_Q, 9, 0)
    chat.print("scenario F-3: the server down")
    assert SOURCE not in reply.replace("not from USDA FoodData Central", "") and "approximate" in reply.lower()


def test_f_1_a_should_i_question_keeps_the_coachs_answer_next_to_the_numbers(coach, user, chat_model, fake_llm, usda):
    fake_llm.food_items_queue = [CHOBANI_ITEMS]
    line = "Yes, it's a good protein snack before your run."
    reply = Chat(coach, user, chat_model).send("should I have a Chobani plain Greek yogurt now?", 9, 0, [
        call("food_facts", text=CHOBANI), say(line)])
    assert reply.startswith(line + "\n\nA Chobani plain Greek yogurt (150 g): about 14 g protein")  # answer first (H-3)
