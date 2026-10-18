"""Replay scenarios from real usage (M3.2 brief, Phase 6 list), on fixtures.

Scripted tests feed the agent preset model turns, so they check the code around the model:
tools, database effects, sessions and the reply rules. Live tests (RUN_LIVE_LLM=1) use the real
model and check that it understands; run them with -s to see the transcripts.
Plan and food estimates are stubbed in both (fake_llm), so numbers are predictable; live tests read
the check-in answer with the real model (real_checkin), since that is understanding too.
Scenario ids and wording: docs/scenarios.md. Phase 2: 1, 8, 9, 11, 12 and the correction guard.
Phase 3 (training): 6, 7, 7b, 7c, 7d, 10, 10b, 10c, 21-25. Phase 4 (food): 2, 3, 4, 4b, 5, 5b, 15-20.
Phase 5 (daily score): 14, 29, 30, 31. The rest come with Phase 6.
"""
import json
import os
import re
from datetime import time, timedelta

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from connectors.health import Health
from core import db, memory, score
from core.agent import EXPIRED_CHECKIN
from core.coach import VOICE, Coach
from core.reply_rules import violations
from core.llm import structured_call
from modules.nutrition import food, meals
from modules.nutrition.targets import dish_key
from modules.training import checkin
from modules.training.baseline import food_totals
from modules.training.module import TrainingModule
from modules.training.planner import LATE_FOLLOW_UP, LATE_WHY
from modules.training.tools import NO_OURA_MATCH
from tests.conftest import call, ideas, needs_fixtures, parsed_checkin, say, tool_results
from today import fixtures_now, food_on

pytestmark = needs_fixtures
live = pytest.mark.skipif(not os.getenv("RUN_LIVE_LLM"), reason="set RUN_LIVE_LLM=1 to call OpenAI")
GREETING_75 = "Hi! Readiness 75 today. Tell me what you ate, ask for a workout, or ask how your day is going."


def at(hour, minute=0):
    return fixtures_now(time(hour, minute))


@pytest.fixture
def coach(temp_db, fake_llm, empty_calendar):
    return Coach(InMemorySaver(), use_fixtures=True)


@pytest.fixture
def real_checkin(monkeypatch):
    """Live tests: the check-in answer is read by the real model (plan and food stay stubbed)."""
    monkeypatch.setattr(checkin, "structured_call", structured_call)


class Chat:
    """Sends messages and keeps a transcript (printed by live tests)."""

    def __init__(self, coach, user, model=None):
        self.coach, self.user, self.model, self.lines = coach, user, model, []

    def send(self, text, hour, minute=0, script=()):
        if self.model is not None:
            self.model.script.extend(script)
            seen_before = len(self.model.seen)
        reply = self.coach.handle_text(self.user, text, at(hour, minute))
        self.lines += [f"[{hour:02d}:{minute:02d}] you: {text}", f"        coach: {reply}"]
        if self.model is not None:   # a scripted tool call with wrong arguments must fail the test
            errors = [m.content for call_ in self.model.seen[seen_before:] for m in call_
                      if isinstance(m, ToolMessage) and m.status == "error"]
            assert not errors, errors
        return reply

    def print(self, title):
        print(f"\n--- {title} ---\n" + "\n".join(self.lines))


def free_form(reply: str) -> str:
    """The part of a reply the reply rules apply to (the expired check-in line is fixed code text)."""
    return reply.removesuffix(f"\n{EXPIRED_CHECKIN}")


def assert_rules(reply: str, tools: set[str] = frozenset({"log_food", "remember", "forget", "remove_food"})):
    assert violations(free_form(reply), set(tools)) == [], reply


def food_today(user):
    return food_on(user, at(12).date())


def facts(user):
    return [f["text"] for f in memory.facts(user)]


def humans_seen(model, call_index=-1):
    return [m.content for m in model.seen[call_index] if isinstance(m, HumanMessage)]


def system_seen(model, call_index=-1):
    return next(m.content for m in model.seen[call_index] if isinstance(m, SystemMessage))


# --- 1. greeting --------------------------------------------------------------------------

def test_1_hi_is_one_line_with_readiness_and_no_llm(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("hi", 9)
    assert reply == GREETING_75
    assert chat_model.seen == []


def test_1_greeting_without_readiness(coach, user, chat_model, monkeypatch):
    monkeypatch.setattr(Health, "readiness", lambda self, u, d: {"score": None})
    assert Chat(coach, user, chat_model).send("Hey!", 9).startswith("Hi! No readiness yet today.")


def test_1_greeting_with_more_goes_to_the_agent(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("hi, had 2 eggs", 9, script=[
        call("log_food", text="2 eggs", time_text=None), say("Logged 2 eggs: about 450 kcal, 20 g protein.")])
    assert reply == "Logged 2 eggs: about 450 kcal, 20 g protein."
    assert [f["raw_text"] for f in food_today(user)] == ["2 eggs"]


# --- 8. a check-in left open, then a new session ----------------------------------------------

def test_8_expired_checkin_gets_one_line_after_the_answer(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    question = chat.send("want to work out now", 7, 0, [call("start_checkin", request_text=None), say("")])
    assert "energy (1-5) and soreness (0-5)" in question
    assert db.pending_plan(1) is not None

    reply = chat.send("what's left to eat today", 7, 16, [
        call("get_today", include_health=False), say("About 2218 kcal and 126 g protein left today.")])
    assert reply == f"About 2218 kcal and 126 g protein left today.\n{EXPIRED_CHECKIN}"
    assert db.pending_plan(1) is None                                    # the old question is dropped
    assert humans_seen(chat_model) == ["what's left to eat today"]       # new session: no old history
    assert_rules(reply)


def test_8_answer_in_the_same_session_is_planned(coach, user, chat_model, fake_llm):
    fake_llm.checkin = parsed_checkin(4, 2, feeling="legs fine")
    chat = Chat(coach, user, chat_model)
    chat.send("want to work out now", 7, 0, [call("start_checkin", request_text=None), say("")])
    plan = chat.send("4 2 legs fine", 7, 2, [call("submit_checkin", answer="4 2 legs fine"),
                                             say("Here's your plan!")])
    assert plan.startswith("🔋 Readiness 75\n🏃 HIIT") and "Here's your plan!" not in plan   # sent as built
    assert EXPIRED_CHECKIN not in plan
    [planned] = db.plans_between(1, at(7).date(), at(7).date())
    assert (planned["energy"], planned["soreness"], planned["note"]) == (4, 2, "legs fine")


# --- 9. remember / what do you know / forget ----------------------------------------------------

def test_9_remember_list_forget(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    saved = chat.send("remember I eat eggs", 9, 0, [
        call("remember", fact="I eat eggs", kind="diet", start_text=None, end_text=None),
        say("Got it, I'll remember that you eat eggs.")])
    assert "I eat eggs" in facts(user)
    assert_rules(saved)

    known = chat.send("what do you know about me", 9, 30, [
        say("You're vegetarian and eat eggs, and you prefer Indian food.")])
    assert humans_seen(chat_model) == ["what do you know about me"]      # new session
    assert "I eat eggs" in system_seen(chat_model)                       # saved memory is in every turn
    assert_rules(known)

    egg_id = next(f["id"] for f in memory.facts(user) if f["text"] == "I eat eggs")
    gone = chat.send("forget the eggs thing", 9, 31, [call("forget", memory_ids=[egg_id]),
                                                      say('I removed "I eat eggs".')])
    assert facts(user) == ["Vegetarian, eats eggs", "Prefers Indian food"]
    assert "I eat eggs" in gone
    assert_rules(gone)


# --- 11. voice --------------------------------------------------------------------------------

def test_11_voice_gets_the_fixed_reply_without_llm(coach, user, chat_model):
    assert coach.handle_voice(user, at(9)) == VOICE
    assert chat_model.seen == []
    session = db.latest_chat_session(1)
    assert [m["text"] for m in db.session_messages(session["id"])] == ["[voice note]", VOICE]


# --- 12. reply rules on free-form replies -------------------------------------------------------

def test_12_a_broken_draft_is_rewritten_once(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("how hard can I go today?", 9, 0, [
        say("Today is a PUSH day, intensity 4 is fine. I synced your Oura data."),
        say("You're well recovered, so a hard session is fine today.")])
    assert reply == "You're well recovered, so a hard session is fine today."


def test_12_still_broken_after_the_rewrite_is_trimmed_by_code(coach, user, chat_model):
    rambling = " ".join(["Your readiness is good."] * 20) + " I'll resend the plan later."
    reply = Chat(coach, user, chat_model).send("how hard can I go today?", 9, 0, [say(rambling), say(rambling)])
    assert_rules(reply, set())
    assert "resend" not in reply


# --- correction guard (until edit_food exists) ----------------------------------------------

def test_remove_food_asks_when_several_entries_match(coach, user, chat_model):
    for hour in (13, 15):
        db.add_food(1, "2 slices of pizza", {"est_calories": 500, "est_protein_g": 20}, at(hour))
    first, second = (f["id"] for f in food_today(user))
    chat = Chat(coach, user, chat_model)
    reply = chat.send("remove the pizza", 16, 0, [
        call("remove_food", entry_ids=[first]),                          # not shown yet: refused
        call("list_food", day="today"),
        say("You have two pizza entries, at 1pm and 3pm. Which one should I remove?")])
    assert len(food_today(user)) == 2
    assert "Which one" in reply

    chat.send("the 3pm one", 16, 1, [call("remove_food", entry_ids=[second]), say("Removed the 3pm pizza.")])
    assert [f["id"] for f in food_today(user)] == [first]


# --- food (Phase 4) ----------------------------------------------------------------------------

POPPI = "poppi strawberry lemon soda small can"


def rows_now():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM food_log ORDER BY id")]


def test_2_a_correction_updates_the_same_row_with_their_numbers(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send(POPPI, 10, 0, [call("log_food", text=POPPI, time_text=None),
                             say("Logged poppi soda: about 450 kcal, 20 g protein.")])
    [logged] = rows_now()
    reply = chat.send("correction on that it is 3g fiber and 9g carbs. 30cal", 10, 2, [
        call("log_food", text="poppi soda, 3g fiber, 9g carbs, 30 cal", time_text=None),   # the model slips
        call("edit_food", entry_id=logged["id"], fiber_g=3, carbs_g=9, calories=30),
        say("Updated poppi soda: 30 kcal, 9 g carbs, 3 g fiber.")])
    [row] = rows_now()                                                            # refused, then edited in place
    assert row["id"] == logged["id"] and (row["est_fiber_g"], row["est_carbs_g"], row["est_calories"]) == (3, 9, 30)
    assert set(json.loads(row["user_numbers_json"])) == {"est_fiber_g", "est_carbs_g", "est_calories"}
    assert food_totals(food_today(user))["calories"] == 30
    assert facts(user) == ["Vegetarian, eats eggs", "Prefers Indian food"]        # must not: a note
    assert last_tool_result(chat_model, "log_food")["saved"] is False
    assert_rules(reply, {"edit_food"})


def test_3_a_new_description_re_estimates_the_same_row(coach, user, chat_model, fake_llm):
    fake_llm.food_queue = [{"est_protein_g": 60, "est_calories": 1500}, {"est_protein_g": 38, "est_calories": 1350}]
    chat = Chat(coach, user, chat_model)
    chat.send("I ate 5 slices of pizza at 3pm", 16, 0, [
        call("log_food", text="5 slices of pizza", time_text="3pm"), say("Logged 5 slices of pizza at 3pm.")])
    [logged] = rows_now()
    assert logged["timestamp"].startswith("2026-10-07T15:00")
    text = "it was cheese pizza with olives and onions, I don't think it's 60g protein. Edit that"
    reply = chat.send(text, 16, 5, [
        call("edit_food", entry_id=logged["id"], new_text="5 slices of cheese pizza with olives and onions",
             feedback="I don't think it's 60g protein"),
        say("Updated: 5 slices of cheese pizza with olives and onions, about 1350 kcal, 38 g protein.")])
    [row] = rows_now()
    assert row["id"] == logged["id"] and row["raw_text"] == "5 slices of cheese pizza with olives and onions"
    assert row["est_protein_g"] == 38 and row["timestamp"] == logged["timestamp"]
    assert "60g protein" in fake_llm.calls[-1][1]                                 # their doubt reached the estimate
    assert_rules(reply, {"edit_food"})


def test_4_remove_the_pizza_names_it_and_updates_totals(coach, user, chat_model):
    db.add_food(1, "chai", {"est_calories": 90, "est_protein_g": 3}, at(8))
    pizza = db.add_food(1, "2 slices of pizza", {"est_calories": 500, "est_protein_g": 20}, at(13))
    reply = Chat(coach, user, chat_model).send("remove the pizza", 16, 0, [
        call("list_food", day="today"), call("remove_food", entry_ids=[pizza["id"]]),
        say("Removed 2 slices of pizza (about 500 kcal).")])
    assert [f["raw_text"] for f in food_today(user)] == ["chai"]
    assert food_totals(food_today(user))["calories"] == 90
    assert "pizza" in reply and last_tool_result(chat_model, "remove_food")["removed"][0]["text"] == "2 slices of pizza"
    assert_rules(reply, {"remove_food"})


def test_4b_two_pizzas_and_the_model_guesses_nothing_is_removed(coach, user, chat_model):
    for hour in (13, 15):
        db.add_food(1, "2 slices of pizza", {"est_calories": 500, "est_protein_g": 20}, at(hour))
    first, second = (f["id"] for f in food_today(user))
    chat = Chat(coach, user, chat_model)
    reply = chat.send("remove the pizza", 16, 0, [
        call("list_food", day="today"), call("remove_food", entry_ids=[first, second]),   # the model guesses
        say("You have two pizza entries, at 1pm and 3pm. Which one should I remove?")])
    assert len(food_today(user)) == 2 and "Which one" in reply
    assert "didn't say which" in last_tool_result(chat_model, "remove_food")["reason"]
    chat.send("both", 16, 1, [call("remove_food", entry_ids=[first, second]), say("Removed both pizza entries.")])
    assert food_today(user) == []


def test_5_ideas_then_the_second_one_then_higher_protein_is_the_same_dish(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    ideas_reply = chat.send("simple dinner ideas, I have dal, rice and spinach", 19, 0, [
        call("suggest_meal", meal="dinner", ingredients=["dal", "rice", "spinach"], constraints="simple"), say("")])
    lines = ideas_reply.splitlines()
    assert lines[0].startswith("For dinner, about ") and lines[1].startswith("1. Dal palak with jeera rice")
    assert lines[3].startswith("2. Spinach moong dal khichdi") and "g (" in lines[4]   # grams + household measure
    dish = last_tool_result(chat_model, "suggest_meal")["options_in_order"][1]
    recipe = chat.send("give me the second one", 19, 1, [call("meal_recipe", dish=dish, change=None,
                                                              ingredients=["dal", "rice", "spinach"]), say("")])
    assert recipe.startswith("Spinach moong dal khichdi · about")
    revised = chat.send("make it higher protein", 19, 2, [call("meal_recipe", dish=dish, change="higher protein",
                                                               ingredients=None), say("")])
    assert revised.startswith("Spinach moong dal khichdi (higher protein)")
    assert plans_today(user) == [] and db.pending_plan(1) is None                  # must not: a workout plan


def test_5b_dinner_ideas_on_two_days_in_a_row_are_different_dishes(coach, user, chat_model, fake_llm):
    today_ = at(19).date()
    fake_llm.meal_queue = [ideas("Rajma chawal", "Dal palak with jeera rice"),     # day 1
                           ideas("Rajma chawal bowl", "Dal palak with rice"),       # day 2: both repeats
                           ideas("Chana masala with chapatis", "Vegetable pulao with raita")]
    chat = Chat(coach, user, chat_model)
    coach._fixture_day = today_ - timedelta(days=1)                               # yesterday evening
    chat.send("dinner ideas", 19, 0, [call("suggest_meal", meal="dinner", ingredients=None, constraints=None), say("")])
    first = last_tool_result(chat_model, "suggest_meal")["options_in_order"]
    coach._fixture_day = today_
    reply = chat.send("dinner ideas", 19, 0, [call("suggest_meal", meal="dinner", ingredients=None, constraints=None),
                                              say("")])
    second = last_tool_result(chat_model, "suggest_meal")["options_in_order"]
    assert {dish_key(d) for d in first}.isdisjoint(dish_key(d) for d in second)
    assert second == ["Chana masala with chapatis", "Vegetable pulao with raita"] and "Chana masala" in reply


def test_15_16_a_question_or_a_plan_to_eat_is_not_logged(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    answer = chat.send("should I have a Celsius right now?", 15, 0, [
        say("A Celsius has about 200 mg caffeine. Fine now, but skip it after 2pm if you want to sleep well.")])
    later = chat.send("I'm going to have pizza later", 15, 1, [say("Enjoy! Tell me once you've had it.")])
    assert rows_now() == []
    assert_rules(answer) and assert_rules(later)


def test_17_two_times_in_one_message_are_two_rows(coach, user, chat_model):
    Chat(coach, user, chat_model).send("coffee at 8 and a banana at 10:30", 11, 0, [
        call("log_food", text="coffee", time_text="at 8"), call("log_food", text="a banana", time_text="10:30"),
        say("Logged coffee at 8am and a banana at 10:30am.")])
    assert [(f["raw_text"], f["timestamp"][11:16]) for f in food_today(user)] == [("coffee", "08:00"),
                                                                                  ("a banana", "10:30")]


def test_18_eaten_together_is_one_row(coach, user, chat_model):
    Chat(coach, user, chat_model).send("2 eggs, toast and coffee for breakfast", 8, 0, [
        call("log_food", text="2 eggs, toast and coffee", time_text=None), say("Logged breakfast.")])
    assert [f["raw_text"] for f in food_today(user)] == ["2 eggs, toast and coffee"]


def test_19_food_at_0050_before_the_nights_sleep_counts_for_the_day_before(coach, user, chat_model, patch_oura):
    patch_oura(bedtime="01:30")                                                   # went to bed at 1:30am
    Health(use_fixtures=True).oura.raw(user, at(12).date(), now=at(0, 40))        # Oura synced (cached sleep)
    Chat(coach, user, chat_model).send("had a bowl of maggi", 0, 50, [
        call("log_food", text="a bowl of maggi", time_text=None), say("Logged a bowl of maggi.")])
    today_ = at(12).date()
    assert [f["raw_text"] for f in food_on(user, today_ - timedelta(days=1))] == ["a bowl of maggi"]
    assert food_on(user, today_) == []


def test_20_delete_the_last_two_removes_only_the_two_newest(coach, user, chat_model):
    oldest, middle, newest = (db.add_food(1, t, {"est_calories": 100}, at(h)) for t, h in
                              (("poha", 8), ("chai", 11), ("biscuits", 15)))
    Chat(coach, user, chat_model).send("delete the last two", 16, 0, [
        call("list_food", day="today"), call("remove_food", entry_ids=[middle["id"], newest["id"]]),
        say("Removed chai and biscuits.")])
    assert [f["id"] for f in food_today(user)] == [oldest["id"]]


def test_list_food_reads_any_past_day(coach, user, chat_model):
    db.add_food(1, "rajma chawal", {"est_calories": 600, "est_protein_g": 20}, at(13) - timedelta(days=2))
    chat = Chat(coach, user, chat_model)
    chat.send("what did I eat on Monday?", 9, 0, [call("list_food", day="Monday"), say("Rajma chawal, about 600 kcal.")])
    result = last_tool_result(chat_model, "list_food")
    assert result["day"] == "Monday Oct 5" and [e["text"] for e in result["entries"]] == ["rajma chawal"]
    chat.send("and on Oct 6?", 9, 1, [call("list_food", day="Oct 6"), say("Nothing logged on Oct 6.")])
    assert last_tool_result(chat_model, "list_food")["entries"] == []
    chat.send("and next Friday?", 9, 2, [call("list_food", day="next Friday"), say("That hasn't happened yet.")])
    assert "Could not read the day" in last_tool_result(chat_model, "list_food")["reason"]


# --- daily score (Phase 5) ---------------------------------------------------------------------

GUILT = re.compile(r"\b(missed|failed|fail|only|should have|bad|behind|lazy|disappointing)\b", re.I)


def score_turn(day=None):
    return [call("get_score", day=day), say("")]


def score_result(chat_model):
    return last_tool_result(chat_model, "get_score")


def part(result, name):
    return next(p for p in result["parts"] if p["name"] == name)


def test_14_hows_my_day_twice_gives_the_same_score_and_breakdown(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    chat.send("how's my day", 12, 0, [call("get_score", day=None), say("25 so far.")])
    first = score_result(chat_model)
    chat.send("how's my day", 12, 1, [call("get_score", day=None), say("25 so far.")])
    assert score_result(chat_model) == first
    assert first["headline"].endswith(" so far") and first["headline"].split()[0].isdigit()   # no average yet
    assert db.get_daily_score(1, at(12).date())["final"] is False


def test_29_no_food_logged_scores_nutrition_zero_and_says_not_logged(coach, user, chat_model):
    Chat(coach, user, chat_model).send("how's my day", 12, 0, [call("get_score", day=None),
                                                               say("A walk and some food would round today out.")])
    nutrition = part(score_result(chat_model), "nutrition")
    assert (nutrition["points"], nutrition["note"]) == (0, "not logged")
    assert "logging what you eat today" in score_result(chat_model)["still_helps_today"]


def test_30_under_the_calorie_floor_after_the_cutoff_scores_calories_zero(coach, user, chat_model):
    db.add_food(1, "toast", {"entry_type": "meal", "est_calories": 800, "est_protein_g": 126}, at(9))
    chat = Chat(coach, user, chat_model)
    chat.send("how's my day", 20, 0, score_turn())
    evening = part(score_result(chat_model), "nutrition")
    chat.send("how's my day", 21, 30, score_turn())
    night_ = part(score_result(chat_model), "nutrition")
    assert "well under your target" not in evening["note"] and "well under your target" in night_["note"]
    assert night_["points"] == 14                     # protein full (40% of 30 = 12) + 1 of 3 meals (2); calories 0
    assert score_result(chat_model)["final"] is True


def test_31_after_the_cutoff_food_does_not_change_the_score(coach, user, chat_model):
    db.add_food(1, "dal rice", {"entry_type": "meal", "est_calories": 1200, "est_protein_g": 40}, at(13))
    chat = Chat(coach, user, chat_model)
    chat.send("how's my day", 20, 0, score_turn())
    chat.send("had a cookie", 21, 30, [call("log_food", text="a cookie", time_text=None),
                                       say("Logged a cookie: about 450 kcal, 20 g protein.")])
    final = db.get_daily_score(1, at(12).date())
    assert final["final"] and "a cookie" in [f["raw_text"] for f in food_today(user)]    # logged, not scored
    chat.send("how's my day", 21, 31, score_turn())
    assert score_result(chat_model)["parts"] == final["result"]["parts"]
    assert score_result(chat_model)["headline"].startswith(f"{final['score']} today")


def test_the_score_compares_to_their_own_7_day_average(coach, user, chat_model):
    for back, value in ((1, 20), (2, 22), (3, 24)):
        db.save_daily_score(1, at(12).date() - timedelta(days=back), {"score": value, "parts": []}, final=True)
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    Chat(coach, user, chat_model).send("how's my day", 12, 0, score_turn())
    headline_ = score_result(chat_model)["headline"]
    assert headline_.endswith(", up from your 22 average") and headline_.split()[0].isdigit()


# --- training (Phase 3): helpers --------------------------------------------------------------

QUESTION_7 = ("Readiness is 64 today, lower than usual, so I'd keep it light.\n"
              "I'd suggest a 30-min walk instead. Still want HIIT?")
PLAN_STARTS = "🔋 Readiness"


def plans_today(user):
    return db.plans_between(user.id, at(12).date(), at(12).date())


def last_tool_result(model, name):
    """The parsed JSON result of the latest call of tool `name` the scripted model was shown."""
    results = [m for call_ in model.seen for m in call_ if isinstance(m, ToolMessage) and m.name == name]
    return tool_results(results[-1:])[0]


def checkin_with_request(chat, request_text, answer, hour, minute=0):
    """"want X" → the question → their answer. Returns the reply to the answer."""
    chat.send(f"want {request_text}" if request_text else "want to work out now", hour, minute,
              [call("start_checkin", request_text=request_text), say("")])
    return chat.send(answer, hour, minute + 1, [call("submit_checkin", answer=answer), say("")])


# --- 6. two blocks from one check-in -----------------------------------------------------------

def test_6_run_plus_strength_is_one_plan_with_two_blocks_that_fits(coach, user, chat_model, fake_llm):
    fake_llm.checkin = parsed_checkin(5, 0, blocks=[("easy_run", None), ("strength", None)])
    reply = checkin_with_request(Chat(coach, user, chat_model), None, "5 0 give me run + strength", 20, 0)
    [planned] = plans_today(user)
    assert (planned["energy"], planned["soreness"]) == (5, 0)                     # soreness 0 accepted
    plan = planned["plan_json"]
    assert [b["workout_type"] for b in plan["blocks"]] == ["easy_run", "strength"]
    assert plan["time_slot"] == "20:01-21:00" and plan["duration_min"] == 59      # cut to fit before 21:00
    assert reply.startswith(PLAN_STARTS) and "Easy run 40 min + 🏋️ Strength (Push) 19 min" in reply


# --- 7. a request the rules would change: one question ------------------------------------------

def ask_for_hiit(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura(readiness=64)
    fake_llm.checkin = parsed_checkin(4, 1, blocks=[("HIIT", None)])
    chat = Chat(coach, user, chat_model)
    question = checkin_with_request(chat, "HIIT", "4 1", 7)
    assert question == QUESTION_7
    assert fake_llm.kinds() == ["checkin"]                                        # no plan call before asking
    return chat


def test_7_yes_plans_hiit_and_records_the_skipped_rules(coach, user, chat_model, fake_llm, patch_oura):
    chat = ask_for_hiit(coach, user, chat_model, fake_llm, patch_oura)
    reply = chat.send("yes", 7, 3, [call("confirm_override", choice="yes"), say("")])
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] == "HIIT" and reply.startswith(PLAN_STARTS)
    assert {o["skipped"] for o in planned["override_reasons"] if o["rule"] == "user_choice"} == {"readiness_floor"}


def test_7b_no_plans_the_suggestion_without_a_second_question(coach, user, chat_model, fake_llm, patch_oura):
    chat = ask_for_hiit(coach, user, chat_model, fake_llm, patch_oura)
    reply = chat.send("no", 7, 3, [call("confirm_override", choice="no"), say("")])
    [planned] = plans_today(user)
    assert (planned["plan_json"]["workout_type"], planned["plan_json"]["duration_min"]) == ("walk", 30)
    assert reply.startswith(PLAN_STARTS) and "Still want" not in reply
    assert "Why: Readiness is 64 today, lower than usual, so I'd keep it light." in reply
    assert "plan" not in fake_llm.kinds()                                        # the suggestion as offered
    assert db.pending_confirm(1) is None


def test_7c_yes_twenty_minutes_later_in_a_new_session_is_still_answered(coach, user, chat_model, fake_llm,
                                                                         patch_oura):
    chat = ask_for_hiit(coach, user, chat_model, fake_llm, patch_oura)
    first_session = db.latest_chat_session(1)["id"]
    reply = chat.send("yes", 7, 21, [call("confirm_override", choice="yes"), say("")])
    assert db.latest_chat_session(1)["id"] != first_session                      # 20 min later: new session
    assert "Waiting for yes or no" in system_seen(chat_model, -2)                 # the question is in its context
    assert reply.startswith(PLAN_STARTS) and EXPIRED_CHECKIN not in reply
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] == "HIIT"


EXPIRED_QUESTION = 'That question expired, so nothing was planned. Say "want to work out" to plan one now.'


def test_7d_an_unanswered_question_expires_with_no_plan_and_no_score(coach, user, chat_model, fake_llm, patch_oura):
    chat = ask_for_hiit(coach, user, chat_model, fake_llm, patch_oura)
    reply = chat.send("yes", 9, 5, [call("confirm_override", choice="yes"), say("")])    # 2 h 4 min later
    assert reply == EXPIRED_QUESTION
    with db.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT status FROM plans")] == ["expired"]
    assert plans_today(user) == [] and "plan" not in fake_llm.kinds()
    assert "expired unanswered" in system_seen(chat_model)                        # the agent is told
    training = TrainingModule(Health(use_fixtures=True)).score(user, at(12).date())
    assert (training.fraction, training.note) == (0.0, "no workout")                  # same as never asking


def test_7_the_coach_cannot_answer_its_own_question(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura(readiness=64)
    fake_llm.checkin = parsed_checkin(4, 1, blocks=[("HIIT", None)])
    chat = Chat(coach, user, chat_model)
    chat.send("want HIIT", 7, 0, [call("start_checkin", request_text="HIIT"), say("")])
    question = chat.send("4 1", 7, 1, [call("submit_checkin", answer="4 1"),
                                       call("confirm_override", choice="yes"), say("")])   # the model slips
    assert question == QUESTION_7
    assert last_tool_result(chat_model, "confirm_override")["planned"] is False
    assert db.pending_confirm(1) is not None                                      # still their decision
    chat.send("yes", 7, 2, [call("confirm_override", choice="yes"), say("")])
    assert plans_today(user)[0]["plan_json"]["workout_type"] == "HIIT"


# --- 10. "done" = exactly what Oura recorded ------------------------------------------------------

def plan_at_noon(coach, user, chat_model, fake_llm):
    fake_llm.checkin = parsed_checkin(4, 1)
    fake_llm.plan = fake_llm.plan.model_copy(update={"time_slot": "12:00-12:45", "duration_min": 45})
    chat = Chat(coach, user, chat_model)
    checkin_with_request(chat, None, "4 1 fine", 12)
    return chat


def test_10_done_takes_oura_times_exactly(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura()
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    patch_oura(workouts=[("running", "12:12", "13:03")])                         # Oura syncs it after the run
    reply = chat.send("done", 13, 10, [call("mark_done", feeling=None), say("")])
    [planned] = plans_today(user)
    assert (planned["completed"], planned["done_by"]) == (1, "oura")
    assert (planned["actual_start"], planned["actual_end"]) == ("2026-10-07T12:12:00-05:00", "2026-10-07T13:03:00-05:00")
    result = last_tool_result(chat_model, "mark_done")
    assert result["done"] == [{"workout": "HIIT", "time": "12:12–1:03pm", "from_oura": True}]
    assert reply == "Marked done: HIIT 12:12–1:03pm (from Oura). How did it feel?"            # sent as built


def test_10c_done_and_a_question_keeps_the_confirmation_then_answers(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura()
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    answer = "Paneer bhurji with 2 rotis covers most of the protein you have left."
    reply = chat.send("done, what should I eat now?", 13, 10, [
        call("mark_done", feeling=None), call("get_today", include_health=False), say(answer)])
    confirmation = "Marked done: HIIT 12:12–1:03pm (from Oura). How did it feel?"
    assert reply == f"{confirmation}\n\n{answer}"                                     # code text first, as built
    assert_rules(reply.split("\n\n", 1)[1], {"mark_done"})


def test_10c_done_and_meal_ideas_need_no_extra_line(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura()
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("done, what should I eat now?", 13, 10, [
        call("mark_done", feeling=None), call("suggest_meal", meal=None, ingredients=None, constraints=None),
        say("Two good options above, pick one!")])
    confirmation, ideas_text = reply.split("\n\n")
    assert confirmation == "Marked done: HIIT 12:12–1:03pm (from Oura). How did it feel?"
    assert ideas_text.startswith("For your next meal, about ") and "pick one" not in reply


def test_10c_a_draft_that_repeats_the_confirmation_is_dropped(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura()
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("done", 13, 10, [call("mark_done", feeling="great"),
                                       say("Marked done: HIIT 12:12–1:03pm (from Oura).")])
    assert reply == "Marked done: HIIT 12:12–1:03pm (from Oura)."


def test_10b_done_without_an_oura_workout_asks_and_invents_nothing(coach, user, chat_model, fake_llm, patch_oura):
    patch_oura()
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    reply = chat.send("done", 13, 10, [call("mark_done", feeling=None), say("")])
    result = last_tool_result(chat_model, "mark_done")
    assert (result["done"], result["say"]) == ([], NO_OURA_MATCH)
    [planned] = plans_today(user)
    assert planned["completed"] is None and planned["blocks_done"] == {} and planned["actual_start"] is None
    assert reply == NO_OURA_MATCH

    chat.send("12:10 to 1, about 50 min", 13, 12, [
        call("add_manual_workout", workout_type="HIIT", minutes=50, start_time_text="12:10", feeling=None),
        say("Added: HIIT 12:10–1:00pm.")])
    [planned] = plans_today(user)
    assert planned["completed"] == 1 and planned["done_by"] == "chat"              # the time they gave
    assert planned["actual_start"] == "2026-10-07T12:10:00-05:00"


def test_10b_a_manual_strength_completes_only_the_strength_block(coach, user, chat_model, fake_llm):
    fake_llm.checkin = parsed_checkin(5, 0, blocks=[("easy_run", 20), ("strength", 30)])
    chat = Chat(coach, user, chat_model)
    checkin_with_request(chat, None, "5 0 20 min run then 30 min strength", 7)
    chat.send("did the strength part at 7:25", 8, 0, [
        call("add_manual_workout", workout_type="strength", minutes=30, start_time_text="7:25", feeling=None),
        say("Added: strength 7:25–7:55am.")])
    [planned] = plans_today(user)
    assert list(planned["blocks_done"]) == [1] and planned["blocks_done"][1]["start"].startswith("2026-10-07T07:25")
    assert planned["completed"] is None                                           # the run is still open


def test_a_manual_workout_without_minutes_gets_the_usual_length_for_its_type(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    chat.send("went for a walk at 6", 8, 0, [
        call("add_manual_workout", workout_type="walk", minutes=None, start_time_text="at 6", feeling=None),
        say("Added a walk at 6am.")])
    [walk] = Health(use_fixtures=True).manual.workouts(user, at(12).date())
    assert (walk["start"][11:16], walk["end"][11:16], walk["minutes"]) == ("06:00", "06:30", None)  # 30 min, as for plans

    fake_llm.checkin = parsed_checkin(5, 0, blocks=[("easy_run", 20), ("strength", 30)])
    checkin_with_request(chat, None, "5 0 20 min run then 30 min strength", 9)
    chat.send("did the strength", 10, 0, [
        call("add_manual_workout", workout_type="strength", minutes=None, start_time_text=None, feeling=None),
        say("Added strength.")])
    assert plans_today(user)[0]["blocks_done"][1]["minutes"] == 45                  # strength: 45 min


# --- 21-25. more training -------------------------------------------------------------------------

def test_21_how_hard_can_i_go_is_a_question_not_a_checkin(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("how hard can I go today?", 9, 0, [
        call("get_today", include_health=True), say("You're well recovered, so a hard session is fine today.")])
    assert db.pending_plan(1) is None and plans_today(user) == []
    assert_rules(reply)


def test_22_a_second_checkin_replaces_the_first_plan(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    checkin_with_request(chat, None, "4 2", 11, 0)
    checkin_with_request(chat, None, "4 2", 11, 20)
    with db.connect() as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM plans ORDER BY id")]
    assert statuses == ["abandoned", "planned"]
    assert len(db.open_plans(1, at(12).date())) == 1


def test_23_hiit_in_the_morning_then_evening_checkin_is_light_and_the_day_scores_full(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("did 40 min HIIT at 7", 8, 0, [
        call("add_manual_workout", workout_type="HIIT", minutes=40, start_time_text="at 7", feeling=None),
        say("Added: HIIT 7:00–7:40am.")])
    reply = checkin_with_request(chat, None, "4 2", 18)
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] in ("walk", "mobility")
    assert "you already trained today (40-min HIIT at 7:00am)" in reply           # the reason, in plain words
    training = TrainingModule(Health(use_fixtures=True)).score(user, at(12).date())
    assert (training.fraction, training.note) == (1.0, "40-min HIIT at 7:00am done")


def test_24_checkin_after_the_cutoff_is_rest_with_a_sleep_note_and_no_plan_call(coach, user, chat_model, fake_llm):
    reply = Chat(coach, user, chat_model).send("want to work out now", 22, 30, [
        call("start_checkin", request_text=None), say("")])
    assert "plan" not in fake_llm.kinds()
    assert f"Why: {LATE_WHY}" in reply and reply.endswith(LATE_FOLLOW_UP)
    assert db.pending_plan(1) is None


def test_25_skip_is_planned_and_labeled_skipped(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    reply = checkin_with_request(chat, None, "skip", 7)
    assert reply.startswith(PLAN_STARTS)
    assert fake_llm.kinds() == ["plan"]                                           # "skip" is read by code
    [planned] = plans_today(user)
    assert (planned["energy"], planned["soreness"], planned["details_json"]["checkin"]["label"]) == (None, None, "skipped")
    why = chat.send("why?", 7, 2, [call("explain_plan"), say("")])
    assert "Check-in: skipped" in why


# --- live: the real model ------------------------------------------------------------------------

@live
def test_live_1_greetings(coach, user):
    chat = Chat(coach, user)
    assert chat.send("hi", 9) == GREETING_75
    chat.send("hi, had 2 eggs", 9, 1)
    chat.print("scenario 1: greetings")
    assert [f["raw_text"].lower() for f in food_today(user)] and "egg" in food_today(user)[0]["raw_text"].lower()


@live
def test_live_8_expired_checkin(coach, user):
    chat = Chat(coach, user)
    question = chat.send("want to work out now", 7, 0)
    reply = chat.send("what's left to eat today", 7, 16)
    chat.print("scenario 8: expired check-in")
    assert "energy (1-5)" in question
    assert reply.endswith(f"\n{EXPIRED_CHECKIN}")
    assert db.pending_plan(1) is None
    assert_rules(reply, set())


@live
def test_live_9_memory(coach, user):
    chat = Chat(coach, user)
    chat.send("remember I eat eggs", 9, 0)
    saved = [f for f in facts(user) if f not in ("Vegetarian, eats eggs", "Prefers Indian food")]
    known = chat.send("what do you know about me", 9, 30)
    chat.send("forget the eggs thing", 9, 31)
    chat.print("scenario 9: remember / what do you know / forget")
    assert len(saved) == 1 and "egg" in saved[0].lower()
    assert "egg" in known.lower()
    assert facts(user) == ["Vegetarian, eats eggs", "Prefers Indian food"]
    for line in chat.lines[1::2]:
        assert_rules(line.split("coach: ", 1)[1])


@live
def test_live_11_voice(coach, user):
    assert coach.handle_voice(user, at(9)) == VOICE


@live
def test_live_12_free_form_replies_follow_the_rules(coach, user):
    chat = Chat(coach, user)
    for i, text in enumerate(["how hard can I go today?", "how did I sleep?", "what's left to eat today?",
                              "update from oura"]):
        chat.send(text, 9, i)
    chat.print("scenario 12: reply rules")
    for line in chat.lines[1::2]:
        assert_rules(line.split("coach: ", 1)[1], {"refresh_health"})
    assert "energy (1-5)" not in chat.lines[1]                          # a question, not a workout request


# --- live: food (Phase 4) -------------------------------------------------------------------------

FOOD_TOOLS = {"log_food", "edit_food", "remove_food", "remember", "forget"}


@pytest.fixture
def real_food(monkeypatch):
    """Live tests: real food estimates (the re-estimate after an edit is what's checked)."""
    monkeypatch.setattr(food, "structured_call", structured_call)


@pytest.fixture
def real_meals(monkeypatch):
    """Live tests: real meal ideas and recipes (variety and same-dish revisions are what's checked)."""
    monkeypatch.setattr(meals, "structured_call", structured_call)


def assert_food_replies(chat):
    for line in chat.lines[1::2]:
        reply = line.split("coach: ", 1)[1]
        if not reply.startswith("For ") and " · about " not in reply.splitlines()[0]:   # code-built meal texts
            assert_rules(reply, FOOD_TOOLS)


@live
def test_live_2_correction_edits_the_row(coach, user):
    chat = Chat(coach, user)
    chat.send(POPPI, 10, 0)
    chat.send("correction on that it is 3g fiber and 9g carbs. 30cal", 10, 2)
    chat.print("scenario 2: correction")
    [row] = rows_now()
    assert (row["est_fiber_g"], row["est_carbs_g"], row["est_calories"]) == (3, 9, 30)
    assert set(json.loads(row["user_numbers_json"])) == {"est_fiber_g", "est_carbs_g", "est_calories"}
    assert food_totals(food_today(user))["calories"] == 30
    assert facts(user) == ["Vegetarian, eats eggs", "Prefers Indian food"]
    assert_food_replies(chat)


@live
def test_live_3_edit_that_re_estimates(coach, user, real_food):
    chat = Chat(coach, user)
    chat.send("I ate 5 slices of pizza at 3pm", 16, 0)
    chat.send("it was cheese pizza with olives and onions, I don't think it's 60g protein. Edit that", 16, 5)
    chat.print("scenario 3: edit that")
    [row] = rows_now()
    assert "olive" in row["raw_text"].lower() and "5" in row["raw_text"] and row["timestamp"][11:16] == "15:00"
    assert row["est_protein_g"] < 60 and "est_protein_g" not in json.loads(row["user_numbers_json"])
    assert_food_replies(chat)


@live
def test_live_4_remove_the_pizza(coach, user):
    db.add_food(1, "chai", {"est_calories": 90, "est_protein_g": 3}, at(8))
    db.add_food(1, "2 slices of pizza", {"est_calories": 500, "est_protein_g": 20}, at(13))
    chat = Chat(coach, user)
    reply = chat.send("remove the pizza", 16, 0)
    chat.print("scenario 4: remove the pizza")
    assert [f["raw_text"] for f in food_today(user)] == ["chai"] and "pizza" in reply.lower()
    assert_food_replies(chat)


@live
def test_live_4b_two_pizzas_asks_which(coach, user):
    for hour in (13, 15):
        db.add_food(1, "2 slices of pizza", {"est_calories": 500, "est_protein_g": 20}, at(hour))
    chat = Chat(coach, user)
    reply = chat.send("remove the pizza", 16, 0)
    chat.print("scenario 4b: two pizzas")
    assert len(food_today(user)) == 2 and "?" in reply


@live
def test_live_5_ideas_recipe_and_higher_protein(coach, user, real_meals):
    chat = Chat(coach, user)
    first = chat.send("simple dinner ideas, I have dal, rice and spinach", 19, 0)
    recipe = chat.send("give me the second one", 19, 1)
    revised = chat.send("make it higher protein", 19, 2)
    chat.print("scenario 5: ideas, the second one, higher protein")
    options = [line.split(". ", 1)[1].split(" · ")[0] for line in first.splitlines() if line[:2] in ("1.", "2.")]
    assert first.startswith("For dinner") and len(options) == 2 and " g (" in first
    assert recipe.startswith(options[1]) and revised.startswith(f"{options[1]} (")
    assert plans_today(user) == []


@live
def test_live_5b_two_days_of_dinner_ideas_are_different(coach, user, real_meals):
    today_ = at(19).date()
    chat = Chat(coach, user)
    coach._fixture_day = today_ - timedelta(days=1)
    day1 = chat.send("dinner ideas", 19, 0)
    coach._fixture_day = today_
    day2 = chat.send("dinner ideas", 19, 0)
    chat.print("scenario 5b: dinner ideas two days in a row")
    dishes = lambda text: {dish_key(line.split(". ", 1)[1].split(" · ")[0])  # noqa: E731
                           for line in text.splitlines() if line[:2] in ("1.", "2.")}
    assert dishes(day1) and dishes(day2) and dishes(day1).isdisjoint(dishes(day2))


@live
def test_live_15_16_questions_and_later_are_not_logged(coach, user):
    chat = Chat(coach, user)
    chat.send("should I have a Celsius right now?", 15, 0)
    chat.send("I'm going to have pizza later", 15, 1)
    chat.print("scenarios 15-16: a question, a plan to eat")
    assert rows_now() == []
    assert_food_replies(chat)


@live
def test_live_17_18_times_and_together(coach, user):
    chat = Chat(coach, user)
    chat.send("coffee at 8 and a banana at 10:30", 11, 0)
    chat.send("2 eggs, toast and coffee for breakfast", 11, 1)
    chat.print("scenarios 17-18: two times, eaten together")
    times = sorted(f["timestamp"][11:16] for f in food_today(user))
    assert times[:2] == ["08:00", "10:30"] and len(times) == 3                   # 2 rows + 1 row
    assert_food_replies(chat)


@live
def test_live_19_after_midnight_before_bed(coach, user, patch_oura):
    patch_oura(bedtime="01:30")
    Health(use_fixtures=True).oura.raw(user, at(12).date(), now=at(0, 40))
    chat = Chat(coach, user)
    chat.send("had a bowl of maggi", 0, 50)
    chat.print("scenario 19: 00:50, before the night's sleep")
    assert len(food_on(user, at(12).date() - timedelta(days=1))) == 1 and food_on(user, at(12).date()) == []


@live
def test_live_20_delete_the_last_two(coach, user):
    for text, hour in (("poha", 8), ("chai", 11), ("biscuits", 15)):
        db.add_food(1, text, {"est_calories": 100}, at(hour))
    chat = Chat(coach, user)
    chat.send("delete the last two", 16, 0)
    chat.print("scenario 20: delete the last two")
    assert [f["raw_text"] for f in food_today(user)] == ["poha"]


@live
def test_live_list_food_any_day(coach, user):
    db.add_food(1, "rajma chawal", {"est_calories": 600, "est_protein_g": 20}, at(13) - timedelta(days=2))
    chat = Chat(coach, user)
    reply = chat.send("what did I eat on Monday?", 9, 0)
    chat.print("list_food: any day")
    assert "rajma" in reply.lower()
    assert_food_replies(chat)


# --- live: daily score (Phase 5) -------------------------------------------------------------------

def assert_kind_score_reply(reply, headline_):
    assert headline_.split(",")[0] in reply, reply                               # the code's number, as given
    assert not GUILT.search(reply), reply
    assert_rules(reply, set())


@live
def test_live_14_hows_my_day_twice(coach, user):
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    for back, value in ((1, 20), (2, 22), (3, 24)):
        db.save_daily_score(1, at(12).date() - timedelta(days=back), {"score": value, "parts": []}, final=True)
    chat = Chat(coach, user)
    first = chat.send("how's my day", 12, 0)
    second = chat.send("how's my day", 12, 1)
    chat.print("scenario 14: how's my day twice")
    stored = db.get_daily_score(1, at(12).date())
    headline_ = score.headline(stored["score"], False, 22, at(12).date(), at(12).date())
    assert_kind_score_reply(first, headline_) and assert_kind_score_reply(second, headline_)
    assert "up from your 22 average" in first


@live
def test_live_29_no_food(coach, user):
    chat = Chat(coach, user)
    reply = chat.send("how's my day", 12, 0)
    chat.print("scenario 29: no food logged")
    stored = db.get_daily_score(1, at(12).date())
    assert next(p for p in stored["result"]["parts"] if p["name"] == "nutrition")["note"] == "not logged"
    assert_kind_score_reply(reply, f"{stored['score']} so far")


@live
def test_live_30_31_under_the_floor_then_food_after_the_cutoff(coach, user):
    db.add_food(1, "toast", {"entry_type": "meal", "est_calories": 800, "est_protein_g": 126}, at(9))
    chat = Chat(coach, user)
    chat.send("had a cookie", 21, 30)
    reply = chat.send("how's my day", 21, 31)
    chat.print("scenarios 30-31: under the floor, then food after the cutoff")
    final = db.get_daily_score(1, at(12).date())
    nutrition = next(p for p in final["result"]["parts"] if p["name"] == "nutrition")
    assert final["final"] and "800 of 2218 kcal, well under your target" in nutrition["note"]   # the cookie isn't in
    assert len(food_today(user)) == 2
    assert_kind_score_reply(reply, f"{final['score']} today")


# --- live: training (Phase 3) ------------------------------------------------------------------

TRAINING_TOOLS = {"mark_done", "add_manual_workout", "log_food", "remember"}


def assert_free_form_rules(chat, tools=TRAINING_TOOLS):
    """Reply rules on every reply the model wrote (plans and questions built by code are sent as built)."""
    for line in chat.lines[1::2]:
        reply = line.split("coach: ", 1)[1]
        if not reply.startswith(PLAN_STARTS) and "energy (1-5)" not in reply and "Still want" not in reply:
            assert_rules(reply, tools)


@live
def test_live_6_run_plus_strength(coach, user, real_checkin):
    chat = Chat(coach, user)
    chat.send("want to work out now", 20, 0)
    reply = chat.send("5 0 give me run + strength", 20, 1)
    chat.print("scenario 6: run + strength")
    [planned] = plans_today(user)
    assert (planned["energy"], planned["soreness"]) == (5, 0)
    assert [b["workout_type"] for b in planned["plan_json"]["blocks"]] == ["easy_run", "strength"]
    assert planned["plan_json"]["time_slot"].split("-")[1] <= "21:00"                # fits before 21:00
    assert reply.startswith(PLAN_STARTS)


@live
@pytest.mark.parametrize("answer, workout", [("yes", "HIIT"), ("no", "walk")])
def test_live_7_one_question_then_yes_or_no(coach, user, real_checkin, patch_oura, answer, workout):
    patch_oura(readiness=64)
    chat = Chat(coach, user)
    chat.send("want HIIT", 7, 0)
    question = chat.send("4 1", 7, 1)
    reply = chat.send(answer, 7, 2)
    chat.print(f"scenario 7: recovery day + HIIT, then {answer}")
    assert "Still want HIIT?" in question
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] == workout
    assert reply.startswith(PLAN_STARTS) and "Still want" not in reply             # 7b: no second question
    assert_free_form_rules(chat)


@live
def test_live_7c_yes_in_a_new_session(coach, user, real_checkin, patch_oura):
    patch_oura(readiness=64)
    chat = Chat(coach, user)
    chat.send("want HIIT", 7, 0)
    chat.send("4 1", 7, 1)
    reply = chat.send("yes", 7, 21)
    chat.print("scenario 7c: yes 20 min later")
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] == "HIIT" and reply.startswith(PLAN_STARTS)


@live
def test_live_10_done_uses_oura_times(coach, user, real_checkin, patch_oura):
    patch_oura()
    chat = Chat(coach, user)
    chat.send("want to work out now", 12, 0)
    chat.send("4 1", 12, 1)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("done", 13, 10)
    chat.print("scenario 10: done, Oura recorded it")
    [planned] = plans_today(user)
    assert (planned["done_by"], planned["actual_start"], planned["actual_end"]) == (
        "oura", "2026-10-07T12:12:00-05:00", "2026-10-07T13:03:00-05:00")
    assert "12:12" in reply
    assert_free_form_rules(chat)


@live
def test_live_10c_done_and_what_to_eat(coach, user, real_checkin, patch_oura):
    patch_oura()
    chat = Chat(coach, user)
    chat.send("want to work out now", 12, 0)
    chat.send("4 1", 12, 1)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("done, what should I eat now?", 13, 10)
    chat.print("scenario 10c: done + what should I eat")
    confirmation, _, answer = reply.partition("\n\n")
    assert confirmation.startswith("Marked done: HIIT 12:12–1:03pm (from Oura).")       # exactly as built
    assert answer and "Marked done" not in answer                                     # the rest, on its own
    if not answer.startswith("For "):                                                 # not meal ideas built by code
        assert_rules(answer, {"mark_done"})


@live
def test_live_7d_question_expires(coach, user, real_checkin, patch_oura, fake_llm):
    patch_oura(readiness=64)
    chat = Chat(coach, user)
    chat.send("want HIIT", 7, 0)
    chat.send("4 1", 7, 1)
    reply = chat.send("yes", 9, 5)
    chat.print("scenario 7d: yes after the question expired")
    assert plans_today(user) == [] and "plan" not in fake_llm.kinds()
    assert "expired" in reply.lower() or "energy (1-5)" in reply


@live
def test_live_10b_done_without_oura(coach, user, real_checkin, patch_oura):
    patch_oura()
    chat = Chat(coach, user)
    chat.send("want to work out now", 12, 0)
    chat.send("4 1", 12, 1)
    reply = chat.send("done", 13, 10)
    chat.print("scenario 10b: done, Oura has nothing")
    [planned] = plans_today(user)
    assert planned["completed"] is None and planned["blocks_done"] == {}         # no invented times
    assert "time" in reply.lower() or "add" in reply.lower()
    assert_free_form_rules(chat)


@live
def test_live_21_how_hard_is_not_a_checkin(coach, user):
    chat = Chat(coach, user)
    chat.send("how hard can I go today?", 9, 0)
    chat.print("scenario 21: how hard can I go")
    assert db.pending_plan(1) is None and plans_today(user) == []
    assert_free_form_rules(chat, set())


@live
def test_live_22_second_checkin_replaces_the_first(coach, user, real_checkin):
    chat = Chat(coach, user)
    for minute in (0, 20):
        chat.send("want to work out now", 11, minute)
        chat.send("4 2", 11, minute + 1)
    chat.print("scenario 22: two check-ins")
    assert len(db.open_plans(1, at(12).date())) == 1 and len(plans_today(user)) == 1


@live
def test_live_23_morning_hiit_then_evening(coach, user, real_checkin):
    chat = Chat(coach, user)
    chat.send("did 40 min HIIT at 7", 8, 0)
    chat.send("want to work out now", 18, 0)
    reply = chat.send("4 2", 18, 1)
    chat.print("scenario 23: HIIT this morning, evening check-in")
    [planned] = plans_today(user)
    assert planned["plan_json"]["workout_type"] in ("walk", "mobility")
    assert "already trained today" in reply
    assert_free_form_rules(chat)


@live
def test_live_24_late_checkin(coach, user, fake_llm):
    chat = Chat(coach, user)
    reply = chat.send("want to work out now", 22, 30)
    chat.print("scenario 24: check-in at 22:30")
    assert "plan" not in fake_llm.kinds()
    assert LATE_WHY in reply


@live
def test_live_25_skip(coach, user, real_checkin):
    chat = Chat(coach, user)
    chat.send("want to work out now", 7, 0)
    reply = chat.send("skip", 7, 1)
    chat.print("scenario 25: skip")
    [planned] = plans_today(user)
    assert reply.startswith(PLAN_STARTS) and planned["details_json"]["checkin"]["label"] == "skipped"
