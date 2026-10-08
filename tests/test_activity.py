from datetime import datetime

from activity import counts_as_workout, hard_reason, merge_activity, oura_items, session_items, worked_out
from config import TZ

RULES = {
    "hard_oura": {"running": {"min_minutes": 60}},
    "walk_counts": {"min_minutes": 20, "not_on_bands": ["PUSH"]},
}


def t(hour, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=TZ)


def oura(activity, start, minutes):
    end = start.replace(hour=start.hour + (start.minute + minutes) // 60, minute=(start.minute + minutes) % 60)
    return {"activity": activity, "intensity": "moderate", "start": start.isoformat(), "end": end.isoformat(),
            "minutes": minutes}


def session(workout_type, start=None, end=None, completed=1, intensity=2, duration=None, source="planned"):
    return {"id": 1, "completed": completed, "feeling": None, "source": source,
            "plan_json": {"workout_type": workout_type, "intensity": intensity, "duration_min": duration},
            "actual_start": start and start.isoformat(), "actual_end": end and end.isoformat()}


def items(oura_list=(), sessions=()):
    return merge_activity(oura_items(list(oura_list), RULES), session_items(list(sessions), RULES))


def test_overlapping_oura_run_and_done_session_merge():
    merged = items([oura("running", t(7), 35)], [session("easy_run", t(7, 10), t(7, 45))])
    assert len(merged) == 1
    assert merged[0]["source"] == "both"
    assert merged[0]["minutes"] == 35


def test_no_overlap_keeps_both():
    merged = items([oura("running", t(7), 35)], [session("easy_run", t(17), t(17, 30))])
    assert len(merged) == 2


def test_running_hard_boundary():
    assert not items([oura("running", t(7), 59)])[0]["hard"]
    assert items([oura("running", t(7), 60)])[0]["hard"]


def test_strength_and_yoga_count_but_are_not_hard():
    merged = items([oura("strengthTraining", t(7), 15), oura("yoga", t(9), 10)])
    assert hard_reason(merged) is None
    assert worked_out(merged, "push", RULES)


def test_unmarked_plan_is_ignored():
    assert items(sessions=[session("HIIT", completed=None)]) == []


def test_done_hiit_session_is_hard():
    assert "HIIT" in hard_reason(items(sessions=[session("HIIT", t(7), t(7, 40), intensity=5)]))


def test_unplanned_without_times_still_listed():
    merged = items(sessions=[session("yoga", duration=None, source="unplanned")])
    assert len(merged) == 1 and merged[0]["start"] is None


# --- walk rule: 20+ min counts as a light workout, never hard, not on PUSH days ---

def test_oura_walk_minimum_length():
    short, long_ = items([oura("walking", t(7), 19)])[0], items([oura("walking", t(7), 20)])[0]
    assert not counts_as_workout(short, "maintain", RULES)
    assert counts_as_workout(long_, "maintain", RULES)


def test_oura_walk_counts_except_on_push_days():
    walk = items([oura("walking", t(7), 30)])
    assert worked_out(walk, "maintain", RULES)
    assert worked_out(walk, "recover", RULES)
    assert worked_out(walk, "push", RULES) is None
    assert hard_reason(walk) is None


def test_chat_walk_minimum_length():
    short = items(sessions=[session("walk", t(7), t(7, 19), source="unplanned")])[0]
    long_ = items(sessions=[session("walk", t(7), t(7, 20), source="unplanned")])[0]
    assert not counts_as_workout(short, "maintain", RULES)
    assert counts_as_workout(long_, "maintain", RULES)


def test_chat_walk_counts_except_on_push_days():
    walk = items(sessions=[session("walk", t(16), t(16, 30), source="unplanned")])
    assert worked_out(walk, "maintain", RULES)
    assert worked_out(walk, "PUSH", RULES) is None
    assert worked_out(walk, "push", RULES) is None
    assert hard_reason(walk) is None
