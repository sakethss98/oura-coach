"""The check-in graph on fixtures: interrupt, resume, abandon, restart. LLM stubbed."""
import sqlite3
from datetime import time, timedelta

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

import db
from graph import build_graph, resume_run, start_run
from tests.conftest import needs_fixtures
from today import fixtures_now

pytestmark = needs_fixtures
ANSWER = {"energy": 4, "soreness": 2, "note": "legs fine"}


def test_checkin_interrupts_then_resumes_to_a_plan(temp_db, fake_llm, empty_calendar):
    app = build_graph(InMemorySaver())
    now = fixtures_now(time(7, 0))

    waiting = start_run(app, now, use_fixtures=True)
    assert "energy (1-5)" in waiting["question"]
    pending = db.pending_session()
    assert pending["thread_id"] == waiting["thread_id"] == f"checkin-{now:%Y-%m-%d-%H%M}"
    assert not any(kind == "plan" for kind, _ in fake_llm.calls)      # nothing planned before the answer

    state = resume_run(app, waiting["thread_id"], ANSWER, now + timedelta(minutes=2))
    assert state["context"]["now"] == "07:02"                         # planned for the answer time
    assert state["final_plan"]["time_slot"].startswith("07:02")       # empty calendar: window starts now
    session = db.get_session(pending["id"])
    assert session["status"] == "planned"
    assert (session["energy"], session["soreness"], session["note"]) == (4, 2, "legs fine")
    assert session["plan_json"] == state["final_plan"]
    assert db.get_daily_log(now.date())["food_targets_json"]


def test_new_checkin_abandons_the_pending_one(temp_db, fake_llm, empty_calendar):
    app = build_graph(InMemorySaver())
    first = start_run(app, fixtures_now(time(7, 0)), use_fixtures=True)
    second = start_run(app, fixtures_now(time(7, 0)), use_fixtures=True)    # same minute: unique thread id
    assert second["thread_id"] == first["thread_id"] + "-2"
    assert db.pending_session()["thread_id"] == second["thread_id"]
    statuses = {s["thread_id"]: s["status"] for s in [db.get_session(i) for i in (1, 2)]}
    assert statuses == {first["thread_id"]: "abandoned", second["thread_id"]: "awaiting_checkin"}


def test_restart_mid_checkin_resumes_from_sqlite(temp_db, fake_llm, empty_calendar, tmp_path):
    now = fixtures_now(time(7, 0))
    conn = sqlite3.connect(tmp_path / "checkpoints.db", check_same_thread=False)
    waiting = start_run(build_graph(SqliteSaver(conn)), now, use_fixtures=True)
    conn.close()                                                       # "bot restarts"

    conn = sqlite3.connect(tmp_path / "checkpoints.db", check_same_thread=False)
    state = resume_run(build_graph(SqliteSaver(conn)), waiting["thread_id"], ANSWER, now)
    conn.close()
    assert state["final_plan"]["workout_type"]
    assert db.pending_session() is None


def test_second_plan_after_a_done_workout_is_light_and_has_no_meal_ideas(temp_db, fake_llm, empty_calendar):
    app = build_graph(InMemorySaver())
    morning = fixtures_now(time(7, 0))
    first = resume_run(app, start_run(app, morning, True)["thread_id"], ANSWER, morning)
    assert first["context"]["first_plan_today"] and first["final_plan"]["meal_ideas"]
    session = db.latest_open_session(morning.date())
    db.mark_done(session["id"], "heavy", morning + timedelta(hours=1), morning, morning + timedelta(minutes=40))

    evening = fixtures_now(time(18, 0))
    second = resume_run(app, start_run(app, evening, True)["thread_id"], ANSWER, evening)
    assert not second["context"]["first_plan_today"]
    assert second["final_plan"]["meal_ideas"] == []
    assert second["final_plan"]["workout_type"] == "walk"             # stub proposed HIIT
    assert any("Already worked out today" in o for o in second["overrides"])
    assert second["context"]["remaining"] is not None
