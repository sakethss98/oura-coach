"""Replay scenarios from real usage (M3.2 brief, Phase 6 list), on fixtures.

Scripted tests feed the agent preset model turns, so they check the code around the model:
tools, database effects, sessions and the reply rules. Live tests (RUN_LIVE_LLM=1) use the real
model and check that it understands; run them with -s to see the transcripts.
Plan and food estimates are stubbed in both (fake_llm), so numbers are predictable; live tests read
the check-in answer with the real model (real_checkin), since that is understanding too.
Scenario ids and wording: docs/scenarios.md. Phase 2: 1, 8, 9, 11, 12 and the correction guard.
Phase 3 (training): 6, 7, 7b, 7c, 10, 10b, 21-25. The rest come with Phases 4-6.
"""
import os
from datetime import time

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from connectors.health import Health
from core import db, memory
from core.agent import EXPIRED_CHECKIN
from core.coach import VOICE, Coach
from core.reply_rules import violations
from core.llm import structured_call
from modules.training import checkin
from modules.training.module import TrainingModule
from modules.training.planner import LATE_FOLLOW_UP, LATE_WHY
from modules.training.tools import NO_OURA_MATCH
from tests.conftest import call, needs_fixtures, parsed_checkin, say, tool_results
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

def test_a_correction_never_becomes_a_second_entry(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("poppi strawberry lemon soda small can", 10, 0, [
        call("log_food", text="poppi strawberry lemon soda small can", time_text=None),
        say("Logged poppi soda: about 450 kcal, 20 g protein.")])
    reply = chat.send("correction on that it is 3g fiber and 9g carbs. 30cal", 10, 2, [
        call("log_food", text="poppi soda, 3g fiber, 9g carbs, 30 cal", time_text=None),   # the model slips
        say("I can't edit entries yet. Want me to remove it and log it again with your numbers?")])
    assert len(food_today(user)) == 1                                   # the tool refused
    assert "remove it and log it again" in reply


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


@live
def test_live_correction_is_not_a_second_entry(coach, user):
    chat = Chat(coach, user)
    chat.send("poppi strawberry lemon soda small can", 10, 0)
    reply = chat.send("correction on that it is 3g fiber and 9g carbs. 30cal", 10, 2)
    chat.print("correction guard")
    assert len(food_today(user)) == 1
    assert "remov" in reply.lower()


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
