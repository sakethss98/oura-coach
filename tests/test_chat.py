"""Message dispatch with the router and the other LLM calls stubbed."""
from datetime import date, datetime, time, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import db
from chat import CHECKIN_REMINDER, UNCLEAR, Coach
from config import TZ
from graph import build_graph
from tests.conftest import action, needs_fixtures
from today import fixtures_now

WEDNESDAY = date(2026, 10, 7)


def at(hour, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=TZ)


@pytest.fixture
def coach(temp_db, fake_llm):
    return Coach(build_graph(InMemorySaver()), use_fixtures=False)


def planned_session(day=WEDNESDAY, workout_type="easy_run", duration=40):
    session_id = db.start_session(day, "checkin-test", at(7))
    plan = {"workout_type": workout_type, "intensity": 3, "time_slot": "07:00-07:40", "duration_min": duration}
    db.finish_session(session_id, {"energy": 4, "soreness": 2, "note": None}, plan, plan, [], {})
    return session_id


def test_food_and_workout_in_one_message(coach, fake_llm):
    session_id = planned_session()
    text = "protein shake after the run, felt great"
    fake_llm.routes[text] = [action("workout_done", refers_to_plan=True, feeling="great"),
                             action("food", text="protein shake", time_text="after the run")]
    reply = coach.handle_text(text, at(8, 10))

    assert len(db.food_entries(WEDNESDAY)) == 1
    session = db.get_session(session_id)
    assert session["completed"] == 1 and session["feeling"] == "great"
    assert session["actual_start"].startswith("2026-10-07T07:30")     # 40 min plan before the message
    assert "Marked done" in reply and "Logged #1" in reply
    assert "Couldn't read the time" in reply                          # "after the run" is not a clock time


def test_food_with_stated_time(coach, fake_llm):
    fake_llm.routes["lunch at 1: rajma chawal"] = [action("food", text="rajma chawal", time_text="at 1")]
    reply = coach.handle_text("lunch at 1: rajma chawal", at(14, 30))
    assert db.food_entries(WEDNESDAY)[0]["timestamp"] == "2026-10-07T13:00:00-05:00"
    assert "1:00pm (time you gave)" in reply


def test_food_without_time_uses_message_time(coach, fake_llm):
    fake_llm.routes["had poha"] = [action("food", text="poha")]
    reply = coach.handle_text("had poha", at(8, 5))
    assert db.food_entries(WEDNESDAY)[0]["timestamp"] == "2026-10-07T08:05:00-05:00"
    assert "(message time)" in reply


def test_context_note_dates_resolved_in_code(coach, fake_llm):
    text = "traveling Thu-Sat, keep it light"
    fake_llm.routes[text] = [action("context_note", text=text, start_text="Thu", end_text="Sat")]
    reply = coach.handle_text(text, at(9))
    assert "Thu Oct 08 - Sat Oct 10" in reply
    assert db.active_context_notes(date(2026, 10, 9)) == [text]
    assert db.active_context_notes(date(2026, 10, 11)) == []


def test_unreadable_note_dates_are_not_saved(coach, fake_llm):
    fake_llm.routes["busy someday"] = [action("context_note", text="busy", start_text="someday")]
    assert "couldn't work out the dates" in coach.handle_text("busy someday", at(9))
    assert db.active_context_notes(WEDNESDAY) == []


def test_walk_without_open_session_is_unplanned(coach, fake_llm):
    fake_llm.routes["went for a 30 min walk"] = [
        action("workout_done", refers_to_plan=False, workout_type="walk", duration_min=30)]
    reply = coach.handle_text("went for a 30 min walk", at(17))
    [session] = db.sessions_between(WEDNESDAY, WEDNESDAY)
    assert (session["source"], session["completed"], session["plan_json"]["workout_type"]) == ("unplanned", 1, "walk")
    assert session["actual_start"].startswith("2026-10-07T16:30")
    assert "Logged: walk, 30 min, 4:30–5:00pm." in reply


def test_new_activity_with_an_open_plan_is_still_unplanned(coach, fake_llm):
    session_id = planned_session()
    fake_llm.routes["went for a 30 min walk"] = [
        action("workout_done", refers_to_plan=False, workout_type="walk", duration_min=30)]
    coach.handle_text("went for a 30 min walk", at(17))
    assert db.get_session(session_id)["completed"] is None


def test_unclear_gets_examples(coach, fake_llm):
    assert coach.handle_text("hmm", at(9)) == UNCLEAR


def test_undo_removes_newest_food_or_note(coach, fake_llm):
    fake_llm.routes["had poha"] = [action("food", text="poha")]
    coach.handle_text("had poha", at(8))
    db.add_context_note("sick", WEDNESDAY, WEDNESDAY)
    assert "Removed note: sick" in coach.handle_command("undo", "", at(9))
    assert "Removed food #1: poha" in coach.handle_command("undo", "", at(9))
    assert coach.handle_command("undo", "", at(9)) == "Nothing to undo."


def test_checkin_reply_while_pending_resumes(coach, fake_llm, monkeypatch):
    resumed = {}
    db.start_session(WEDNESDAY, "checkin-pending", at(7))
    monkeypatch.setattr(coach, "_resume", lambda pending, answer, now: resumed.update(answer) or "PLAN")
    assert coach.handle_text("4 2 slept ok", at(7, 5)) == "PLAN"
    assert resumed == {"energy": 4, "soreness": 2, "note": "slept ok"}
    assert fake_llm.calls == []                                       # no router call for a check-in answer


def test_food_while_checkin_pending_is_logged_with_a_reminder(coach, fake_llm):
    db.start_session(WEDNESDAY, "checkin-pending", at(7))
    fake_llm.routes["had 3 eggs and 2 toast"] = [action("food", text="3 eggs and 2 toast")]
    reply = coach.handle_text("had 3 eggs and 2 toast", at(7, 5))
    assert "Logged #1" in reply and reply.endswith(CHECKIN_REMINDER)


def test_pending_checkin_from_yesterday_is_dropped(coach, fake_llm):
    db.start_session(WEDNESDAY - timedelta(days=1), "checkin-old", at(7) - timedelta(days=1))
    fake_llm.routes["4 2"] = [action("unclear")]
    coach.handle_text("4 2", at(7))
    assert db.pending_session() is None


def test_help_command(coach):
    assert "/checkin" in coach.handle_command("help", "", at(9))


# --- commands that run the graph or fetch today's data (fixtures) -------------

@pytest.fixture
def fixtures_coach(temp_db, fake_llm, empty_calendar):
    return Coach(build_graph(InMemorySaver()), use_fixtures=True)


def fixture_time(hour, minute=0):
    return fixtures_now(time(hour, minute))


@needs_fixtures
def test_checkin_command_then_answer(fixtures_coach):
    question = fixtures_coach.handle_command("checkin", "", fixture_time(7))
    assert "energy (1-5)" in question
    reply = fixtures_coach.handle_text("4 2", fixture_time(7, 2))
    assert reply.startswith("🔋 Readiness")
    assert "7:02–7:42am" in reply and "\nToday: " in reply


@needs_fixtures
def test_plan_reuses_latest_checkin_with_its_time(fixtures_coach):
    fixtures_coach.handle_command("checkin", "", fixture_time(7))
    fixtures_coach.handle_text("3 4", fixture_time(7, 1))
    fixtures_coach.handle_command("plan", "", fixture_time(12))
    why = fixtures_coach.handle_command("why", "", fixture_time(12, 5))
    assert "Check-in (from your 7:00am check-in): energy 3, soreness 4" in why


@needs_fixtures
def test_plan_without_checkin_says_skipped(fixtures_coach):
    fixtures_coach.handle_command("plan", "", fixture_time(12))
    assert "Check-in: skipped" in fixtures_coach.handle_command("why", "", fixture_time(12, 5))


@needs_fixtures
def test_today_command(fixtures_coach, fake_llm):
    fake_llm.routes["had poha"] = [action("food", text="poha")]
    fixtures_coach.handle_text("had poha", fixture_time(8))
    db.add_context_note("traveling", fixture_time(8).date(), fixture_time(8).date())
    reply = fixtures_coach.handle_command("today", "", fixture_time(9))
    assert "8:00am poha" in reply
    assert "Food so far: 1 entries" in reply
    assert "Notes: traveling" in reply
    assert "Left today:" in reply


@needs_fixtures
def test_question_is_answered_from_todays_data(fixtures_coach, fake_llm):
    fake_llm.routes["what should I eat for dinner?"] = [action("question", text="what should I eat for dinner?")]
    assert fixtures_coach.handle_text("what should I eat for dinner?", fixture_time(18)) == "stub answer"
    kind, user = fake_llm.calls[-1]
    assert kind == "answer" and "remaining_today" in user


# --- /why, the "why" action, and answers to the coach's question (M3.1) ----------

def test_why_without_a_plan_today(coach):
    assert coach.handle_command("why", "", at(9)) == "No plan yet today. Send /checkin to get one."


@needs_fixtures
def test_why_shows_reasoning_overrides_and_numbers(fixtures_coach, fake_llm):
    fixtures_coach.handle_command("checkin", "", fixture_time(7))
    fixtures_coach.handle_text("2 2", fixture_time(7, 1))
    why = fixtures_coach.handle_command("why", "", fixture_time(7, 5))
    assert why.startswith("Your 7:00am plan: Walk")
    assert "Check-in (just now): energy 2, soreness 2" in why
    assert "Oura: readiness 75 (usual 75.6)" in why
    assert "Check-in: You said energy 2, so today is a recovery day." in why
    assert "Coach's reasoning: stub reasoning" in why
    assert "What code changed:" in why and "Food ideas from fixed options" in why


@needs_fixtures
def test_router_why_action(fixtures_coach, fake_llm):
    fixtures_coach.handle_command("plan", "", fixture_time(7))
    fake_llm.routes["why?"] = [action("why")]
    assert fixtures_coach.handle_text("why?", fixture_time(7, 5)).startswith("Your 7:00am plan:")


@needs_fixtures
def test_yes_goes_to_the_router_with_the_coachs_last_question(fixtures_coach, fake_llm):
    fixtures_coach.handle_command("plan", "", fixture_time(20, 45))          # no time left: rest + question
    fake_llm.routes["yes"] = [action("question", text="Yes to: Want a 10-minute stretch you can do anytime?")]
    assert fixtures_coach.handle_text("yes", fixture_time(20, 50)) == "stub answer"
    router_input = next(user for kind, user in fake_llm.calls if kind == "router")
    assert '"coach_last_question": "Want a 10-minute stretch you can do anytime?"' in router_input


@needs_fixtures
def test_oura_workout_marks_the_plan_done_in_today(fixtures_coach, fake_llm, monkeypatch):
    import today
    fixtures_coach.handle_command("plan", "", fixture_time(7))               # stub plan: HIIT 07:00-07:40
    real_fetch = today.fetch_oura

    def with_morning_run(day, use_fixtures):
        oura = real_fetch(day, use_fixtures)
        run = {"activity": "running", "intensity": "moderate", "minutes": 30,
               "start": f"{day}T07:10-05:00", "end": f"{day}T07:40-05:00"}
        oura["workouts_by_day"][day.isoformat()] = [run]
        return oura

    monkeypatch.setattr(today, "fetch_oura", with_morning_run)
    reply = fixtures_coach.handle_command("today", "", fixture_time(9))
    assert "- Planned: HIIT 7:00–7:40am (done, from Oura)" in reply
    assert db.latest_planned_session(fixture_time(9).date())["done_by"] == "oura"
