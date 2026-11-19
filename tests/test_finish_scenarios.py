"""M4 finish scenarios (docs/scenarios.md H-1..H-8): the fixes from the judge calibration, scripted and live.
Fixtures' today is Wed Oct 7, 2026; their usual bedtime in the fixtures is after midnight."""
import json
from datetime import timedelta

import pytest

from core import db
from core.agent import MEDICAL, NOT_MEASURED
from core.reply_rules import ADVICE_OFFER
from evals.checks import Turn
from evals.judge import grade
from evals.setups import _food, set_up
from modules.nutrition.schemas import FoodItem, FoodItems, ItemGuess
from tests.conftest import call, live, needs_fixtures, parsed_checkin, say
from tests.test_goal_scenarios import runner  # noqa: F401  (fixture)
from tests.test_replay import (COFFEE_QUESTION, LONG_RUN_QUESTION, Chat, at, coach, last_tool_result,  # noqa: F401
                               real_checkin, real_meals)

pytestmark = needs_fixtures

CELSIUS_Q = "should I have a Celsius right now?"
CELSIUS = FoodItems(entry_type="drink", confidence="high", items=[
    FoodItem(name="Celsius", usda_query="energy drink, Celsius", brand="Celsius", quantity=1, unit="can",
             grams_guess=355, guess=ItemGuess(protein_g=0, fiber_g=0, carbs_g=0, calories=10, caffeine_mg=200,
                                              alcohol_drinks=0))])
HRV_Q = "Why is my HRV lower than usual today?"
HOW_HARD = "how hard can I go today?"
MUSCLE_Q = "am I building muscle with my runs and lifts?"


def checks_pass(message: str, reply: str, *checks: str) -> list[tuple[str, str]]:
    """The calibrated eval judge (evals/judge.py) on one reply, for the live tests."""
    t = Turn(case={}, message=message, earlier=[], reply=reply, tool_results=[])
    return grade(t, [{"check": c} for c in checks])


def plan_at(user, start: str, end: str, workout="easy_run"):
    """A planned workout later today (a plans row, as the planner leaves it)."""
    plan_id = db.start_plan(user.id, at(9).date(), "test", at(8))
    db.finish_plan(plan_id, {"energy": 4, "soreness": 1}, {"workout_type": workout, "intensity": 3, "duration_min": 40,
                                                           "time_slot": f"{start}-{end}"}, {}, [], {})


# --- H-1 the medical line on every pain, injury or illness answer --------------------------------------------

def test_h_1_illness_in_a_checkin_answer_gets_the_line_and_sore_legs_do_not(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    chat.send("want to work out", 9, 0, [call("start_checkin", request_text=None), say("")])
    fake_llm.checkin = parsed_checkin(3, 1, feeling="sore throat")
    sick = chat.send("3 1 sore throat", 9, 1, [call("submit_checkin", answer="3 1 sore throat"), say("")])
    assert sick.endswith(f"\n\n{MEDICAL}")                     # routed to training: the line comes from their words
    chat.send("want to work out", 11, 0, [call("start_checkin", request_text=None), say("")])
    fake_llm.checkin = parsed_checkin(4, 2, feeling="sore legs")
    sore = chat.send("4 2 sore legs", 11, 1, [call("submit_checkin", answer="4 2 sore legs"), say("")])
    assert MEDICAL not in sore                                  # training soreness is not pain


def test_h_1_a_cold_answer_ends_with_the_line_and_the_models_disclaimer_is_dropped(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("I have a cold, should I run?", 9, 0, [
        say("With only a runny nose, an easy run is usually fine; with a fever, rest. This isn't medical advice.")])
    assert reply == ("With only a runny nose, an easy run is usually fine; with a fever, rest.\n\n" + MEDICAL)


def test_h_1_a_cold_earlier_in_the_conversation_keeps_how_hard_easy(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("I have a cold, should I run?", 9, 0, [say("With only a runny nose, an easy run is usually fine.")])
    chat.send(HOW_HARD, 9, 5, [call("get_today", include_health=True), say("Rest today, or a gentle walk.")])
    call_ = last_tool_result(chat_model, "get_today")["health"]["today_call"]
    assert call_["unwell"] is True and call_["effort"] == "easy"        # whatever the ring says (readiness 75)


# --- H-2 lifts and body composition aren't measured, said by code -------------------------------------------

def test_h_2_a_muscle_question_gets_the_not_measured_line(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(MUSCLE_Q, 9, 0, [
        call("workouts", days_back=28),
        say("Mostly runs lately, with little lifting, so muscle is a stretch for now. I can't see your lifts or "
            "muscle.")])
    assert reply == ("Mostly runs lately, with little lifting, so muscle is a stretch for now.\n\n" + NOT_MEASURED)


# --- H-3 should I have it now? the call opens the reply ------------------------------------------------------

def test_h_3_a_celsius_in_the_morning_is_yes_first(coach, user, chat_model, fake_llm):
    fake_llm.food_items_queue = [CELSIUS]
    reply = Chat(coach, user, chat_model).send(CELSIUS_Q, 9, 0, [
        call("food_facts", text="a can of Celsius"), say("Yes, it's early and you haven't had caffeine yet.")])
    assert reply.startswith("Yes, it's early and you haven't had caffeine yet.\n\nA can of Celsius")
    result = last_tool_result(chat_model, "food_facts")
    assert result["call"] == "yes" and "you've had no caffeine yet today" in result["call_reason"]


def test_h_3_late_in_the_evening_is_no_and_a_wrong_yes_becomes_codes_sentence(coach, user, chat_model, fake_llm):
    fake_llm.food_items_queue = [CELSIUS]
    reply = Chat(coach, user, chat_model).send(CELSIUS_Q, 21, 0, [
        call("food_facts", text="a can of Celsius"), say("Sure, go for it."), say("Sure, enjoy it.")])  # + rewrite
    first = reply.split("\n\n")[0]
    assert first.startswith("Not now: your usual bedtime (") and "caffeine this late can cut into your sleep" in first


def test_h_3_a_run_later_today_is_wait(coach, user, chat_model, fake_llm):
    plan_at(user, "11:30", "12:10")
    fake_llm.food_items_queue = [CELSIUS]
    reply = Chat(coach, user, chat_model).send(CELSIUS_Q, 9, 0, [
        call("food_facts", text="a can of Celsius"), say("Wait until about 10:30, an hour before your run.")])
    assert reply.startswith("Wait until about 10:30, an hour before your run.\n\nA can of Celsius")
    assert last_tool_result(chat_model, "food_facts")["call_reason"] == (
        "have it around 10:30am, about 60 min before your easy run at 11:30am")


# --- H-4 the next step itself, never an offer of it ----------------------------------------------------------

def test_h_4_an_offer_of_a_tweak_is_rewritten_into_the_step(coach, user, chat_model):
    offer = "Your HRV is above your usual today. Want one quick bedtime tweak to keep it up tonight?"
    step = "Your HRV is above your usual today. Lights out at your usual time tonight keeps it there."
    reply = Chat(coach, user, chat_model).send(HRV_Q, 9, 0, [call("get_today", include_health=True), say(offer),
                                                            say(step)])
    assert reply == step
    assert last_tool_result(chat_model, "get_today")["health"]["usual_bedtime"].endswith("am")   # a code clock time


def test_h_4_a_rewrite_that_still_offers_is_trimmed(coach, user, chat_model):
    offer = "Your HRV is above your usual today. Want tips?"
    reply = Chat(coach, user, chat_model).send(HRV_Q, 9, 0, [call("get_today", include_health=True), say(offer),
                                                            say(offer)])
    assert reply == "Your HRV is above your usual today." and not ADVICE_OFFER.search(reply)


# --- H-5 one clear recommendation, from code ------------------------------------------------------------------

def test_h_5_how_hard_gets_codes_call_and_borderline_only_near_the_floor(coach, user, chat_model, patch_oura):
    chat = Chat(coach, user, chat_model)
    chat.send(HOW_HARD, 9, 0, [call("get_today", include_health=True), say("Go for it: HIIT now.")])
    call_ = last_tool_result(chat_model, "get_today")["health"]["today_call"]
    assert call_ == {"effort": "hard", "workout": "30 min of HIIT", "from_program": False, "when": "now until 9:00pm",
                     "borderline": False}                           # readiness 75, floor 70: a clear push day


def test_h_5_readiness_just_above_the_floor_is_borderline(coach, user, chat_model, patch_oura):
    patch_oura(readiness=72)
    Chat(coach, user, chat_model).send(HOW_HARD, 9, 0, [call("get_today", include_health=True), say("Easy run.")])
    assert last_tool_result(chat_model, "get_today")["health"]["today_call"]["borderline"] is True


def test_h_5_a_program_session_today_is_the_recommendation(coach, user, chat_model, runner):  # noqa: F811
    set_up(user, at(9).date() - timedelta(days=3))                 # week 1's Wednesday: an easy run, then strength
    Chat(coach, user, chat_model).send(HOW_HARD, 9, 0, [call("get_today", include_health=True), say("Your run.")])
    call_ = last_tool_result(chat_model, "get_today")["health"]["today_call"]
    assert call_["from_program"] is True and "easy run then" in call_["workout"] and "strength" in call_["workout"]


# --- H-6 the caffeine line is theirs -----------------------------------------------------------------------------

def test_h_6_caffeine_for_them_today_with_what_they_had_and_their_bedtime(coach, user, chat_model):
    first = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 7, 0, [call("fueling_plan", parts=["caffeine"]),
                                                                      say("")])
    assert first.startswith("Caffeine, if you use it: about 110 mg for you today, about 60 min before the run")
    db.add_food(user.id, "a coffee", {"entry_type": "drink", "est_calories": 5, "est_caffeine_mg": 80}, at(7, 30))
    later = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 8, 0, [call("fueling_plan", parts=["caffeine"]),
                                                                      say("")])
    assert "about 110 mg for you today (with the 80 mg you've had today)" in later
    evening = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 20, 0, [call("fueling_plan", parts=["caffeine"]),
                                                                         say("")])
    assert evening.startswith("Caffeine: skip it today: your usual bedtime (")


# --- H-7 real foods and timing in the fueling plan --------------------------------------------------------------

def test_h_7_the_plan_names_their_own_foods_and_when(coach, user, chat_model):
    for back in (1, 2, 3):
        _food(user, "paneer bhurji with 2 rotis", 20, 0, protein=24, carbs=55, calories=600, day_back=back)
        _food(user, "banana", 8, 0, protein=1, carbs=27, calories=105, fiber=3, kind="snack", day_back=back)
        _food(user, "Greek yogurt", 16, 0, protein=17, carbs=6, calories=150, fiber=0, kind="snack", day_back=back)
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [call("fueling_plan", day_text="Saturday"),
                                                                        say("")])
    lines = reply.splitlines()
    assert any(line.startswith("Fri dinner: your usual paneer bhurji with 2 rotis (no extra carbs needed for a run "
                               "under 90 min)") for line in lines)
    assert "Sat, 60–90 min before: your usual banana, if you want something." in lines
    assert any(line.startswith("After: 20–25 g protein (your usual Greek yogurt works)") for line in lines)
    assert last_tool_result(chat_model, "fueling_plan")["carb_load"] is False


# --- H-8 brainstorm first, the recipe after a pick ----------------------------------------------------------------

def test_h_8_what_can_i_make_is_three_one_line_ideas(coach, user, chat_model, fake_llm):
    from tests.conftest import ideas
    fake_llm.meal = ideas("Paneer bhurji with 2 rotis", "Palak tofu with rice", "Moong dal chilla with curd")
    reply = Chat(coach, user, chat_model).send("what can I make with paneer and spinach?", 13, 0, [
        call("suggest_meal", meal=None, ingredients=["paneer", "spinach"], constraints=None), say("")])
    assert reply.splitlines()[1:] == ["1. Paneer bhurji with 2 rotis · ~25 g protein",
                                      "2. Palak tofu with rice · ~25 g protein",
                                      "3. Moong dal chilla with curd · ~25 g protein", "Pick one for the recipe."]


# --- live (the real models) ---------------------------------------------------------------------------------------

@live
def test_live_h_1_a_cold_ends_with_the_line_and_sore_legs_do_not(coach, user):
    chat = Chat(coach, user)
    cold = chat.send("I have a cold, should I run?", 9, 0)
    legs = chat.send("my legs are a bit stiff after yesterday's run, how hard can I go today?", 9, 5)
    chat.print("scenario H-1: the medical line")
    assert cold.splitlines()[-1] == MEDICAL and MEDICAL not in legs


@live
def test_live_h_2_muscle_question_says_what_isnt_measured(coach, user):
    chat = Chat(coach, user)
    reply = chat.send(MUSCLE_Q, 9, 0)
    chat.print("scenario H-2: not measured")
    assert NOT_MEASURED in reply and reply.count("aren't measured") + reply.count("can't measure") == 1


@live
def test_live_h_3_should_i_have_a_celsius_opens_with_the_call(coach, user, fake_llm):
    fake_llm.food_items_queue = [CELSIUS] * 3
    chat = Chat(coach, user)                          # in time order, each a new conversation (30+ min apart)
    morning = chat.send(CELSIUS_Q, 8, 0)
    plan_at(user, "11:30", "12:10")
    before_run = chat.send(CELSIUS_Q, 9, 0)
    evening = chat.send(CELSIUS_Q, 21, 0)
    chat.print("scenario H-3: should I have it now?")
    assert morning.lower().startswith(("yes", "yep", "yeah", "sure", "go for it", "go ahead"))
    assert evening.lower().startswith(("no", "not now", "not tonight", "skip", "caffeine: skip"))   # via fueling_plan too
    assert before_run.lower().startswith(("wait", "hold off", "not yet"))


@live
def test_live_h_4_hrv_question_gives_the_step_itself(coach, user):
    chat = Chat(coach, user)
    reply = chat.send(HRV_Q, 9, 0)
    chat.print("scenario H-4: the next step itself")
    assert not ADVICE_OFFER.search(reply)
    [(answer, reason)] = checks_pass(HRV_Q, reply, "It gives one concrete next step itself (what to do, and when), not "
                                                   "a menu of options or an offer to give one")
    assert answer == "yes", reason


@live
def test_live_h_5_how_hard_gives_one_recommendation(coach, user):
    chat = Chat(coach, user)
    reply = chat.send(HOW_HARD, 9, 0)
    chat.print("scenario H-5: one clear recommendation")
    assert "HIIT" in reply
    [(answer, reason)] = checks_pass(HOW_HARD, reply, "If it suggests what to do, it gives one clear recommendation; an "
                                                      "alternative only if the data is borderline (readiness 75, floor "
                                                      "70: not borderline)")
    assert answer == "yes", reason


@live
def test_live_h_6_caffeine_is_sized_for_them(coach, user):
    chat = Chat(coach, user)
    reply = chat.send(COFFEE_QUESTION, 7, 0)
    chat.print("scenario H-6: caffeine for them")
    assert "about 110 mg for you today" in reply


@live
def test_live_h_7_long_run_plan_names_their_foods(coach, user):
    for back in (1, 2, 3):
        _food(user, "paneer bhurji with 2 rotis", 20, 0, protein=24, carbs=55, calories=600, day_back=back)
        _food(user, "banana", 8, 0, protein=1, carbs=27, calories=105, fiber=3, kind="snack", day_back=back)
    chat = Chat(coach, user)
    reply = chat.send(LONG_RUN_QUESTION, 9, 0)
    chat.print("scenario H-7: real foods")
    assert "Fri dinner: your usual paneer bhurji with 2 rotis" in reply
    assert "Sat, 60–90 min before: your usual banana" in reply


@live
def test_live_h_8_what_can_i_make_is_a_brainstorm(coach, user, real_meals):
    chat = Chat(coach, user)
    reply = chat.send("what can I make with paneer and spinach?", 13, 0)
    chat.print("scenario H-8: brainstorm")
    ideas = [line for line in reply.splitlines() if line[:2] in ("1.", "2.", "3.")]
    assert 2 <= len(ideas) <= 3 and all("g protein" in line for line in ideas)
    assert reply.endswith("Pick one for the recipe.") and " g (" not in reply      # no portions before a pick
