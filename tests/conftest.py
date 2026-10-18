import json
import sys
import uuid
from pathlib import Path

import icalendar
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import agent, db, users  # noqa: E402
from core.config import DB_PATH, FIXTURES_DIR  # noqa: E402
from modules.nutrition import food  # noqa: E402
from modules.nutrition.schemas import FoodEstimate  # noqa: E402
from modules.training import planner as graph  # noqa: E402
from modules.training.schemas import Plan  # noqa: E402
import today  # noqa: E402

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


@pytest.fixture
def user(temp_db):
    """User 1, seeded by init_db (default settings, America/Chicago)."""
    return users.default()


class FakeLLM:
    """Stands in for llm.structured_call (plan, food): canned answers by call kind, every call recorded."""

    def __init__(self):
        self.plan = Plan(workout_type="HIIT", intensity=5, time_slot="07:00-07:40", duration_min=40,
                         why="Your readiness looks good, so let's push.", reasoning="stub reasoning",
                         food_before="A banana 30 minutes before", food_after="Paneer bhurji with 2 rotis",
                         follow_up="", exercises=[], rules_applied=[])
        self.food_calories = 450.0
        self.calls: list[tuple[str, str]] = []

    def __call__(self, kind, schema, system, user, user_id=None):
        self.calls.append((kind, user))
        if kind == "plan":
            return self.plan
        if kind == "food":
            return FoodEstimate(entry_type="meal", est_protein_g=20, est_fiber_g=6, est_carbs_g=60,
                                est_calories=self.food_calories, est_caffeine_mg=0, alcohol_drinks=0,
                                confidence="med")
        raise AssertionError(f"unexpected LLM call {kind}")


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    for module in (graph, food):
        monkeypatch.setattr(module, "structured_call", fake)
    return fake


@pytest.fixture
def empty_calendar(monkeypatch):
    """Fixtures mode with no calendar events, so the plan window is predictable."""
    monkeypatch.setattr(today, "fixture_calendar", lambda: (icalendar.Calendar(), "empty (test)"))


# --- the agent's chat model, scripted ------------------------------------------

def call(name: str, **args) -> AIMessage:
    """A scripted model turn that calls one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])


def say(text: str) -> AIMessage:
    """A scripted model turn that replies with text."""
    return AIMessage(content=text)


class ScriptedChat(BaseChatModel):
    """The agent's model in tests: returns the next scripted message; records what it was shown."""
    script: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)       # the messages of each call
    tool_names: list = Field(default_factory=list)  # tools offered in the latest bind_tools

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("the agent called the model more often than scripted")
        message = self.script.pop(0)
        return ChatResult(generations=[ChatGeneration(message=message.model_copy())])

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [t.name for t in tools]
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


@pytest.fixture
def chat_model(monkeypatch):
    model = ScriptedChat()
    monkeypatch.setattr(agent, "make_model", lambda: model)
    return model


def tool_results(messages) -> list[dict]:
    """Parsed JSON tool results in a list of messages."""
    return [json.loads(m.content) for m in messages if m.type == "tool" and m.content.startswith("{")]
