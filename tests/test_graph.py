"""The training planner on fixtures (no interrupt: the check-in is known before it runs). LLM stubbed."""
from datetime import time, timedelta

import pytest

from core import db, users
from modules.training.planner import answer_consult, build_graph, run_plan
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


# --- requests, the consult question, late check-ins (M3.2 Phase 3) -------------------------

from datetime import date  # noqa: E402

from core.config import TZ  # noqa: E402
from modules.training.planner import LATE_FOLLOW_UP, LATE_WHY  # noqa: E402
from modules.training.schemas import PlanBlock  # noqa: E402
from modules.training.tools import CONSULT_TTL  # noqa: E402

HIIT = [{"workout_type": "HIIT", "duration_min": None}]
FRESH = {"energy": 4, "soreness": 1, "note": None, "constraints": [], "label": "just now"}


def plan_at(hour, minute=0, checkin=FRESH, **kwargs):
    return run_plan(build_graph(), users.default(), fixtures_now(time(hour, minute)), True, checkin, **kwargs)


def test_a_request_with_no_conflict_is_planned_in_its_order(temp_db, fake_llm, empty_calendar):
    fake_llm.plan = fake_llm.plan.model_copy(update={"blocks": [
        PlanBlock(workout_type="strength", intensity=3, duration_min=35),
        PlanBlock(workout_type="easy_run", intensity=3, duration_min=25)]})
    request = [{"workout_type": "easy_run", "duration_min": None}, {"workout_type": "strength", "duration_min": None}]
    state = plan_at(7, request=request)
    blocks = state["final_plan"]["blocks"]
    assert [(b["workout_type"], b["duration_min"]) for b in blocks] == [("easy_run", 25), ("strength", 35)]
    assert state["final_plan"]["time_slot"] == "07:00-08:00"
    assert '"requested"' in fake_llm.calls[-1][1]


NUDGES = {
    "HIIT on a recovery day": (64, HIIT,
                               "Readiness is 64 today, lower than usual, so I'd keep it light.\n"
                               "I'd suggest a 30-min walk instead. Still want HIIT?"),
    "a long run on a weekday": (None, [{"workout_type": "long_run", "duration_min": None}],
                                "Long runs are for the weekend, so it's an easy run today.\n"
                                "I'd suggest a 75-min easy run instead. Still want a long run?"),
    "strength on a low-readiness day": (64, [{"workout_type": "strength", "duration_min": 40}],
                                        "Readiness is 64 today, lower than usual, so I'd keep it light.\n"
                                        "I'd suggest a 40-min walk instead. Still want 40 min of strength?"),
}


@pytest.mark.parametrize("case", NUDGES)
def test_a_conflicting_request_gets_one_question_and_no_llm_call(temp_db, fake_llm, empty_calendar, patch_oura,
                                                                 case):
    readiness, request, question = NUDGES[case]
    patch_oura(readiness=readiness)
    state = plan_at(7, request=request)
    assert state["consult"] == question
    assert fake_llm.calls == [] and "final_plan" not in state
    pending = db.pending_confirm(1)
    assert pending["id"] == state["plan_id"] and pending["requested_json"]["request"] == request


def test_yes_plans_the_request_and_records_the_skipped_rules(temp_db, fake_llm, empty_calendar, patch_oura):
    patch_oura(readiness=64)
    plan_at(7, request=HIIT)
    first = db.pending_confirm(1)
    state = answer_consult(build_graph(), users.default(), fixtures_now(time(7, 2)), True, first, "yes")
    assert "consult" not in state
    assert state["final_plan"]["workout_type"] == "HIIT"
    assert {o["skipped"] for o in state["overrides"] if o["rule"] == "user_choice"} == {"readiness_floor"}
    assert db.get_plan(first["id"])["status"] == "planned" and db.pending_confirm(1) is None


def test_no_plans_the_suggestion_as_offered_without_an_llm_call(temp_db, fake_llm, empty_calendar, patch_oura):
    patch_oura(readiness=64)
    plan_at(7, request=HIIT)
    pending = db.pending_confirm(1)
    state = answer_consult(build_graph(), users.default(), fixtures_now(time(7, 20)), True, pending, "no")
    final = state["final_plan"]
    assert "consult" not in state and fake_llm.calls == []
    assert (final["workout_type"], final["duration_min"], final["time_slot"]) == ("walk", 30, "07:20-07:50")
    assert final["why"] == "Readiness is 64 today, lower than usual, so I'd keep it light."
    assert final["food_before"] == "" and final["food_source"] == "code"
    assert not any(o["rule"] == "user_choice" for o in state["overrides"])
    assert db.get_plan(pending["id"])["status"] == "planned"


def test_when_the_coach_ignores_the_request_its_words_are_replaced(temp_db, fake_llm, empty_calendar):
    state = plan_at(7, request=[{"workout_type": "strength", "duration_min": 45}])   # the stub proposes HIIT
    final = state["final_plan"]
    assert final["workout_type"] == "strength" and final["why"] == "You asked for 45 min of strength."
    assert final["food_source"] == "code"


def test_an_unanswered_question_expires_after_two_hours_with_no_plan(temp_db, fake_llm, empty_calendar, patch_oura):
    patch_oura(readiness=64)
    first = plan_at(7, request=HIIT)
    asked = fixtures_now(time(7, 0))
    assert db.expire_consults(1, asked + CONSULT_TTL - timedelta(minutes=1), CONSULT_TTL) == []
    [expired] = db.expire_consults(1, asked + CONSULT_TTL, CONSULT_TTL)
    saved = db.get_plan(first["plan_id"])
    assert (saved["status"], saved["plan_json"]) == ("expired", None)              # nothing was planned
    assert db.pending_confirm(1) is None and db.plans_between(1, asked.date(), asked.date()) == []
    assert db.expired_consult(1, asked.date())["id"] == first["plan_id"]
    assert fake_llm.calls == []


def test_a_request_with_no_time_left_says_it_did_not_fit(temp_db, fake_llm, empty_calendar):
    state = plan_at(20, 45, request=HIIT)
    assert state["final_plan"]["workout_type"] == "rest"
    assert state["final_plan"]["why"] == "There's no free time left today for HIIT."
    assert fake_llm.calls == []


@pytest.mark.parametrize("hour, minute, day", [(21, 30, date(2026, 10, 7)), (22, 30, date(2026, 10, 7)),
                                               (0, 30, date(2026, 10, 6)), (5, 0, date(2026, 10, 7))])
def test_late_or_before_the_morning_is_rest_with_a_sleep_note(temp_db, fake_llm, empty_calendar, hour, minute, day):
    state = plan_at(hour, minute, request=HIIT)
    final = state["final_plan"]
    assert (final["workout_type"], final["why"], final["follow_up"]) == ("rest", LATE_WHY, LATE_FOLLOW_UP)
    assert fake_llm.calls == [] and "consult" not in state
    assert db.get_plan(state["plan_id"])["date"] == day.isoformat()     # 00:30 is before that night's sleep


def test_a_new_checkin_abandons_the_open_plan(temp_db, fake_llm, empty_calendar):
    first = plan_at(11)
    second = plan_at(11, 20)
    assert db.get_plan(first["plan_id"])["status"] == "abandoned"
    assert [p["id"] for p in db.open_plans(1, fixtures_now(time(12)).date())] == [second["plan_id"]]


def test_a_plan_oura_recorded_is_done_not_abandoned(temp_db, fake_llm, empty_calendar, patch_oura):
    patch_oura()
    first = plan_at(7)                                                  # HIIT 07:00-07:40
    patch_oura(workouts=[("running", "07:05", "07:45")])                # Oura syncs the workout later
    second = plan_at(11)                                                # cache is stale: Oura read again
    done = db.get_plan(first["plan_id"])
    assert (done["status"], done["completed"], done["done_by"]) == ("planned", 1, "oura")
    assert done["actual_start"].startswith("2026-10-07T07:05")
    assert db.get_plan(second["plan_id"])["status"] == "planned"


def test_a_late_checkin_leaves_the_days_plans_alone(temp_db, fake_llm, empty_calendar):
    morning = plan_at(7)
    late = plan_at(22, 30)
    assert db.get_plan(morning["plan_id"])["status"] == "planned"
    assert [p["id"] for p in db.plans_between(1, date(2026, 10, 7), date(2026, 10, 7))] == [
        morning["plan_id"], late["plan_id"]]
