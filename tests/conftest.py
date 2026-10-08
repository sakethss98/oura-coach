import json
import sys
from pathlib import Path

import icalendar
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
import db  # noqa: E402
import food  # noqa: E402
import graph  # noqa: E402
import router  # noqa: E402
import today  # noqa: E402
from config import DB_PATH, FIXTURES_DIR  # noqa: E402
from schemas import Answer, FoodEstimate, Plan, RouterAction, RouterResult  # noqa: E402

needs_fixtures = pytest.mark.skipif(
    not (FIXTURES_DIR / "daily_readiness.json").exists(), reason="fixtures/ not present (gitignored)"
)


@pytest.fixture
def temp_db(tmp_path):
    """Point db.py at a fresh database for one test."""
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield tmp_path / "test.db"
    db.set_db_path(DB_PATH)


def action(type, **fields) -> RouterAction:
    """A router action with every other field null."""
    base = {k: None for k in RouterAction.model_fields if k != "type"}
    return RouterAction(type=type, **{**base, **fields})


class FakeLLM:
    """Stands in for llm.structured_call: canned answers by call kind, every call recorded."""

    def __init__(self):
        self.plan = Plan(workout_type="HIIT", intensity=5, time_slot="07:00-07:40", duration_min=40,
                         why="Your readiness looks good, so let's push.", reasoning="stub reasoning",
                         food_before="A banana 30 minutes before", food_after="Paneer bhurji with 2 rotis",
                         follow_up="", exercises=[], rules_applied=[])
        self.food_calories = 450.0
        self.routes: dict[str, list[RouterAction]] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, kind, schema, system, user):
        self.calls.append((kind, user))
        if kind == "plan":
            return self.plan
        if kind == "food":
            return FoodEstimate(entry_type="meal", est_protein_g=20, est_fiber_g=6, est_carbs_g=60,
                                est_calories=self.food_calories, est_caffeine_mg=0, alcohol_drinks=0,
                                confidence="med")
        if kind == "router":
            message = json.loads(user)["message"]
            return RouterResult(actions=self.routes.get(message, [action("unclear")]))
        if kind == "answer":
            return Answer(reply="stub answer")
        raise AssertionError(f"unexpected LLM call {kind}")


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    for module in (graph, chat, food, router):
        monkeypatch.setattr(module, "structured_call", fake)
    return fake


@pytest.fixture
def empty_calendar(monkeypatch):
    """Fixtures mode with no calendar events, so the plan window is predictable."""
    monkeypatch.setattr(today, "fixture_calendar", lambda: (icalendar.Calendar(), "empty (test)"))
