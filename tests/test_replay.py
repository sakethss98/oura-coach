"""Replay scenarios from real usage (M3.2 brief, Phase 6 list), on fixtures.

Scripted tests feed the agent preset model turns, so they check the code around the model:
tools, database effects, sessions and the reply rules. Live tests (RUN_LIVE_LLM=1) use the real
model and check that it understands; run them with -s to see the transcripts.
Plan and food estimates are stubbed in both (fake_llm), so numbers are predictable.
Phase 2 covers scenarios 1, 8, 9, 11, 12 and the correction guard; the rest come with Phases 3-5.
"""
import os
from datetime import time

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from connectors.health import Health
from core import db, memory
from core.agent import EXPIRED_CHECKIN
from core.coach import VOICE, Coach
from core.reply_rules import violations
from tests.conftest import call, needs_fixtures, say
from today import fixtures_now, food_on

pytestmark = needs_fixtures
live = pytest.mark.skipif(not os.getenv("RUN_LIVE_LLM"), reason="set RUN_LIVE_LLM=1 to call OpenAI")
GREETING_75 = "Hi! Readiness 75 today. Tell me what you ate, ask for a workout, or ask how your day is going."


def at(hour, minute=0):
    return fixtures_now(time(hour, minute))


@pytest.fixture
def coach(temp_db, fake_llm, empty_calendar):
    return Coach(InMemorySaver(), use_fixtures=True)


class Chat:
    """Sends messages and keeps a transcript (printed by live tests)."""

    def __init__(self, coach, user, model=None):
        self.coach, self.user, self.model, self.lines = coach, user, model, []

    def send(self, text, hour, minute=0, script=()):
        if self.model is not None:
            self.model.script.extend(script)
        reply = self.coach.handle_text(self.user, text, at(hour, minute))
        self.lines += [f"[{hour:02d}:{minute:02d}] you: {text}", f"        coach: {reply}"]
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
    question = chat.send("want to work out now", 7, 0, [call("start_checkin"), say("")])
    assert "energy (1-5) and soreness (0-5)" in question
    assert db.pending_plan(1) is not None

    reply = chat.send("what's left to eat today", 7, 16, [
        call("get_today", include_health=False), say("About 2218 kcal and 126 g protein left today.")])
    assert reply == f"About 2218 kcal and 126 g protein left today.\n{EXPIRED_CHECKIN}"
    assert db.pending_plan(1) is None                                    # the old question is dropped
    assert humans_seen(chat_model) == ["what's left to eat today"]       # new session: no old history
    assert_rules(reply)


def test_8_answer_in_the_same_session_is_planned(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("want to work out now", 7, 0, [call("start_checkin"), say("")])
    plan = chat.send("4 2 legs fine", 7, 2, [call("submit_checkin", energy=4, soreness=2, comment="legs fine"),
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
