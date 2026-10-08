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


# --- Oura completes an open plan (matching type, planned slot +/- 60 min) -------

from activity import match_open_plans, next_split_day  # noqa: E402


def open_plan(workout_type, slot="06:30-07:00", completed=None, split_day=None):
    return {"id": 7, "date": "2026-10-07", "completed": completed, "feeling": None, "source": "planned",
            "plan_json": {"workout_type": workout_type, "intensity": 3, "time_slot": slot, "split_day": split_day},
            "actual_start": None, "actual_end": None}


def test_oura_run_near_the_planned_time_completes_a_run_plan():
    assert len(match_open_plans([open_plan("easy_run")], oura_items([oura("running", t(7, 20), 30)], RULES))) == 1


def test_oura_run_more_than_an_hour_after_the_slot_does_not():
    assert match_open_plans([open_plan("easy_run")], oura_items([oura("running", t(8, 40), 30)], RULES)) == []


def test_oura_walk_does_not_complete_a_run_plan():
    assert match_open_plans([open_plan("easy_run")], oura_items([oura("walking", t(6, 40), 30)], RULES)) == []


def test_hiit_plan_matches_any_oura_activity():
    assert match_open_plans([open_plan("HIIT")], oura_items([oura("strengthTraining", t(6, 35), 25)], RULES))


def test_done_or_rest_plans_are_not_matched():
    workouts = oura_items([oura("running", t(6, 35), 25)], RULES)
    assert match_open_plans([open_plan("easy_run", completed=1), open_plan("rest", slot="none")], workouts) == []


# --- strength split rotation ------------------------------------------------

SPLIT = ["Push", "Pull", "Legs"]


def strength(split_day, completed=1):
    return open_plan("strength", completed=completed, split_day=split_day)


def test_split_starts_at_push():
    assert next_split_day([], SPLIT) == "Push"


def test_split_rotates_from_the_last_done_session():
    assert next_split_day([strength("Push")], SPLIT) == "Pull"
    assert next_split_day([strength("Push"), strength("Pull")], SPLIT) == "Legs"
    assert next_split_day([strength("Legs")], SPLIT) == "Push"


def test_split_skips_sessions_without_a_day_or_not_done():
    assert next_split_day([strength("Push"), strength(None)], SPLIT) == "Pull"
    assert next_split_day([strength("Push"), strength("Pull", completed=None)], SPLIT) == "Pull"


def test_walk_rule_uses_the_band_it_is_given():
    # graph passes the check-in-adjusted band: a PUSH day lowered to maintain makes a 30-min walk count
    walk = items([oura("walking", t(7), 30)])
    assert worked_out(walk, "push", RULES) is None
    assert worked_out(walk, "maintain", RULES) == "30-min walk at 7:00am"
