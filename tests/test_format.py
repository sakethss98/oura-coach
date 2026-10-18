"""Golden text for the chat messages, and no internal terms anywhere the person reads."""
import re
from datetime import date, datetime, time

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from core import db
from core.coach import Coach
from core.config import TZ
from modules.training.messages import format_plan, format_why
from modules.training.planner import finalize
from tests.conftest import call, needs_fixtures, say
from today import fixtures_now

TARGETS = {"targets": {"calories": 2218, "protein_g": 126, "carbs_g": 290, "fat_g": 62, "fiber_g": 31},
           "inputs": {}, "reason": None}
REMAINING = {"calories": 1838, "protein_g": 101, "carbs_g": 262, "fiber_g": 26}
INTERNAL = re.compile(r"\b[a-z]+_[a-z_]+\b|\bnull\b|\bwindow\b|\bfloor\b", re.I)
DONE = 'Reply "done" when you finish, or I\'ll mark it from Oura if it records it.'


def plan(**fields):
    base = {"workout_type": "HIIT", "intensity": 4, "time_slot": "18:30-19:00", "duration_min": 30,
            "why": "Your readiness is 82 and HRV is above your usual, so it's a good day to push.",
            "reasoning": "Full reasoning.", "food_before": "A banana or 2 dates 30 minutes before",
            "food_after": "Paneer bhurji with 2 rotis to cover most of the protein left", "follow_up": "",
            "exercises": [], "rules_applied": [], "split_day": None, "food_source": "coach", "exercises_note": None}
    return {**base, **fields}


def state(final, first=True, readiness=82):
    return {"final_plan": final, "oura": {"today": {"readiness_score": readiness}},
            "context": {"first_plan_today": first, "targets": TARGETS, "remaining": REMAINING}}


def test_workout_message_golden():
    assert format_plan(state(plan())) == "\n".join([
        "🔋 Readiness 82",
        "🏃 HIIT · 30 min · 6:30–7:00pm",
        "Today: 2218 kcal · 126g protein",
        "",
        "Why: Your readiness is 82 and HRV is above your usual, so it's a good day to push.",
        "",
        "🍽 Before: A banana or 2 dates 30 minutes before",
        "After: Paneer bhurji with 2 rotis to cover most of the protein left",
        "",
        DONE,
    ])


def test_later_plan_has_no_today_line_and_walk_has_no_before():
    text = format_plan(state(plan(workout_type="walk", time_slot="11:30-12:15", duration_min=45, food_before=""),
                             first=False))
    assert text.splitlines()[:3] == ["🔋 Readiness 82", "🚶 Walk · 45 min · 11:30am–12:15pm", ""]
    assert "🍽 After: Paneer bhurji" in text and "Before" not in text


def test_rest_message_golden():
    rest = plan(workout_type="rest", intensity=1, time_slot="none", duration_min=0, food_before="",
                why="Your body could handle more, but you said energy 2, so today is a recovery day.",
                food_after="Moong dal chilla with paneer and curd",
                follow_up="Want a 10-minute mobility routine instead?")
    assert format_plan(state(rest, first=False, readiness=75)) == "\n".join([
        "🔋 Readiness 75",
        "😴 Rest",
        "",
        "Why: Your body could handle more, but you said energy 2, so today is a recovery day.",
        "",
        "🍽 Left today: 1838 kcal · 101g protein",
        "Moong dal chilla with paneer and curd",
        "",
        "Want a 10-minute mobility routine instead?",
    ])


EXERCISES = [{"name": "Bench press", "sets_reps": "4 x 8-10"}, {"name": "Overhead press", "sets_reps": "3 x 8-10"},
             {"name": "Incline dumbbell press", "sets_reps": "3 x 10-12"},
             {"name": "Lateral raises", "sets_reps": "3 x 12-15"}, {"name": "Dips", "sets_reps": "3 x 8-12"},
             {"name": "Triceps pushdown", "sets_reps": "3 x 12-15"}]


def test_strength_message_golden():
    strength = plan(workout_type="strength", time_slot="06:30-07:15", duration_min=45, split_day="Push",
                    exercises=EXERCISES, why="Good recovery and it's your Push day.",
                    food_before="A bowl of curd with fruit an hour before",
                    food_after="Rajma chawal with a bowl of curd")
    assert format_plan(state(strength, first=False)) == "\n".join([
        "🔋 Readiness 82",
        "🏋️ Strength (Push) · 45 min · 6:30–7:15am",
        "1. Bench press · 4 x 8-10",
        "2. Overhead press · 3 x 8-10",
        "3. Incline dumbbell press · 3 x 10-12",
        "4. Lateral raises · 3 x 12-15",
        "5. Dips · 3 x 8-12",
        "6. Triceps pushdown · 3 x 12-15",
        "",
        "Why: Good recovery and it's your Push day.",
        "",
        "🍽 Before: A bowl of curd with fruit an hour before",
        "After: Rajma chawal with a bowl of curd",
        "",
        DONE,
    ])


# --- finalize: every chat field matches the final workout ---------------------

def graph_state(llm_plan, overrides=(), split="Pull", oura_band="push", recent=()):
    return {"context": {"remaining": REMAINING, "next_split_day": split, "recent_meal_ideas": list(recent)},
            "baseline": {"band": {"band": oura_band}}, "oura": {"today": {"readiness_score": 82}},
            "goals": {"schedule": {"readiness_floor": 70}, "nutrition": {"diet": "vegetarian"}},
            "day": "2026-10-07", "plan_source": "coach", "llm_plan": llm_plan}


def test_strength_gets_split_day_from_code_and_keeps_5_to_6_exercises():
    llm = plan(workout_type="strength", exercises=EXERCISES)
    final = finalize(dict(llm), llm, [], graph_state(llm))
    assert final["split_day"] == "Pull" and len(final["exercises"]) == 6
    assert "Strength (Pull)" in format_plan(state(final))


def test_strength_with_wrong_exercise_count_is_shown_without_the_list():
    llm = plan(workout_type="strength", exercises=EXERCISES[:3])
    final = finalize(dict(llm), llm, [], graph_state(llm))
    assert final["exercises"] == [] and "3 exercises" in final["exercises_note"]
    assert "1. " not in format_plan(state(final))


def test_override_from_hiit_to_walk_replaces_food_lines_with_code_picks():
    llm = plan()
    override = {"rule": "worked_out", "text": "t", "plain": "You already trained today (35-min run at 7:00am), "
                "so just something light.", "changes_type": True}
    final = finalize({**llm, "workout_type": "walk", "intensity": 2}, llm, [override], graph_state(llm))
    assert final["food_source"] == "code"
    assert final["food_before"] == ""
    assert final["food_after"] == "Paneer bhurji (150 g, about 1 katori) with 2 chapatis and a bowl of dal"
    assert final["why"].startswith("Your body could handle more, but you already trained today")
    assert final["exercises"] == [] and final["split_day"] is None
    again = finalize({**llm, "workout_type": "walk", "intensity": 2}, llm, [override],
                     graph_state(llm, recent=["Paneer bhurji wrap"]))         # suggested lately: the next one
    assert again["food_after"].startswith("Soya chunk curry")


def test_override_to_rest_adds_a_question():
    llm = plan()
    override = {"rule": "no_time", "text": "t", "plain": "There's no free time left today.", "changes_type": True}
    final = finalize({**llm, "workout_type": "rest", "time_slot": "none", "duration_min": 0}, llm, [override],
                     graph_state(llm))
    assert final["follow_up"] == "Want a 10-minute stretch you can do anytime?"
    assert format_plan(state(final)).endswith("Want a 10-minute stretch you can do anytime?")


# --- no internal terms --------------------------------------------------------

def assert_plain(text: str) -> None:
    assert not INTERNAL.findall(text), INTERNAL.findall(text)


def test_plan_messages_are_plain():
    assert_plain(format_plan(state(plan())))
    assert_plain(format_plan(state(plan(workout_type="rest", time_slot="none", follow_up="Stretch?"))))


@pytest.fixture
def coach(temp_db, fake_llm, empty_calendar):
    return Coach(InMemorySaver(), use_fixtures=True)


@needs_fixtures
def test_tool_texts_and_confirmations_are_plain(coach, chat_model, user):
    """Every code-built text the agent sends (question, plan, why) and the confirmations stay plain."""
    at = lambda h, m=0: fixtures_now(time(h, m))  # noqa: E731
    turns = [
        ("want to work out", [call("start_checkin", request_text=None), say("")]),
        ("2 3 tired", [call("submit_checkin", answer="2 3 tired"), say("")]),
        ("why?", [call("explain_plan"), say("")]),
        ("had poha", [call("log_food", text="poha", time_text=None), say("Logged poha: about 450 kcal, 20 g protein.")]),
        ("done, felt great", [call("mark_done", feeling="great"), say("Oura doesn't have that walk yet. When did you do it?")]),
        ("went for a 30 min walk at 6", [call("add_manual_workout", workout_type="walk", minutes=30,
                                              start_time_text="at 6", feeling=None), say("Added a 30-minute walk at 6am.")]),
    ]
    for i, (message, script) in enumerate(turns):
        chat_model.script.extend(script)
        assert_plain(coach.handle_text(user, message, at(7, i)))
    assert chat_model.script == []


def test_why_with_old_string_overrides_still_works(temp_db):
    session_id = db.start_plan(1, date(2026, 10, 7), "t", datetime(2026, 10, 7, 7, tzinfo=TZ))
    db.finish_plan(session_id, {}, plan(), plan(), ["Old plain override text."], {})
    text = format_why(db.get_plan(session_id))
    assert "Old plain override text." in text and "Check-in: skipped" in text


# --- several blocks, the consult question (M3.2 Phase 3) -------------------------------

from modules.training.messages import format_consult  # noqa: E402

RUN_THEN_LEGS = [{"workout_type": "easy_run", "intensity": 3, "duration_min": 20},
                 {"workout_type": "strength", "intensity": 3, "duration_min": 30}]


def test_two_block_message_golden():
    final = plan(workout_type="easy_run", intensity=3, time_slot="07:00-07:50", duration_min=50, split_day="Legs",
                 blocks=RUN_THEN_LEGS, why="Fresh legs and good readiness, so a run, then legs.")
    assert format_plan(state(final)) == "\n".join([
        "🔋 Readiness 82",
        "🏃 Easy run 20 min + 🏋️ Strength (Legs) 30 min · 7:00–7:50am",
        "Today: 2218 kcal · 126g protein",
        "",
        "Why: Fresh legs and good readiness, so a run, then legs.",
        "",
        "🍽 Before: A banana or 2 dates 30 minutes before",
        "After: Paneer bhurji with 2 rotis to cover most of the protein left",
        "",
        DONE,
    ])


def test_consult_questions_are_plain():
    floor = {"rule": "readiness_floor", "plain": "Readiness is 64 today, lower than usual, so I'd keep it light.",
             "changes_type": True}
    band = {"rule": "band", "plain": "Readiness is 64 today, too low for a hard day. So keep it light today.",
            "changes_type": True}
    question = format_consult([{"workout_type": "HIIT", "duration_min": None},
                               {"workout_type": "strength", "duration_min": 30}],
                              [{"workout_type": "walk", "duration_min": 60}], [floor, band, floor])
    assert question == ("Readiness is 64 today, lower than usual, so I'd keep it light.\n"
                        "I'd suggest a 60-min walk instead. Still want HIIT, then 30 min of strength?")
    assert_plain(question)


def test_why_for_a_two_block_plan_shows_each_block_done_state(temp_db):
    plan_id = db.start_plan(1, date(2026, 10, 7), "t", datetime(2026, 10, 7, 7, tzinfo=TZ))
    final = plan(workout_type="easy_run", intensity=3, time_slot="07:00-07:50", duration_min=50, split_day="Legs",
                 blocks=RUN_THEN_LEGS)
    db.finish_plan(plan_id, {}, final, final, [], {})
    db.mark_block_done(plan_id, 0, datetime(2026, 10, 7, 7, 2, tzinfo=TZ), datetime(2026, 10, 7, 7, 21, tzinfo=TZ),
                       19, "oura", datetime(2026, 10, 7, 7, 21, tzinfo=TZ))
    text = format_why(db.get_plan(plan_id))
    assert text.startswith("Your 7:00am plan: Easy run 20 min + Strength (Legs) 30 min · 7:00–7:50am\n"
                           "Done: Easy run 7:02–7:21am (from Oura); Strength (Legs) not yet")
    assert_plain(text)
    assert db.get_plan(plan_id)["completed"] is None                         # one block left
