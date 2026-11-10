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
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from connectors.health import Health
from core import citations, db, memory, score
from core.agent import CONFIRM_PREVIOUS, EXPIRED_CHECKIN
from core.coach import VOICE, Coach
from core.reply_rules import GUILT, copied, topics, violations
from core.llm import structured_call
from modules.nutrition import food, meals
from modules.nutrition.targets import dish_key
from modules.training import checkin
from modules.training.baseline import food_totals
from modules.training.module import TrainingModule
from modules.training.planner import LATE_FOLLOW_UP, LATE_WHY
from modules.training.tools import NO_OURA_MATCH, RING_ONLY
from tests.conftest import (GSSI_URLS, call, core, ideas, judge, judge_pushback, live, needs_fixtures,
                            parsed_checkin, say, tool_results)
from today import fixtures_now, food_on

pytestmark = needs_fixtures
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
    assert "doesn't say which" in last_tool_result(chat_model, "remove_food")["reason"]
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
    assert last_tool_result(chat_model, "list_food")["could_not_read"]


# --- daily score (Phase 5) ---------------------------------------------------------------------

SCORE_GUILT = re.compile(r"\b(missed|failed|fail|only|should have|bad|behind|lazy|disappointing)\b", re.I)


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
    assert (nutrition["points"], nutrition["detail"]) == (0, "not logged")
    assert "logging what you eat today" in score_result(chat_model)["still_helps_today"]


def test_30_under_the_calorie_floor_after_the_cutoff_scores_calories_zero(coach, user, chat_model):
    db.add_food(1, "toast", {"entry_type": "meal", "est_calories": 800, "est_protein_g": 126}, at(9))
    chat = Chat(coach, user, chat_model)
    chat.send("how's my day", 20, 0, score_turn())
    evening = part(score_result(chat_model), "nutrition")
    chat.send("how's my day", 21, 30, score_turn())
    night_ = part(score_result(chat_model), "nutrition")
    assert "well under your target" not in evening["detail"] and "well under your target" in night_["detail"]
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
    assert score_result(chat_model)["parts"] == [{("detail" if k == "note" else k): v for k, v in p.items()}
                                                 for p in final["result"]["parts"]]
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


def last_tool_result_list(model, name):
    """The parsed JSON list the latest call of tool `name` returned."""
    results = [m for call_ in model.seen for m in call_ if isinstance(m, ToolMessage) and m.name == name]
    return json.loads(results[-1].content)


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
    assert (training.fraction, training.note) == (1.0, "Recovery rest — readiness was 64")  # same as never asking


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


def test_10b_the_model_cannot_add_the_workout_in_the_turn_the_offer_was_made(coach, user, chat_model, fake_llm,
                                                                            patch_oura):
    patch_oura()                                     # live 2026-11-10: "done" -> offer -> add 12:30-1:10pm by itself
    chat = plan_at_noon(coach, user, chat_model, fake_llm)
    reply = chat.send("done", 13, 10, [
        call("mark_done", feeling=None),
        call("add_manual_workout", workout_type="HIIT", minutes=40, start_time_text="12:30", feeling=None),
        say("Marked your 40-min HIIT done (12:30–1:10pm).")])
    assert last_tool_result(chat_model, "add_manual_workout")["added"] is False
    [planned] = plans_today(user)
    assert planned["completed"] is None and planned["blocks_done"] == {}                 # no invented times
    assert reply.startswith(NO_OURA_MATCH) and "12:30" not in reply, reply


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


# --- 13. "update from oura" (Phase 6) ----------------------------------------------------------

def test_13_update_from_oura_with_nothing_new_says_so(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("hi", 9, 0)                                                         # Oura read once already
    reply = chat.send("update from oura", 9, 1, [call("refresh_health"),
                                                 say("Nothing new from Oura since this morning.")])
    assert last_tool_result(chat_model, "refresh_health")["anything_new"] is False
    assert "synced" not in reply.lower()
    assert_rules(reply, {"refresh_health"})


def test_13b_update_from_oura_with_a_new_workout_names_it(coach, user, chat_model, patch_oura):
    patch_oura()
    chat = Chat(coach, user, chat_model)
    chat.send("hi", 9, 0)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("update from oura", 13, 30, [call("refresh_health"),
                                                   say("Oura has a new run, 12:12–1:03pm.")])
    result = last_tool_result(chat_model, "refresh_health")
    assert result["anything_new"] is True
    assert {"activity": "running", "start": "12:12", "end": "13:03"} in result["oura_workouts_today"]
    assert_rules(reply, {"refresh_health"})


# --- memory (Phase 6) ----------------------------------------------------------------------------

def on_day(coach, offset):
    """Fixtures mode on another day: the fixtures' today + offset (Oura data exists up to today)."""
    coach._fixture_day = at(12).date() + timedelta(days=offset)


def test_26_a_dated_fact_shapes_the_plan_in_range_and_is_gone_after(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    on_day(coach, -2)                                                             # said on Monday
    chat.send("remember I'm traveling Tue-Thu, keep it light", 9, 0, [
        call("remember", fact="Traveling, keep it light", kind="context", start_text="Tue", end_text="Thu",
             keep_light="light"),
        say("Got it: traveling Tue–Thu, I'll keep it light.")])
    [trip] = [f for f in memory.facts(user) if f["kind"] == "context"]
    assert (trip["start_date"], trip["end_date"], trip["intensity_cap"]) == ("2026-10-06", "2026-10-08", 3)
    on_day(coach, 0)                                                              # Wednesday, during the trip
    reply = checkin_with_request(chat, None, "4 2", 7)
    assert "Traveling, keep it light" in fake_llm.calls[-1][1]                    # the plan sees the trip
    [planned] = plans_today(user)                                                 # the coach said HIIT; code decides
    assert (planned["plan_json"]["workout_type"], planned["plan_json"]["intensity"]) == ("easy_run", 3)
    assert "context_cap" in {o["rule"] for o in planned["override_reasons"]}
    assert "you noted “Traveling, keep it light”" in reply
    on_day(coach, 2)                                                              # Friday, after it
    chat.send("what do you know about me?", 9, 0, [call("list_memory"),
                                                    say("You're vegetarian, eat eggs and prefer Indian food.")])
    assert "Traveling" not in system_seen(chat_model)
    assert [f["fact"] for f in last_tool_result_list(chat_model, "list_memory")] == [
        "Vegetarian, eats eggs", "Prefers Indian food"]                           # not listed either


def test_27_a_fact_without_days_is_not_saved_with_made_up_dates(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("remember I'm away sometime soon", 9, 0, [
        call("remember", fact="Away", kind="context", start_text="sometime soon", end_text=None),
        say("Which days will you be away?")])
    assert last_tool_result(chat_model, "remember")["saved"] is False
    assert facts(user) == ["Vegetarian, eats eggs", "Prefers Indian food"] and "?" in reply


def test_28_forget_my_diet_stuff_asks_before_removing_anything(coach, user, chat_model):
    memory.remember(user, "No mushrooms", "diet")
    ids = [f["id"] for f in memory.facts(user) if f["kind"] == "diet"]
    chat = Chat(coach, user, chat_model)
    reply = chat.send("forget my diet stuff", 9, 0, [
        call("forget", memory_ids=ids),                                           # the model guesses: refused
        say('You have two diet facts: "Vegetarian, eats eggs" and "No mushrooms". Which one, or both?')])
    assert len(facts(user)) == 3 and "Which one" in reply
    chat.send("both", 9, 1, [call("forget", memory_ids=ids), say("Removed both diet facts.")])
    assert facts(user) == ["Prefers Indian food"]


# --- 32. two people, separate data ------------------------------------------------------------------

def test_32_user_2_sees_only_their_own_food_and_facts(coach, user, chat_model):
    from core import users
    sam = users.get(db.add_user("222", "Sam"))
    db.add_food(1, "poha", {"est_calories": 300, "est_protein_g": 8}, at(8))
    db.add_food(sam.id, "idli sambar", {"est_calories": 350, "est_protein_g": 10}, at(8))
    memory.remember(sam, "Allergic to peanuts", "diet")
    chat = Chat(coach, sam, chat_model)
    chat.send("what did I eat today?", 12, 0, [call("list_food", day="today"), say("Idli sambar at 8am.")])
    assert [e["text"] for e in last_tool_result(chat_model, "list_food")["entries"]] == ["idli sambar"]
    chat.send("what do you know about me?", 12, 1, [say("You're allergic to peanuts.")])
    prompt = system_seen(chat_model)
    saved = prompt[prompt.index("Saved facts"):prompt.index("\nTraining:")]           # the facts section
    assert "Allergic to peanuts" in saved and "Vegetarian" not in saved and "Indian" not in saved
    assert "poha" not in prompt
    assert db.latest_chat_session(1) is None                                      # user 1's sessions untouched


# --- 33-37. the score without data, on a low-readiness day, and inferred rest days ------------------

def set_readiness(raw, day, score_):
    next(r for r in raw["daily_readiness"]["data"] if r["day"] == day)["score"] = score_


def test_33_no_wearable_data_scores_only_what_there_is(coach, user, chat_model, patch_oura):
    raw, day = patch_oura(), at(12).date().isoformat()
    for endpoint in ("daily_activity", "daily_readiness", "daily_sleep", "workout", "sleep"):
        raw[endpoint]["data"] = [r for r in raw[endpoint]["data"] if r["day"] != day]
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    Chat(coach, user, chat_model).send("how's my day", 12, 0, score_turn())
    result = score_result(chat_model)
    assert [p["name"] for p in result["parts"]] == ["nutrition"] and result["parts"][0]["of"] == 100
    assert len(result["left_out"]) == 3


def test_34_low_readiness_with_no_hard_workout_is_not_held_against_them(coach, user, chat_model, patch_oura):
    patch_oura(readiness=64)
    Chat(coach, user, chat_model).send("how's my day", 21, 30, score_turn())
    result = score_result(chat_model)
    assert "took it easy on a low-readiness day" in part(result, "recovery")["detail"]
    assert part(result, "training")["detail"] == "Recovery rest — readiness was 64"


def test_35_one_rest_day_within_the_weekly_budget_is_full_credit(coach, user, chat_model):
    # Oct 2 (readiness 59) was a recovery rest, so today is the week's only rest day.
    Chat(coach, user, chat_model).send("how's my day", 21, 30, score_turn())
    training = part(score_result(chat_model), "training")
    assert (training["points"], training["of"]) == (35, 35)
    assert training["detail"] == "Rest day — you've trained 5 of the last 7 days"


def test_36_a_second_rest_day_in_the_same_7_days_is_missed(coach, user, chat_model, patch_oura):
    set_readiness(patch_oura(), "2026-10-02", 80)                                 # Oct 2: an ordinary rest day
    Chat(coach, user, chat_model).send("how's my day", 21, 30, score_turn())
    training = part(score_result(chat_model), "training")
    assert training["points"] == 0
    assert training["detail"] == "no workout — 2 rest days in the last 7 (your week has 1)"


def test_37_a_low_readiness_rest_day_is_full_credit_without_using_the_budget(coach, user, chat_model,
                                                                              patch_oura):
    raw = patch_oura(readiness=64)
    set_readiness(raw, "2026-10-02", 80)                                          # budget already used
    Chat(coach, user, chat_model).send("how's my day", 21, 30, score_turn())
    training = part(score_result(chat_model), "training")
    assert (training["points"], training["detail"]) == (35, "Recovery rest — readiness was 64")


def test_38_a_travel_fact_makes_a_rest_day_a_recovery_rest(coach, user, chat_model, patch_oura):
    set_readiness(patch_oura(), "2026-10-02", 80)
    memory.remember(user, "Traveling, keep it light", "context", at(12).date(), at(12).date())
    Chat(coach, user, chat_model).send("how's my day", 21, 30, score_turn())
    assert part(score_result(chat_model), "training")["detail"] == "Recovery rest — you noted: Traveling, keep it light"


# --- live: the real model ------------------------------------------------------------------------

@core
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


@core
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


@core
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


@core
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


@core
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
    answer = chat.send("should I have a Celsius right now?", 15, 0)
    chat.send("I'm going to have pizza later", 15, 1)
    chat.print("scenarios 15-16: a question, a plan to eat")
    assert rows_now() == []
    assert_answered("should I have a Celsius right now?", answer)
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
    assert not SCORE_GUILT.search(reply), reply
    assert_rules(reply, set())


@core
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


@core
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


@core
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


@core
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
    reply = chat.send("how hard can I go today?", 9, 0)
    chat.print("scenario 21: how hard can I go")
    assert_answered("how hard can I go today?", reply)
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
    offer = chat.send("did 40 min HIIT at 7", 8, 0)
    if "Want me to add" in offer:                     # M4 A4: Oura doesn't have it, so it offers to add it
        chat.send("yes", 8, 1)
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


# --- live: Phase 6 scenarios ---------------------------------------------------------------------

@pytest.fixture
def real_plan(monkeypatch):
    """Live tests: the real plan LLM (whether a plan respects a saved fact is what's checked)."""
    from modules.training import planner
    monkeypatch.setattr(planner, "structured_call", structured_call)


def assert_answered(message, reply):
    verdict = judge(message, reply)
    assert verdict.answered, f"{verdict.reason}\n{reply}"


@live
@core
def test_live_13_update_from_oura_nothing_new(coach, user):
    chat = Chat(coach, user)
    chat.send("hi", 9, 0)
    reply = chat.send("update from oura", 9, 1)
    chat.print("scenario 13: update from oura, nothing new")
    assert "synced" not in reply.lower()
    assert re.search(r"nothing new|no new|no updates|nothing has changed", reply, re.I), reply
    assert_rules(reply, {"refresh_health"})


@live
def test_live_13b_update_from_oura_new_workout(coach, user, patch_oura):
    patch_oura()
    chat = Chat(coach, user)
    chat.send("hi", 9, 0)
    patch_oura(workouts=[("running", "12:12", "13:03")])
    reply = chat.send("update from oura", 13, 30)
    chat.print("scenario 13b: update from oura, a new workout")
    assert "run" in reply.lower() and "12:12" in reply
    assert_rules(reply, {"refresh_health"})


@live
def test_live_26_dated_trip(coach, user, real_plan):
    chat = Chat(coach, user)
    on_day(coach, -2)
    chat.send("remember I'm traveling Tue-Thu, keep it light", 9, 0)
    on_day(coach, 0)
    chat.send("want to work out now", 7, 0)
    plan_reply = chat.send("4 2", 7, 1)
    on_day(coach, 2)
    later = chat.send("what do you know about me?", 9, 0)
    chat.print("scenario 26: traveling Tue-Thu")
    [trip] = [f for f in memory.facts(user) if f["kind"] == "context"]
    assert (trip["start_date"], trip["end_date"]) == ("2026-10-06", "2026-10-08")
    [planned] = plans_today(user)
    assert planned["plan_json"]["intensity"] <= 3                                # light
    assert re.search(r"travel|trip", plan_reply, re.I)                           # and it says why
    assert not re.search(r"travel|trip", later, re.I)                            # gone after the trip


@live
def test_live_27_away_sometime_soon(coach, user):
    chat = Chat(coach, user)
    reply = chat.send("remember I'm away sometime soon", 9, 0)
    chat.print("scenario 27: away sometime soon")
    assert not [f for f in memory.facts(user) if f["start_date"]]                # no made-up dates
    assert "?" in reply
    assert_rules(reply, {"remember"})


@live
def test_live_28_forget_my_diet_stuff(coach, user):
    memory.remember(user, "No mushrooms", "diet")
    chat = Chat(coach, user)
    reply = chat.send("forget my diet stuff", 9, 0)
    chat.print("scenario 28: forget my diet stuff")
    assert len(facts(user)) == 3                                                 # nothing removed yet
    assert "mushroom" in reply.lower() and "vegetarian" in reply.lower()         # lists both


@live
@core
def test_live_32_two_people(coach, user):
    from core import users
    sam = users.get(db.add_user("222", "Sam"))
    db.add_food(1, "poha", {"est_calories": 300, "est_protein_g": 8}, at(8))
    db.add_food(sam.id, "idli sambar", {"est_calories": 350, "est_protein_g": 10}, at(8))
    memory.remember(sam, "Allergic to peanuts", "diet")
    chat = Chat(coach, sam)
    ate = chat.send("what did I eat today?", 12, 0)
    known = chat.send("what do you know about me?", 12, 1)
    chat.print("scenario 32: user 2")
    assert "idli" in ate.lower() and "poha" not in ate.lower()
    assert "peanut" in known.lower() and "vegetarian" not in known.lower() and "indian" not in known.lower()


@live
def test_live_33_no_wearable(coach, user, patch_oura):
    raw, day = patch_oura(), at(12).date().isoformat()
    for endpoint in ("daily_activity", "daily_readiness", "daily_sleep", "workout", "sleep"):
        raw[endpoint]["data"] = [r for r in raw[endpoint]["data"] if r["day"] != day]
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    chat = Chat(coach, user)
    reply = chat.send("how's my day", 12, 0)
    chat.print("scenario 33: no wearable data")
    stored = db.get_daily_score(1, at(12).date())
    assert [p["name"] for p in stored["result"]["parts"]] == ["nutrition"]
    assert_kind_score_reply(reply, f"{stored['score']} so far")


@live
def test_live_34_37_a_low_readiness_rest_day(coach, user, patch_oura):
    raw = patch_oura(readiness=64)
    set_readiness(raw, "2026-10-02", 80)
    chat = Chat(coach, user)
    reply = chat.send("how's my day", 21, 30)
    chat.print("scenarios 34 and 37: a low-readiness rest day, budget already used")
    stored = db.get_daily_score(1, at(12).date())
    training = next(p for p in stored["result"]["parts"] if p["name"] == "training")
    assert training["note"] == "Recovery rest — readiness was 64" and training["points"] == training["of"]
    assert_kind_score_reply(reply, f"{stored['score']} today")


@live
def test_live_35_rest_day_within_budget(coach, user):
    chat = Chat(coach, user)
    reply = chat.send("how's my day", 21, 30)
    chat.print("scenario 35: one rest day within the budget")
    stored = db.get_daily_score(1, at(12).date())
    assert next(p for p in stored["result"]["parts"] if p["name"] == "training")["note"].startswith("Rest day")
    assert_kind_score_reply(reply, f"{stored['score']} today")


@live
def test_live_36_second_rest_day(coach, user, patch_oura):
    set_readiness(patch_oura(), "2026-10-02", 80)
    chat = Chat(coach, user)
    reply = chat.send("how's my day", 21, 30)
    chat.print("scenario 36: a second rest day in the week")
    stored = db.get_daily_score(1, at(12).date())
    assert next(p for p in stored["result"]["parts"] if p["name"] == "training")["points"] == 0
    assert_kind_score_reply(reply, f"{stored['score']} today")


# --- M4 Part A: history, a "yes" across sessions, general questions, workouts Oura can't see ---------

def eat(day_offset: int, protein: float, hour=13, calories=600, text="dal and rice"):
    db.add_food(1, text, {"entry_type": "meal", "est_calories": calories, "est_protein_g": protein},
                at(hour) - timedelta(days=-day_offset))


def a_week_of_dal():
    """M4-1: 7 days of food, 20-26 g protein (161 g in all, 23 g a day)."""
    for back in range(7):
        eat(-back, 20 + back)


def test_m4_1_protein_over_the_last_week_is_the_codes_sum(coach, user, chat_model):
    a_week_of_dal()
    Chat(coach, user, chat_model).send("How much protein did I have over the last week?", 18, 0, [
        call("food_totals", nutrients=["protein_g"], days_back=7), say("161 g protein in the last 7 days, about 23 g a day.")])
    result = last_tool_result(chat_model, "food_totals")
    assert (result["total"]["protein_g"], result["average_per_logged_day"]["protein_g"]) == (161, 23)
    assert result["days"] == 7 and result["range"] == "Thu Oct 1 to Wed Oct 7"


def test_m4_2_summarize_a_day(coach, user, chat_model):
    db.add_food(1, "rajma chawal", {"entry_type": "meal", "est_calories": 650, "est_protein_g": 22}, at(13) - timedelta(days=1))
    Chat(coach, user, chat_model).send("Summarize Oct 6", 9, 0, [
        call("day_summary", day_text="Oct 6"), say("Oct 6: about 650 kcal, a short strength session, readiness 86.")])
    result = last_tool_result(chat_model, "day_summary")
    assert (result["day"], result["food"]["kcal"], result["readiness"]) == ("Tue Oct 6", 650, 86)
    assert [w["workout"] for w in result["training"]["workouts"]] == ["Strength"]


def an_old_question(hours_ago: float, text="Logged the smoothie.\nWant an estimate for that smoothie?"):
    asked = at(8) - timedelta(hours=hours_ago)
    session = db.add_chat_session(1, asked, "session-1-old")
    db.add_message(1, session["id"], asked, "assistant", text)


def test_m4_3_a_bare_yes_the_next_morning_is_checked_first(coach, user, chat_model):
    an_old_question(hours_ago=20 + 52 / 60)                                       # yesterday 11:08am
    chat = Chat(coach, user, chat_model)
    reply = chat.send("Yes", 8, 0)
    assert reply == CONFIRM_PREVIOUS.format(when="yesterday at 11:08am", question="Want an estimate for that smoothie?")
    assert chat_model.seen == [] and db.pending_plan(1) is None                   # no model call, no check-in
    chat.send("yes, the smoothie", 8, 1, [say("A banana-berry smoothie is about 250 kcal and 5 g protein.")])
    assert "Want an estimate for that smoothie?" in system_seen(chat_model)       # the old question is in view


def test_m4_3_a_yes_within_12_hours_goes_to_the_agent_with_the_question(coach, user, chat_model):
    an_old_question(hours_ago=10)
    Chat(coach, user, chat_model).send("yes", 8, 0, [say("A banana-berry smoothie is about 250 kcal.")])
    assert "Your last message before this conversation" in system_seen(chat_model)


def test_m4_3_an_old_question_is_not_shown_after_24_hours(coach, user, chat_model):
    an_old_question(hours_ago=25)
    Chat(coach, user, chat_model).send("yes", 8, 0, [say("Yes to what? Tell me what you'd like.")])
    assert "Your last message before this conversation" not in system_seen(chat_model)


def test_m4_4_a_general_question_never_offers_to_look_it_up(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("How much caffeine is in a 12 oz Celsius?", 10, 0, [
        say("About 200 mg (approximate). Want me to look it up?"),
        say("About 200 mg of caffeine in a 12 oz Celsius (approximate).")])           # the rewrite
    assert reply == "About 200 mg of caffeine in a 12 oz Celsius (approximate)."
    assert food_today(user) == []


def test_m4_5_a_workout_oura_never_saw_gets_the_ring_note_and_an_offer(coach, user, chat_model, patch_oura):
    patch_oura()
    chat = Chat(coach, user, chat_model)
    reply = chat.send("I did 49 min HIIT at 10:37", 12, 0, [
        call("mark_done", feeling=None, workout_type="HIIT", minutes=49, start_time_text="10:37"), say("")])
    assert reply == f"Oura doesn't have it. {RING_ONLY} Want me to add 49 min of HIIT at 10:37am?"
    chat.send("yes", 12, 1, [call("add_manual_workout", workout_type="HIIT", minutes=49, start_time_text="10:37",
                                  feeling=None), say("Added: HIIT 10:37–11:26am.")])
    [w] = Health(use_fixtures=True).manual.workouts(user, at(12).date())
    assert (w["workout_type"], w["start"][11:16], w["minutes"]) == ("HIIT", "10:37", 49)


def test_m4_5_oura_has_it_says_so_and_adds_nothing(coach, user, chat_model, patch_oura):
    patch_oura(workouts=[("running", "10:37", "11:20")])
    reply = Chat(coach, user, chat_model).send("I ran at 10:37", 12, 0, [
        call("mark_done", feeling=None, workout_type="easy_run", minutes=None, start_time_text="10:37"), say("")])
    assert reply == "Oura has it: run 10:37–11:20am."
    assert Health(use_fixtures=True).manual.workouts(user, at(12).date()) == []


def test_m4_6_the_lowest_protein_day_this_week(coach, user, chat_model):
    eat(-2, 30)
    eat(-1, 15)                                                                   # Tuesday: the lowest
    eat(0, 5)                                                                     # today, still going
    Chat(coach, user, chat_model).send("Which day this week was I lowest on protein?", 18, 0, [
        call("food_totals", nutrients=["protein_g"], start_text="this week"), say("Tuesday, with 15 g.")])
    result = last_tool_result(chat_model, "food_totals")
    assert result["lowest_day"]["protein_g"] == {"day": "Tue Oct 6", "value": 15}
    assert result["today_so_far"]["protein_g"] == 5


def test_m4_7_how_did_i_sleep_this_week(coach, user, chat_model):
    Chat(coach, user, chat_model).send("How did I sleep this week?", 9, 0, [
        call("health_range", metrics=["sleep_score", "sleep_hours"], start_text="this week"),
        say("Sleep score averaged 78 this week, best on Tuesday (90).")])
    result = last_tool_result(chat_model, "health_range")
    assert [d["sleep_score"] for d in result["per_day"]] == [67, 90, 78]           # Mon-Wed in the fixtures
    assert result["average"]["sleep_score"] == 78.3


def workouts_except_fridays(weeks=4):
    """Manual workouts every day of the last `weeks` weeks except Fridays (the fixtures have no Friday workout)."""
    for back in range(1, weeks * 7):
        day = at(7) - timedelta(days=back)
        if day.weekday() != 4:
            db.add_manual_workout(1, "strength", day, day + timedelta(minutes=40), 40, None)


def test_m4_7b_which_days_do_i_usually_skip(coach, user, chat_model):
    workouts_except_fridays()
    Chat(coach, user, chat_model).send("Which days do I usually skip workouts?", 9, 0, [
        call("workouts", start_text="last 4 weeks", group_by="weekday"), say("Fridays: no workout on any of the last 4.")])
    result = last_tool_result(chat_model, "workouts")
    assert result["by_weekday"]["most_often_without_a_workout"] == ["Friday"]
    assert result["by_weekday"]["weekdays"]["Friday"] == {"days": 4, "with_a_workout": 0, "without": 4,
                                                          "planned_but_not_done": 0}


def test_m4_7c_best_sleep_this_month_and_what_was_different(coach, user, chat_model):
    Chat(coach, user, chat_model).send("When did I sleep best this month, and what was different that day?", 9, 0, [
        call("health_range", metrics=["sleep_score"], start_text="this month", compare_days=True),
        say("Best on Tuesday (90): you went to bed much earlier than usual.")])
    best = last_tool_result(chat_model, "health_range")["best_and_worst"]["sleep_score"]["best"]
    assert (best["day"], best["value"]) == ("Tue Oct 6", 90)
    assert any("went to bed at 9:53pm" in d and "earlier than usual" in d for d in best["what_was_different"])


PUSHBACK = {
    "M4-8": ("I'll do HIIT twice today to make up for yesterday",
             "Two HIIT sessions in one day mostly adds fatigue. One good session now, or HIIT plus an easy walk "
             "tonight. Which one?"),
    "M4-9": ("Should I skip dinner to hit my deficit?",
             "I wouldn't: skipping dinner usually backfires and costs protein. A lighter dal and veg dinner keeps you "
             "on target. Want ideas for that?"),
    "M4-10": ("Going for a 2-hour run after 5 hours of sleep",
              "Five hours is short, so two hours will feel rough. An easy 45 minutes, or the long run tomorrow after "
              "a full night. Your call?"),
}


@pytest.mark.parametrize("scenario", sorted(PUSHBACK))
def test_m4_8_9_10_pushback_is_kind_with_options(coach, user, chat_model, scenario):
    message, draft = PUSHBACK[scenario]
    reply = Chat(coach, user, chat_model).send(message, 9, 0, [say(draft)])
    assert reply == draft and reply.rstrip().endswith("?") and not GUILT.search(reply)
    assert_rules(reply)


@live
def test_live_m4_1_protein_over_the_last_week(coach, user):
    a_week_of_dal()
    reply = Chat(coach, user).send("How much protein did I have over the last week?", 18, 0)
    assert "161" in reply and "23" in reply                                      # total 161 g, 23 g a day


@live
def test_live_m4_2_summarize_a_day(coach, user):
    db.add_food(1, "rajma chawal", {"entry_type": "meal", "est_calories": 650, "est_protein_g": 22}, at(13) - timedelta(days=1))
    reply = Chat(coach, user).send("Summarize Oct 6", 9, 0)
    assert "650" in reply and "86" in reply                                     # food total and readiness that day


@live
def test_live_m4_3_a_yes_the_next_morning(coach, user):
    an_old_question(hours_ago=20 + 52 / 60, text="Want an estimate for that smoothie?")
    reply = Chat(coach, user).send("Yes", 8, 0)
    assert db.pending_plan(1) is None and ("estimate" in reply.lower() or "smoothie" in reply.lower())


@live
def test_live_m4_4_caffeine_in_a_celsius(coach, user):
    chat = Chat(coach, user)
    reply = chat.send("How much caffeine is in a 12 oz Celsius?", 10, 0)
    chat.print("scenario M4-4: caffeine in a Celsius")
    assert re.search(r"\b2\d\d\s*mg\b", reply) and not re.search(r"look (it )?up|brows|search", reply, re.I)
    assert food_today(user) == []


@live
def test_live_m4_5_a_workout_oura_never_saw(coach, user):
    chat = Chat(coach, user)
    reply = chat.send("I did 49 min HIIT at 10:37", 12, 0)
    chat.print("scenario M4-5: a workout Oura never saw")
    assert "ring" in reply.lower() and "nothing new came in" not in reply.lower()


@live
def test_live_m4_6_lowest_protein_day(coach, user):
    eat(-2, 30)
    eat(-1, 15)
    eat(0, 5)
    reply = Chat(coach, user).send("Which day this week was I lowest on protein?", 18, 0)
    assert re.search(r"tue", reply, re.I) and "15" in reply


@live
def test_live_m4_7_sleep_this_week(coach, user):
    reply = Chat(coach, user).send("How did I sleep this week?", 9, 0)
    assert re.search(r"\b(78|67|90)\b", reply), reply                          # numbers from health_range
    assert_answered("How did I sleep this week?", reply)


@live
def test_live_m4_7b_which_days_do_i_skip(coach, user):
    workouts_except_fridays()
    reply = Chat(coach, user).send("Which days do I usually skip workouts?", 9, 0)
    assert re.search(r"friday", reply, re.I), reply


@live
def test_live_m4_7c_best_sleep_and_what_was_different(coach, user):
    reply = Chat(coach, user).send("When did I sleep best this month, and what was different that day?", 9, 0)
    assert re.search(r"oct 6|tuesday|tue", reply, re.I) and re.search(r"bed|earlier", reply, re.I), reply


@live
@pytest.mark.parametrize("scenario", sorted(PUSHBACK))
def test_live_m4_8_9_10_pushback(coach, user, scenario):
    message = PUSHBACK[scenario][0]
    chat = Chat(coach, user)
    reply = chat.send(message, 9, 0)
    chat.print(f"scenario {scenario}: pushback")
    verdict = judge_pushback(message, reply)
    assert verdict.noticed and verdict.honest_call and verdict.options and verdict.their_choice, verdict.reason
    assert verdict.at_most_two_options, f"more than two options: {reply}"
    assert not verdict.guilt and not GUILT.search(reply), verdict.reason
    assert_rules(reply, set())


@live
def test_live_38_travel_rest_day(coach, user, patch_oura):
    set_readiness(patch_oura(), "2026-10-02", 80)
    chat = Chat(coach, user)
    chat.send("remember I'm traveling today and tomorrow, keep it light", 9, 0)
    reply = chat.send("how's my day", 21, 30)
    chat.print("scenario 38: a rest day while traveling")
    stored = db.get_daily_score(1, at(12).date())
    training = next(p for p in stored["result"]["parts"] if p["name"] == "training")
    assert training["note"].startswith("Recovery rest — you noted:") and training["points"] == training["of"]
    assert_kind_score_reply(reply, f"{stored['score']} today")


# --- every scenario in docs/scenarios.md has a scripted and a live test -------------------------------

SCENARIOS_DOC = Path(__file__).resolve().parent.parent / "docs" / "scenarios.md"
# scenario id -> (scripted test, live test). M4 scenarios have only a live test, marked xfail("M4").
# --- M4 Part D: GSSI knowledge and fueling --------------------------------------------------------

LONG_RUN_QUESTION = "My long run is Saturday. What should I eat and drink Friday and Saturday?"
COFFEE_QUESTION = "How long before a run should I have a coffee or energy drink?"
ELECTROLYTES_QUESTION = "Do I need electrolytes for a 10k or is water fine?"


def eat_carbs(day_offset: int, carbs: float):
    db.add_food(1, "rice and dal", {"entry_type": "meal", "est_calories": 600, "est_protein_g": 20,
                                    "est_carbs_g": carbs}, at(13) - timedelta(days=-day_offset))


def test_m4_11_long_run_fueling_is_the_codes_amounts_with_a_line_and_a_link(coach, user, chat_model, fake_gssi):
    for back, carbs in ((1, 200), (2, 160), (3, 180)):
        eat_carbs(-back, carbs)
    line = ("Readiness is 75 and the week was easy. Eating enough carbohydrate the day before a long run helps you "
            "feel stronger late in the run [1].")                       # what passage 1 says, in other words
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("get_today", include_health=True), call("workouts", days_back=7),
        call("search_gssi", query="carbohydrate the day before a long run", k=4),
        call("fueling_plan", day_text="Saturday"), say(line)])
    plan, own = reply.split("\n\n")
    own, source, amounts = own.split("\n")
    assert plan.splitlines()[:2] == [
        "🏃 Run Sat Oct 10 · about 37 min (your longest run in the last 4 weeks; tell me if it's longer)",
        "Fri: no extra carbs needed, just your usual meals with carbs; lately about 180 g a day (3 days logged)."]
    assert own == line and source == f"Source [1]: GSSI – Carbohydrate (test) <{GSSI_URLS['carbs']}>"
    assert amounts.startswith("Amounts: GSSI – ") and amounts.count(" · ") == 2      # one line, the other 3 articles
    assert fake_gssi == [("gssi", "search_gssi", {"query": "carbohydrate the day before a long run", "k": 4})]


def test_m4_11_a_marker_the_passage_does_not_support_is_removed_and_the_sentence_stays(coach, user, chat_model,
                                                                                      monkeypatch):
    line = "Readiness is 75 and the week was easy, so fuel up Friday and enjoy it [1]."    # about their day, not GSSI
    asked, scripted = [], citations.passage_states

    def check(claim, passages, user_id=None):
        asked.append(claim)
        return scripted(claim, passages, user_id)
    monkeypatch.setattr(citations, "passage_states", check)
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("search_gssi", query="carbohydrate the day before a long run", k=4),
        call("fueling_plan", day_text="Saturday"), say(line)])
    own = reply.split("\n\n")[1].splitlines()
    assert own[0] == "Readiness is 75 and the week was easy, so fuel up Friday and enjoy it."
    assert "Source" not in reply and own[1].startswith("Amounts: GSSI – Dietary Carbohydrate")   # amounts still cited
    assert asked == ["Readiness is 75 and the week was easy, so fuel up Friday and enjoy it"]   # one question


def test_m4_11_a_copied_line_is_dropped_and_the_link_is_still_there(coach, user, chat_model):
    copy = ("Runners who eat enough carbohydrate the day before a long run tend to feel stronger in the last "
            "part of the run.")
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("search_gssi", query="carbs before a long run", k=2), call("fueling_plan", day_text="Saturday"),
        say(copy), say(copy)])                                       # the rewrite copies again: trimmed by code
    assert copy not in reply and "\nAmounts: GSSI – Dietary Carbohydrate And Endurance Athletes <" in reply
    assert reply.startswith("🏃 Run Sat Oct 10")


def test_m4_12_coffee_before_a_run_is_timed_and_sized_for_their_weight(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 7, 0, [
        call("search_gssi", query="caffeine timing before exercise", k=3), call("fueling_plan", parts=["caffeine"]),
        say(f"A modest dose about an hour before helps many people. {GSSI_URLS['caffeine']}")])        # pasted url
    assert reply.startswith("Caffeine, if you use it: start low, 1.5–3 mg/kg → 100–210 mg; more doesn't help. Take "
                            "it about 60 min before the run")                                    # 69.9 kg
    assert reply.count("(using 70 kg from your Oura profile; tell me if that's off)") == 1    # said where, once
    assert "helps many people [3].\nSource [3]: GSSI – Caffeine (test) <" + GSSI_URLS["caffeine"] + ">" in reply
    assert reply.endswith("Amounts: GSSI – How Sleep Loss Undermines Nutritional Goals <https://www.gssiweb.org/en/"
                          "sports-science-exchange/article/fueling-performance-starts-with-sleep--how-sleep-loss-"
                          "undermines-nutritional-goals>")                                  # the timing's article
    assert "🏃" not in reply
    again = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 7, 30, [call("fueling_plan", parts=["caffeine"]),
                                                                        say("")])
    assert "100–210 mg" in again and "using 70 kg" not in again                     # once per weight, not each time


def test_m4_13_a_10k_drinks_to_thirst_and_says_there_is_no_weather_data(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(ELECTROLYTES_QUESTION, 7, 0, [
        call("search_gssi", query="electrolytes sodium shorter runs", k=3),
        call("fueling_plan", distance_km=10, parts=["fluids"]),
        say("Drinking to thirst works well on shorter runs in mild weather [2], so for a 10k water is plenty.")])
    assert reply.startswith("🏃 Run of 10 km · about 70 min (10 km at about 7 min per km; no usable run pace from Oura)\n"
                            "Drink: to thirst; water is fine")
    assert "no weather data" in reply and f"Source [2]: GSSI – Drinking (test) <{GSSI_URLS['drinking']}>" in reply


def test_m4_14_no_weight_means_per_kg_one_question_then_grams(coach, user, chat_model, patch_oura):
    patch_oura()["personal_info"]["weight"] = None
    chat = Chat(coach, user, chat_model)
    first = chat.send(LONG_RUN_QUESTION, 9, 0, [call("fueling_plan", day_text="Saturday"), say("Fuel up Friday.")])
    assert "1–1.2 g per kg an hour" in first and "Tell me your weight" in first
    again = chat.send("And for Sunday instead?", 9, 2, [call("fueling_plan", day_text="Sunday"), say("")])
    assert "g per kg" in again and "Tell me your weight" not in again                 # asked once only
    saved = chat.send("I'm 62 kg", 9, 5, [call("save_body_weight", weight=62, unit="kg"),
                                          say("Saved 62 kg. That's 1–4 g/kg → 62–248 g.")])   # its own math: dropped
    assert memory.body_weight(user) == 62 and "\n\nSaved 62 kg.\nAmounts: GSSI" in saved
    assert chat_model.tool_choices[-2:] == ["save_body_weight", None]   # code made it call the tool first (no math)
    assert "1–1.2 g/kg an hour → 60–75 g an hour" in saved and "62–248" not in saved          # redone by code
    grams = chat.send(LONG_RUN_QUESTION, 9, 8, [call("fueling_plan", day_text="Saturday"), say("")])
    assert "1–1.2 g/kg an hour → 60–75 g an hour" in grams
    assert "using 62 kg" not in saved + grams                     # they just told us: no "(using 62 kg …)" note


def test_m4_14_a_weight_answer_routed_elsewhere_still_saves_it_and_code_does_the_grams(coach, user, chat_model,
                                                                                      patch_oura, monkeypatch):
    patch_oura()["personal_info"]["weight"] = None
    chat = Chat(coach, user, chat_model)
    chat.send(LONG_RUN_QUESTION, 9, 0, [call("fueling_plan", day_text="Saturday"), say("Fuel up Friday.")])
    from core import classifier
    monkeypatch.setattr(classifier, "classify", lambda *a, **k: {"categories": ["memory"], "reason": "misrouted"})
    saved = chat.send("I'm 62 kg", 9, 2, [call("save_body_weight", weight=62, unit="kg"), say("Saved 62 kg.")])
    assert memory.body_weight(user) == 62 and chat_model.tool_choices[-2] == "save_body_weight"
    assert "1–1.2 g/kg an hour → 60–75 g an hour" in saved                     # redone by code, not the model


def test_m4_14_minutes_from_an_earlier_plan_are_not_theirs(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("fueling_plan", day_text="Saturday", minutes=37), say("")])
    assert "(your longest run in the last 4 weeks" in reply and "as you said" not in reply


def test_m4_13_the_same_plan_called_twice_is_sent_once(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(ELECTROLYTES_QUESTION, 7, 0, [
        call("fueling_plan", distance_km=10, parts=["fluids"]), call("fueling_plan", distance_km=10, parts=["fluids"]),
        say("")])
    assert reply.count("🏃 Run of 10 km") == 1


def test_m4_11_a_rewrite_that_talks_about_rewriting_is_not_sent(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 7, 0, [
        call("search_gssi", query="caffeine timing", k=3), call("fueling_plan", parts=["caffeine"]),
        say("Use the fueling_plan amounts about an hour out [3]."),               # a snake_case term: rewrite
        say("Please paste the reply you want rewritten and I'll fix it.")])
    assert "paste" not in reply and reply.startswith("Caffeine, if you use it:")


def test_m4_11_a_rewrite_next_to_the_plan_gets_the_same_guards(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("search_gssi", query="carbs before a long run", k=2), call("fueling_plan", day_text="Saturday"),
        say("Use the fueling_plan text [1]."),                                   # snake_case: rewrite
        say("Friday (day before): spread your carbs [1].\nKeep fat low at dinner [2].\nLight week, so go [1]. "
            "Sound good?")])
    own = reply.split("\n\n")[1].splitlines()[0]
    assert own == "Keep fat low at dinner. Light week, so go."     # no recap, no question, 2 at most, no unsupported [n]
    assert "Source" not in reply


def test_m4_11_an_own_line_about_a_topic_the_plan_lacks_is_rewritten_once_then_that_sentence_dropped(
        coach, user, chat_model):
    drift = "Readiness is 75 and your week was light. Stick to its caffeine timing."   # the plan has no caffeine line
    script = [call("search_gssi", query="carbs before a long run", k=2), call("fueling_plan", day_text="Saturday")]
    fixed = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, script + [
        say(drift), say("Readiness is 75 and your week was light, so drink to thirst.")])
    assert fixed.split("\n\n")[1].startswith("Readiness is 75 and your week was light, so drink to thirst.")
    trimmed = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 30, script + [say(drift), say(drift)])
    assert "caffeine" not in trimmed.lower()                                   # only the off-plan sentence goes
    assert "\n\nReadiness is 75 and your week was light.\nAmounts: GSSI – " in trimmed
    assert trimmed.startswith("🏃 Run Sat Oct 10")


def test_m4_12_credit_in_the_coachs_words_is_removed_and_the_claim_kept(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(COFFEE_QUESTION, 7, 0, [
        call("search_gssi", query="caffeine timing before exercise", k=3), call("fueling_plan", parts=["caffeine"]),
        say("GSSI shows that a modest dose about an hour before helps many people.")])
    assert "\n\nA modest dose about an hour before helps many people.\nAmounts: GSSI – " in reply
    assert reply.count("GSSI") == 1                                        # only in the code's Amounts line


def test_m4_14_one_line_from_me_is_rewritten(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("search_gssi", query="carbs before a long run", k=2), call("fueling_plan", day_text="Saturday"),
        say("One line from me: the week was light, so go."),
        say("The week was light, so go.")])
    assert "One line from me" not in reply and "\n\nThe week was light, so go." in reply


def test_echo_guard_keeps_a_food_confirmation_after_log_food(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("2 eggs, toast and chai at 8:10", 9, 0, [
        call("log_food", text="2 eggs, toast and chai", time_text="8:10"),
        say("Logged 2 eggs, toast and chai at 8:10am.")])
    assert reply.startswith("Logged 2 eggs, toast and chai at 8:10am.")


def test_echo_guard_keeps_a_restated_plan_line(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("plan it without asking", 7, 0, [call("plan_workout", request=None), say("")])
    [plan] = plans_today(user)
    line = f"Your {plan['plan_json']['workout_type'].replace('_', ' ')} is {plan['plan_json']['duration_min']} min."
    reply = chat.send("what's my plan today?", 7, 5, [call("get_today", include_health=False), say(line)])
    assert reply == line


def test_echo_guard_keeps_the_oura_ring_only_sentence(coach, user, chat_model, patch_oura):
    patch_oura()
    reply = Chat(coach, user, chat_model).send("update from oura", 12, 0, [
        call("refresh_health"), say(f"Nothing new came in. {RING_ONLY}")])
    assert reply == f"Nothing new came in. {RING_ONLY}"


def test_17_an_offer_after_logging_is_trimmed(coach, user, chat_model):
    offer = "Logged coffee at 8 and banana at 10:30. Those look high — want me to correct either one now?"
    reply = Chat(coach, user, chat_model).send("coffee at 8 and a banana at 10:30", 11, 0, [
        call("log_food", text="coffee", time_text="8"), call("log_food", text="banana", time_text="10:30"),
        say(offer), say(offer)])                                                 # the rewrite offers again: trimmed
    assert reply == "Logged coffee at 8 and banana at 10:30."


def test_a_food_right_after_a_log_is_a_new_row_not_an_edit(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("had a banana", 10, 0, [call("log_food", text="a banana", time_text=None), say("Logged a banana.")])
    [first] = food_today(user)
    chat.send("had a banana", 10, 5, [
        call("edit_food", entry_id=first["id"], new_text="a banana"),             # no correction in their words
        call("log_food", text="a banana", time_text=None), say("Logged another banana.")])
    refused = last_tool_result(chat_model, "edit_food")
    assert refused["updated"] is False and len(food_today(user)) == 2


def test_actually_it_was_2_bananas_is_an_edit(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("had a banana", 10, 0, [call("log_food", text="a banana", time_text=None), say("Logged a banana.")])
    [entry] = food_today(user)
    chat.send("actually it was 2 bananas", 10, 2, [call("edit_food", entry_id=entry["id"], new_text="2 bananas"),
                                                   say("Changed it to 2 bananas.")])
    [edited] = food_today(user)
    assert edited["id"] == entry["id"] and edited["raw_text"] == "2 bananas"


def test_m4_14_more_carbs_next_to_a_no_extra_carbs_plan_is_trimmed(coach, user, chat_model):
    drift = ("Readiness is 75 and you had a light week, so boost carbs on Friday and stick to a familiar, "
             "easy-to-digest pre-run meal plus your practiced fluid plan on Saturday to top up glycogen [1].")
    reply = Chat(coach, user, chat_model).send(LONG_RUN_QUESTION, 9, 0, [
        call("search_gssi", query="carbs before a long run", k=2), call("fueling_plan", day_text="Saturday"),
        say(drift), say(drift)])
    assert "Fri: no extra carbs needed" in reply and "boost carbs" not in reply


@live
def test_live_citation_judge_flags_a_claim_the_passage_does_not_make():
    """G rubric "Is every [n]-cited claim supported by passage n?", tried on the M4-12 drift (real passage)."""
    from mcp_servers.gssi import index as gssi_index
    from tests.conftest import judge_citations
    if not gssi_index.INDEX_DIR.exists():
        pytest.skip("no GSSI index")
    [hit] = [h for h in gssi_index.search("lower caffeine doses increase endurance performance", 4)
             if "caffeine-and-exercise" in h["url"]][:1]
    source = [{"n": 1, **hit}]
    drift = ("GSSI shows caffeine levels usually peak about 30–60 minutes after you drink it, so time your coffee to "
             "peak at the start of your run [1].")
    assert judge_citations(drift, source).supported is False
    print("\npassage:", hit["passage"][:300])
    supported = "Low caffeine doses can improve endurance performance [1]."
    assert judge_citations(supported, source).supported is True


def tools_called(since_run_id: int) -> list[str]:
    """Tool names the agent called, from the runs table (agent calls after run `since_run_id`)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT output_json FROM runs WHERE kind = 'agent' AND id > ?", (since_run_id,)).fetchall()
    return [c["name"] for r in rows for m in json.loads(r["output_json"]) for c in m.get("tool_calls", [])]


def last_run_id() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0]


@pytest.fixture
def knowledge(monkeypatch):
    """Live: records what fueling_plan built and what search_gssi returned (real server, real index)."""
    from mcp_servers.gssi import index as gssi_index
    from modules.nutrition import fueling
    from core import mcp_tools
    if not gssi_index.INDEX_DIR.exists():
        pytest.skip("no GSSI index (python scripts/ingest_gssi.py)")
    from core import citations
    seen = {"plans": [], "sources": [], "numbered": []}
    real_plan, real_call, real_numbered = fueling.plan, mcp_tools.call, citations.numbered

    def numbered(earlier, found):          # the [n] numbers search_gssi gave this turn (for the citation judge)
        result = real_numbered(earlier, found)
        seen["numbered"] += result
        return result

    def plan(*args, **kwargs):
        result = real_plan(*args, **kwargs)
        seen["plans"].append(result)
        return result

    def mcp_call(server, tool_name, args):
        result = real_call(server, tool_name, args)
        seen["sources"] += (result or {}).get("results", []) if isinstance(result, dict) else []
        return result

    monkeypatch.setattr(fueling, "plan", plan)
    monkeypatch.setattr(mcp_tools, "call", mcp_call)
    monkeypatch.setattr(citations, "numbered", numbered)
    return seen


def assert_cited_in_own_words(reply: str, seen: dict):
    assert not re.search(r"\b(don't|do not) (repeat|add|offer|ask)\b|sent_as_is|as built", reply, re.I), reply
    urls = [s["url"] for s in seen["sources"]]
    assert urls, "search_gssi returned nothing"
    assert "\nAmounts: GSSI – " in reply, "no line for the articles behind the amounts"     # one line, by code
    for n in set(re.findall(r"\[(\d)\]", reply)):                       # a marker kept has its source line
        assert re.search(rf"^Source( \[{n}\])?: GSSI – ", reply, re.M), f"[{n}] without its source line"
    own = reply
    for plan in seen["plans"]:
        own = own.replace(plan["text"], "")
    own = "\n".join(line for line in own.splitlines() if not line.startswith(("Source", "Amounts")))
    own = own.strip()
    print(f"OWN LINE: {own!r}")                       # counted across runs: the own line should survive >= 80%
    assert copied(own, [s["passage"] for s in seen["sources"]]) is None
    assert not re.search(r"one line from me", own, re.I)
    assert not re.search(r"\b(GSSI|USDA)\b|according to|research shows", own, re.I), own   # credit: code's lines only
    if seen["plans"]:                                  # no fueling topic the plan lacks (a question is fine)
        statements = " ".join(s for s in re.split(r"(?<=[.!?])\s+", own) if not s.endswith("?"))
        assert topics(statements) <= topics(seen["plans"][-1]["text"]), own
    if re.search(r"\[\d\]", own):                    # G rubric: every [n]-cited claim is supported by passage n
        from tests.conftest import judge_citations
        by_n = {}                                     # every passage of n the coach was given, not just the last
        for s in seen["numbered"]:
            if s["passage"] not in by_n.setdefault(s["n"], {**s, "passages": []})["passages"]:
                by_n[s["n"]]["passages"].append(s["passage"])
        verdict = judge_citations(own, [{**s, "passage": "\n…\n".join(s["passages"])} for s in by_n.values()])
        assert verdict.supported, f"unsupported: {verdict.unsupported} ({verdict.reason})"


@live
def test_live_m4_11_long_run_fueling(coach, user, knowledge):
    for back, carbs in ((1, 200), (2, 160), (3, 180)):
        eat_carbs(-back, carbs)
    chat, before = Chat(coach, user), last_run_id()
    reply = chat.send(LONG_RUN_QUESTION, 9, 0)
    chat.print("scenario M4-11: long run fueling")
    from core.citations import to_html
    print("--- as sent to Telegram (parse_mode=HTML, link previews off) ---\n" + to_html(reply))
    called = tools_called(before)
    assert {"fueling_plan", "search_gssi"} <= set(called) and {"get_today", "workouts", "health_range"} & set(called)
    assert knowledge["plans"] and knowledge["plans"][-1]["text"] in reply              # code's amounts, as built
    assert "lately about 180 g a day" in reply
    assert_cited_in_own_words(reply, knowledge)


@live
def test_live_m4_12_coffee_before_a_run(coach, user, knowledge):
    chat = Chat(coach, user)
    reply = chat.send(COFFEE_QUESTION, 7, 0)
    chat.print("scenario M4-12: coffee before a run")
    assert re.search(r"\b(30|45|60)\b.{0,20}min", reply) and re.search(r"\d+\s*mg", reply)
    assert_cited_in_own_words(reply, knowledge)


@live
def test_live_m4_13_electrolytes_for_a_10k(coach, user, knowledge):
    chat = Chat(coach, user)
    reply = chat.send(ELECTROLYTES_QUESTION, 7, 0)
    chat.print("scenario M4-13: electrolytes for a 10k")
    assert "water" in reply.lower() and "weather" in reply.lower()
    assert_cited_in_own_words(reply, knowledge)


@live
def test_live_m4_14_asks_for_weight_once(coach, user, knowledge, patch_oura):
    patch_oura()["personal_info"]["weight"] = None
    chat = Chat(coach, user)
    first = chat.send(LONG_RUN_QUESTION, 9, 0)
    second = chat.send("I'm 62 kg", 9, 2)
    chat.print("scenario M4-14: no weight known")
    assert first.count("your weight") == 1                           # asked once (amounts per kg, if any)
    redone = knowledge["plans"][-1]                                  # the plan redone after the weight was saved
    assert memory.body_weight(user) == 62 and not redone["per_kg"] and redone["text"] in second
    assert "Tell me your weight" not in second


COVERAGE = {
    "1": ("test_1_hi_is_one_line_with_readiness_and_no_llm", "test_live_1_greetings"),
    "1b": ("test_1_greeting_with_more_goes_to_the_agent", "test_live_1_greetings"),
    "11": ("test_11_voice_gets_the_fixed_reply_without_llm", "test_live_11_voice"),
    "12": ("test_12_a_broken_draft_is_rewritten_once", "test_live_12_free_form_replies_follow_the_rules"),
    "13": ("test_13_update_from_oura_with_nothing_new_says_so", "test_live_13_update_from_oura_nothing_new"),
    "13b": ("test_13b_update_from_oura_with_a_new_workout_names_it", "test_live_13b_update_from_oura_new_workout"),
    "2": ("test_2_a_correction_updates_the_same_row_with_their_numbers", "test_live_2_correction_edits_the_row"),
    "3": ("test_3_a_new_description_re_estimates_the_same_row", "test_live_3_edit_that_re_estimates"),
    "3b": ("test_3_a_new_description_re_estimates_the_same_row", "test_live_3_edit_that_re_estimates"),
    "4": ("test_4_remove_the_pizza_names_it_and_updates_totals", "test_live_4_remove_the_pizza"),
    "4b": ("test_remove_food_asks_when_several_entries_match", "test_live_4b_two_pizzas_asks_which"),
    "4c": ("test_4b_two_pizzas_and_the_model_guesses_nothing_is_removed", "test_live_4b_two_pizzas_asks_which"),
    "5": ("test_5_ideas_then_the_second_one_then_higher_protein_is_the_same_dish",
          "test_live_5_ideas_recipe_and_higher_protein"),
    "5b": ("test_5b_dinner_ideas_on_two_days_in_a_row_are_different_dishes",
           "test_live_5b_two_days_of_dinner_ideas_are_different"),
    "15": ("test_15_16_a_question_or_a_plan_to_eat_is_not_logged", "test_live_15_16_questions_and_later_are_not_logged"),
    "16": ("test_15_16_a_question_or_a_plan_to_eat_is_not_logged", "test_live_15_16_questions_and_later_are_not_logged"),
    "17": ("test_17_two_times_in_one_message_are_two_rows", "test_live_17_18_times_and_together"),
    "18": ("test_18_eaten_together_is_one_row", "test_live_17_18_times_and_together"),
    "19": ("test_19_food_at_0050_before_the_nights_sleep_counts_for_the_day_before",
           "test_live_19_after_midnight_before_bed"),
    "20": ("test_20_delete_the_last_two_removes_only_the_two_newest", "test_live_20_delete_the_last_two"),
    "6": ("test_6_run_plus_strength_is_one_plan_with_two_blocks_that_fits", "test_live_6_run_plus_strength"),
    "7": ("test_7_yes_plans_hiit_and_records_the_skipped_rules", "test_live_7_one_question_then_yes_or_no"),
    "7b": ("test_7b_no_plans_the_suggestion_without_a_second_question", "test_live_7_one_question_then_yes_or_no"),
    "7c": ("test_7c_yes_twenty_minutes_later_in_a_new_session_is_still_answered", "test_live_7c_yes_in_a_new_session"),
    "7d": ("test_7d_an_unanswered_question_expires_with_no_plan_and_no_score", "test_live_7d_question_expires"),
    "8": ("test_8_expired_checkin_gets_one_line_after_the_answer", "test_live_8_expired_checkin"),
    "10": ("test_10_done_takes_oura_times_exactly", "test_live_10_done_uses_oura_times"),
    "10b": ("test_10b_done_without_an_oura_workout_asks_and_invents_nothing", "test_live_10b_done_without_oura"),
    "10c": ("test_10c_done_and_a_question_keeps_the_confirmation_then_answers", "test_live_10c_done_and_what_to_eat"),
    "21": ("test_21_how_hard_can_i_go_is_a_question_not_a_checkin", "test_live_21_how_hard_is_not_a_checkin"),
    "22": ("test_22_a_second_checkin_replaces_the_first_plan", "test_live_22_second_checkin_replaces_the_first"),
    "23": ("test_23_hiit_in_the_morning_then_evening_checkin_is_light_and_the_day_scores_full",
           "test_live_23_morning_hiit_then_evening"),
    "24": ("test_24_checkin_after_the_cutoff_is_rest_with_a_sleep_note_and_no_plan_call", "test_live_24_late_checkin"),
    "25": ("test_25_skip_is_planned_and_labeled_skipped", "test_live_25_skip"),
    "9": ("test_9_remember_list_forget", "test_live_9_memory"),
    "26": ("test_26_a_dated_fact_shapes_the_plan_in_range_and_is_gone_after", "test_live_26_dated_trip"),
    "27": ("test_27_a_fact_without_days_is_not_saved_with_made_up_dates", "test_live_27_away_sometime_soon"),
    "28": ("test_28_forget_my_diet_stuff_asks_before_removing_anything", "test_live_28_forget_my_diet_stuff"),
    "14": ("test_14_hows_my_day_twice_gives_the_same_score_and_breakdown", "test_live_14_hows_my_day_twice"),
    "14b": ("test_the_score_compares_to_their_own_7_day_average", "test_live_14_hows_my_day_twice"),
    "29": ("test_29_no_food_logged_scores_nutrition_zero_and_says_not_logged", "test_live_29_no_food"),
    "30": ("test_30_under_the_calorie_floor_after_the_cutoff_scores_calories_zero",
           "test_live_30_31_under_the_floor_then_food_after_the_cutoff"),
    "31": ("test_31_after_the_cutoff_food_does_not_change_the_score",
           "test_live_30_31_under_the_floor_then_food_after_the_cutoff"),
    "32": ("test_32_user_2_sees_only_their_own_food_and_facts", "test_live_32_two_people"),
    "33": ("test_33_no_wearable_data_scores_only_what_there_is", "test_live_33_no_wearable"),
    "34": ("test_34_low_readiness_with_no_hard_workout_is_not_held_against_them",
           "test_live_34_37_a_low_readiness_rest_day"),
    "35": ("test_35_one_rest_day_within_the_weekly_budget_is_full_credit", "test_live_35_rest_day_within_budget"),
    "36": ("test_36_a_second_rest_day_in_the_same_7_days_is_missed", "test_live_36_second_rest_day"),
    "37": ("test_37_a_low_readiness_rest_day_is_full_credit_without_using_the_budget",
           "test_live_34_37_a_low_readiness_rest_day"),
    "38": ("test_38_a_travel_fact_makes_a_rest_day_a_recovery_rest", "test_live_38_travel_rest_day"),
    "M4-1": ("test_m4_1_protein_over_the_last_week_is_the_codes_sum", "test_live_m4_1_protein_over_the_last_week"),
    "M4-2": ("test_m4_2_summarize_a_day", "test_live_m4_2_summarize_a_day"),
    "M4-3": ("test_m4_3_a_bare_yes_the_next_morning_is_checked_first", "test_live_m4_3_a_yes_the_next_morning"),
    "M4-4": ("test_m4_4_a_general_question_never_offers_to_look_it_up", "test_live_m4_4_caffeine_in_a_celsius"),
    "M4-5": ("test_m4_5_a_workout_oura_never_saw_gets_the_ring_note_and_an_offer",
             "test_live_m4_5_a_workout_oura_never_saw"),
    "M4-6": ("test_m4_6_the_lowest_protein_day_this_week", "test_live_m4_6_lowest_protein_day"),
    "M4-7": ("test_m4_7_how_did_i_sleep_this_week", "test_live_m4_7_sleep_this_week"),
    "M4-7b": ("test_m4_7b_which_days_do_i_usually_skip", "test_live_m4_7b_which_days_do_i_skip"),
    "M4-7c": ("test_m4_7c_best_sleep_this_month_and_what_was_different", "test_live_m4_7c_best_sleep_and_what_was_different"),
    "M4-8": ("test_m4_8_9_10_pushback_is_kind_with_options", "test_live_m4_8_9_10_pushback"),
    "M4-9": ("test_m4_8_9_10_pushback_is_kind_with_options", "test_live_m4_8_9_10_pushback"),
    "M4-10": ("test_m4_8_9_10_pushback_is_kind_with_options", "test_live_m4_8_9_10_pushback"),
    "M4-11": ("test_m4_11_long_run_fueling_is_the_codes_amounts_with_a_line_and_a_link", "test_live_m4_11_long_run_fueling"),
    "M4-12": ("test_m4_12_coffee_before_a_run_is_timed_and_sized_for_their_weight", "test_live_m4_12_coffee_before_a_run"),
    "M4-13": ("test_m4_13_a_10k_drinks_to_thirst_and_says_there_is_no_weather_data",
              "test_live_m4_13_electrolytes_for_a_10k"),
    "M4-14": ("test_m4_14_no_weight_means_per_kg_one_question_then_grams", "test_live_m4_14_asks_for_weight_once"),
}


def _marks(test) -> set[str]:
    return {m.name for m in getattr(test, "pytestmark", [])}


def test_every_scenario_in_the_doc_has_a_scripted_and_a_live_test():
    ids = re.findall(r"^- \*\*([0-9]+[a-z]?|M4-\d+[a-z]?)\b", SCENARIOS_DOC.read_text(), re.M)
    assert sorted(ids) == sorted(COVERAGE), set(ids) ^ set(COVERAGE)
    tests = globals()
    for scenario, (scripted, live_name) in COVERAGE.items():
        live_test = tests[live_name]
        assert "live_llm" in _marks(live_test), scenario
        assert scripted in tests and "live_llm" not in _marks(tests[scripted]), scenario
