"""Real OpenAI router calls. Skipped unless RUN_LIVE_LLM=1 (costs a few cents, needs OPENAI_API_KEY).

Run: RUN_LIVE_LLM=1 pytest -q tests/test_router_live.py
"""
import os
from datetime import datetime

import pytest

import db
from config import DB_PATH, TZ
from router import route

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1 for live LLM tests")

NOW = datetime(2026, 10, 7, 14, 30, tzinfo=TZ)
OPEN_SESSION = {"plan_json": {"workout_type": "easy_run"}}

CASES = [
    ("had poha and chai", ["food"]),
    ("lunch at 1: rajma chawal", ["food"]),
    ("traveling Thu-Sat, keep it light", ["context_note"]),
    ("want to work out now", ["start_checkin"]),
    ("what should I eat for dinner?", ["question"]),
    ("protein shake after the run, felt great", ["workout_done", "food"]),
]


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    db.set_db_path(tmp_path / "live.db")   # runs are traced; keep them out of oura_coach.db
    db.init_db()
    yield
    db.set_db_path(DB_PATH)


@pytest.mark.parametrize("message,expected", CASES)
def test_router_action_types(message, expected):
    actions = route(message, NOW, pending_checkin=False, open_session=OPEN_SESSION)
    assert sorted(a.type for a in actions) == sorted(expected)


def test_router_extracts_phrases_not_dates():
    [lunch] = route("lunch at 1: rajma chawal", NOW, False, None)
    assert lunch.time_text and "1" in lunch.time_text
    [note] = route("traveling Thu-Sat, keep it light", NOW, False, None)
    assert note.start_text.lower().startswith("thu") and note.end_text.lower().startswith("sat")


def test_walk_with_no_plan_is_a_new_activity():
    [walk] = route("went for a 30 min walk", NOW, False, None)
    assert walk.type == "workout_done" and walk.refers_to_plan is False
    assert walk.workout_type == "walk" and walk.duration_min == 30


def test_why_question_routes_to_why():
    assert [a.type for a in route("why?", NOW, False, None)] == ["why"]


def test_yes_to_the_coachs_question_becomes_a_question():
    [answer] = route("yes", NOW, False, None, last_question="Want a 10-minute stretch you can do anytime?")
    assert answer.type == "question" and "stretch" in answer.text.lower()


def test_live_plan_text_has_no_internal_terms():
    """One real plan on fixtures: what the person reads must be plain words."""
    import re
    from datetime import time

    from langgraph.checkpoint.memory import InMemorySaver

    from graph import build_graph, resume_run, start_run
    from today import fixtures_now

    app, now = build_graph(InMemorySaver()), fixtures_now(time(7, 0))
    state = resume_run(app, start_run(app, now, True)["thread_id"], {"energy": 4, "soreness": 2, "note": None}, now)
    internal = re.compile(r"\b[a-z]+_[a-z_]+\b|\bnull\b|\bwindow\b|\bband\b", re.I)
    plan = state["final_plan"]
    for text in (plan["why"], plan["food_before"], plan["food_after"], plan["follow_up"]):
        assert not internal.findall(text), text
