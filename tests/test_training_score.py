"""The training share of the daily score (pure function, no database)."""
from datetime import datetime, timedelta

from core.config import TZ
from modules.training.activity import manual_items, merge_activity, oura_items, session_items
from modules.training.score import score_day

RULES = {"hard_oura": {"running": {"min_minutes": 60}}, "walk_counts": {"min_minutes": 20, "not_on_bands": ["PUSH"]}}


def t(hour, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=TZ)


def plan(workout_type="easy_run", minutes=45, made=t(7), blocks=None, done=None, plan_id=1):
    """A plans row; `done` = {block index: (start, minutes, done_by)}."""
    p = {"workout_type": workout_type, "intensity": 3, "duration_min": minutes,
         "time_slot": "none" if workout_type == "rest" else f"{made:%H:%M}-{made + timedelta(minutes=minutes):%H:%M}"}
    if blocks:
        p["blocks"] = [{"workout_type": b, "intensity": 3, "duration_min": m} for b, m in blocks]
        p["duration_min"] = sum(m for _, m in blocks)
    blocks_done = {i: {"start": s.isoformat(), "end": (s + timedelta(minutes=m)).isoformat(), "minutes": m,
                       "done_by": by} for i, (s, m, by) in (done or {}).items()}
    return {"id": plan_id, "checkin_at": made.isoformat(), "plan_json": p, "blocks_done": blocks_done,
            "completed": 1 if done and len(done) == len(blocks or [None]) else None, "feeling": None,
            "actual_start": None, "actual_end": None, "done_by": None}


def oura(activity, start, minutes):
    return {"activity": activity, "intensity": "moderate", "start": start.isoformat(),
            "end": (start + timedelta(minutes=minutes)).isoformat(), "minutes": minutes}


def manual(workout_type, start, minutes):
    return {"id": 9, "workout_type": workout_type, "start": start.isoformat(),
            "end": (start + timedelta(minutes=minutes)).isoformat(), "minutes": minutes, "feeling": None}


def score(plans, oura_list=(), manual_list=()):
    """The day's merged activity, built the way today.day_activity builds it."""
    items = merge_activity(oura_items(list(oura_list), RULES),
                           session_items(plans, RULES) + manual_items(list(manual_list), RULES))
    return score_day(plans, items, RULES)


def test_done_as_planned_is_full_credit():
    s = score([plan(done={0: (t(7, 5), 45, "oura")})])
    assert (s.fraction, s.note) == (1.0, "easy run done")


def test_seventy_percent_of_the_minutes_is_enough():
    assert score([plan(done={0: (t(7, 5), 32, "oura")})]).fraction == 1.0
    s = score([plan(done={0: (t(7, 5), 25, "oura")})])
    assert (s.fraction, s.note) == (0.5, "easy run partly done (25 of 45 min)")


def test_blocks_are_credited_separately_and_capped_at_their_planned_minutes():
    blocks = [("easy_run", 20), ("strength", 30)]
    s = score([plan(blocks=blocks, done={0: (t(7), 60, "oura")})])          # a long run, strength skipped
    assert (s.fraction, s.note) == (0.5, "easy run + strength partly done (20 of 50 min)")
    s = score([plan(blocks=blocks, done={0: (t(7), 20, "oura"), 1: (t(7, 20), 30, "chat")})])
    assert s.fraction == 1.0


def test_a_different_workout_after_the_plan_is_half():
    s = score([plan()], manual_list=[manual("walk", t(9), 30)])
    assert (s.fraction, s.note) == (0.5, "30-min walk at 9:00am instead of the planned easy run")


def test_not_done_is_zero():
    s = score([plan()], manual_list=[manual("walk", t(9), 10)])            # a 10-min walk doesn't count
    assert (s.fraction, s.note) == (0.0, "easy run not done")


def test_rest_is_kept_unless_a_hard_workout_starts_after_the_rest_plan():
    rest = plan("rest", 0, made=t(12))
    assert score([rest], oura_list=[oura("running", t(7), 70)]).fraction == 1.0        # before the plan
    s = score([rest], oura_list=[oura("running", t(17), 70)])
    assert (s.fraction, s.note) == (0.0, "hard workout on a rest day (70-min run at 5:00pm)")


def test_a_workout_before_the_first_plan_counts_scenario_23():
    walk_plan = plan("walk", 30, made=t(18))                               # evening plan, not done
    s = score([walk_plan], manual_list=[manual("HIIT", t(7), 40)])
    assert (s.fraction, s.note) == (1.0, "40-min HIIT at 7:00am done")


def test_the_best_plan_of_the_day_counts():
    morning = plan("HIIT", 30, made=t(7), done={0: (t(7), 30, "chat")}, plan_id=1)
    evening = plan("walk", 30, made=t(18), plan_id=2)
    s = score([morning, evening])
    assert (s.fraction, s.note) == (1.0, "HIIT done")
    assert [p["fraction"] for p in s.details["plans"]] == [1.0, 0.0]       # the HIIT came before the walk plan


def test_no_plan_scores_any_counting_workout():
    assert score([], oura_list=[oura("walking", t(9), 25)]).fraction == 1.0
    assert score([], oura_list=[oura("walking", t(9), 15)]).fraction == 0.0
    s = score([])
    assert (s.fraction, s.note) == (0.0, "no workout")


def test_same_inputs_same_score():
    plans = [plan(done={0: (t(7, 5), 25, "oura")})]
    assert score(plans) == score(plans)


def test_a_late_or_no_time_rest_is_not_a_rest_day():
    morning = plan("easy_run", 45, made=t(7), plan_id=1)                    # planned, never done
    late_rest = {**plan("rest", 0, made=t(22), plan_id=2), "details_json": {"plan_source": "code"}}
    s = score([morning, late_rest])
    assert (s.fraction, s.note) == (0.0, "easy run not done")              # the late check-in erases nothing
    coach_rest = {**plan("rest", 0, made=t(7), plan_id=3), "details_json": {"plan_source": "coach"}}
    assert score([coach_rest]).fraction == 1.0
