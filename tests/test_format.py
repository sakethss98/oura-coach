"""Golden text for the chat messages, and no internal terms anywhere the person reads."""
import re
from datetime import date, datetime, time

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import db
from chat import Coach, format_plan, format_why
from config import TZ
from graph import build_graph, finalize
from tests.conftest import action, needs_fixtures
from today import fixtures_now

TARGETS = {"targets": {"calories": 2218, "protein_g": 126, "carbs_g": 290, "fat_g": 62, "fiber_g": 31},
           "inputs": {}, "reason": None}
REMAINING = {"calories": 1838, "protein_g": 101, "carbs_g": 262, "fiber_g": 26}
INTERNAL = re.compile(r"\b[a-z]+_[a-z_]+\b|\bnull\b|\bwindow\b", re.I)
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

def graph_state(llm_plan, overrides=(), split="Pull", oura_band="push"):
    return {"context": {"remaining": REMAINING, "next_split_day": split},
            "baseline": {"band": {"band": oura_band}}, "oura": {"today": {"readiness_score": 82}},
            "goals": {"schedule": {"readiness_floor": 70}}, "plan_source": "coach", "llm_plan": llm_plan}


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
    assert final["food_before"] == "" and final["food_after"] == "Paneer bhurji with 2 rotis and a bowl of dal"
    assert final["why"].startswith("Your body could handle more, but you already trained today")
    assert final["exercises"] == [] and final["split_day"] is None


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
    return Coach(build_graph(InMemorySaver()), use_fixtures=True)


@needs_fixtures
def test_why_today_and_confirmations_are_plain(coach, fake_llm):
    at = lambda h, m=0: fixtures_now(time(h, m))  # noqa: E731
    coach.handle_command("checkin", "", at(7))
    assert_plain(coach.handle_text("2 3 tired", at(7, 1)))
    assert_plain(coach.handle_command("why", "", at(7, 2)))

    fake_llm.routes["had poha"] = [action("food", text="poha")]
    fake_llm.routes["done, felt great"] = [action("workout_done", refers_to_plan=True, feeling="great")]
    fake_llm.routes["traveling Thu-Sat"] = [action("context_note", text="traveling", start_text="Thu",
                                                   end_text="Sat")]
    fake_llm.routes["went for a 30 min walk"] = [action("workout_done", refers_to_plan=False, workout_type="walk",
                                                        duration_min=30)]
    for message in fake_llm.routes:
        assert_plain(coach.handle_text(message, at(9)))
    assert_plain(coach.handle_command("today", "", at(10)))
    assert_plain(coach.handle_command("undo", "", at(10)))


def test_why_with_old_string_overrides_still_works(temp_db):
    session_id = db.start_session(date(2026, 10, 7), "t", datetime(2026, 10, 7, 7, tzinfo=TZ))
    db.finish_session(session_id, {}, plan(), plan(), ["Old plain override text."], {})
    text = format_why(db.get_session(session_id))
    assert "Old plain override text." in text and "Check-in: skipped" in text
