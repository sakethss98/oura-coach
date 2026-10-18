"""run.py still works end to end on fixtures (LLM stubbed, temp demo databases)."""

import pytest

from core import db
import run
from core.config import DB_PATH
from tests.conftest import needs_fixtures

pytestmark = needs_fixtures


@pytest.fixture
def temp_demo(tmp_path, monkeypatch, fake_llm, empty_calendar):
    monkeypatch.setattr(run, "DEMO_DB_PATH", tmp_path / "demo.db")
    yield tmp_path
    db.set_db_path(DB_PATH)


def test_run_with_checkin_flags(temp_demo, capsys):
    state = run.main(["--fixtures", "--at", "07:00", "--energy", "4", "--soreness", "2"])
    assert state["checkin"]["energy"] == 4
    assert state["final_plan"]["time_slot"].startswith("07:00")
    out = capsys.readouterr().out
    assert "Window:    07:00-21:00" in out and "Workout:" in out
    assert db.db_path() == temp_demo / "demo.db"                      # fixtures runs never touch oura_coach.db


def test_simulate_hard_yesterday_triggers_override_and_cleans_up(temp_demo, fake_llm):
    state = run.main(["--fixtures", "--at", "07:00", "--simulate-hard-yesterday"])
    assert any("back-to-back" in o["text"] for o in state["overrides"])
    assert state["final_plan"]["workout_type"] == "easy_run"
    assert "yesterday_hard" not in fake_llm.calls[-1][1]              # hidden from the LLM
    with db.connect() as conn:                                        # simulated workout deleted
        assert conn.execute("SELECT COUNT(*) FROM manual_workouts").fetchone()[0] == 0
