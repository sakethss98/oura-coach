"""The training planner on fixtures (no interrupt: the check-in is known before it runs). LLM stubbed."""
from datetime import time, timedelta

from core import db, users
from modules.training.planner import build_graph, run_plan
from tests.conftest import needs_fixtures
from today import fixtures_now

pytestmark = needs_fixtures
ANSWER = {"energy": 4, "soreness": 2, "note": "legs fine", "label": "just now"}


def test_known_checkin_plans_for_now_and_saves_the_plan(temp_db, fake_llm, empty_calendar):
    now = fixtures_now(time(7, 2))
    state = run_plan(build_graph(), users.default(), now, True, ANSWER)
    assert state["context"]["now"] == "07:02"
    assert state["final_plan"]["time_slot"].startswith("07:02")       # empty calendar: window starts now
    plan = db.get_plan(state["plan_id"])
    assert plan["status"] == "planned"
    assert (plan["energy"], plan["soreness"], plan["note"]) == (4, 2, "legs fine")
    assert plan["plan_json"] == state["final_plan"]
    assert db.get_daily_log(1, now.date())["food_targets_json"]


def test_answered_checkin_fills_its_pending_row(temp_db, fake_llm, empty_calendar):
    now = fixtures_now(time(7, 0))
    pending_id = db.start_plan(1, now.date(), "checkin-x", now, chat_session_id=5)
    state = run_plan(build_graph(), users.default(), now + timedelta(minutes=1), True, ANSWER, pending_id)
    assert state["plan_id"] == pending_id
    assert db.pending_plan(1) is None
    assert db.get_plan(pending_id)["status"] == "planned"


def test_second_plan_after_a_done_workout_is_light_and_has_no_meal_ideas(temp_db, fake_llm, empty_calendar):
    app, user = build_graph(), users.default()
    morning = fixtures_now(time(7, 0))
    first = run_plan(app, user, morning, True, ANSWER)
    assert first["context"]["first_plan_today"]
    db.mark_done(first["plan_id"], "heavy", morning + timedelta(hours=1), morning, morning + timedelta(minutes=40))

    second = run_plan(app, user, fixtures_now(time(18, 0)), True, ANSWER)
    assert not second["context"]["first_plan_today"]
    assert second["final_plan"]["workout_type"] == "walk"             # stub proposed HIIT
    assert any(o["rule"] == "worked_out" for o in second["overrides"])
    assert second["final_plan"]["food_source"] == "code"              # food lines match the walk
    assert second["final_plan"]["food_before"] == ""
    assert second["context"]["remaining"] is not None


def test_nodes_in_order(temp_db, fake_llm, empty_calendar):
    names = []
    for update in build_graph().stream({"user_id": 1, "use_fixtures": True, "now": fixtures_now(time(7, 0)).isoformat(),
                                        "checkin": ANSWER}, stream_mode="updates"):
        names.extend(update)
    assert names == ["fetch_data", "compute_baseline", "gather_today", "plan", "policy_check", "log"]


def test_no_time_left_takes_rest_plan_without_an_llm_call(temp_db, fake_llm, empty_calendar):
    now = fixtures_now(time(20, 45))                       # 15 min before 21:00: no usable time
    state = run_plan(build_graph(), users.default(), now, True, ANSWER)
    assert fake_llm.calls == []                            # no plan call at all
    assert state["plan_source"] == "code"
    assert state["final_plan"]["workout_type"] == "rest"
    assert state["final_plan"]["why"] == "Your body could handle more, but there's no free time left today."
    assert state["final_plan"]["follow_up"]
    plan = db.latest_planned(1, now.date())
    assert plan["details_json"]["plan_source"] == "code"
    assert plan["details_json"]["window"] is None


def test_checkin_lowers_the_band_before_planning(temp_db, fake_llm, empty_calendar):
    now = fixtures_now(time(7, 0))
    state = run_plan(build_graph(), users.default(), now, True, {"energy": 2, "soreness": 2, "note": None})
    assert state["band"]["band"] == "recover" and state["band"]["changed_by_checkin"]
    assert '"level": "recover"' in fake_llm.calls[-1][1]   # the LLM sees the adjusted level
    assert state["final_plan"]["workout_type"] == "walk"
    assert all(o["rule"] == "checkin" for o in state["overrides"] if o["changes_type"])
