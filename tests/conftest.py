import copy
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
from modules.nutrition import meals  # noqa: E402
from modules.nutrition.schemas import FoodEstimate, MealIdeas, MealOption, Portion, Recipe  # noqa: E402
from modules.training import planner as graph  # noqa: E402
from modules.training import checkin  # noqa: E402
from modules.training.schemas import Block, CheckinParse, Plan, Request  # noqa: E402
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


def parsed_checkin(energy=None, soreness=None, blocks=(), feeling=None, constraints=()) -> CheckinParse:
    """A check-in answer as the LLM would read it; blocks = [(workout_type, minutes | None), ...]."""
    request = Request(blocks=[Block(workout_type=t, duration_min=m) for t, m in blocks]) if blocks else None
    return CheckinParse(energy=energy, soreness=soreness, request=request, feeling=feeling,
                        constraints=list(constraints))


def ideas(*dishes: str) -> MealIdeas:
    """Meal ideas as the LLM would give them: one option per dish, with portions."""
    return MealIdeas(options=[MealOption(dish=d, portions=[Portion(food="cooked dal", grams=200, household="1 katori"),
                                                           Portion(food="chapati", grams=80, household="2 chapatis")],
                                         est_calories=550, est_protein_g=25) for d in dishes])


class FakeLLM:
    """Stands in for llm.structured_call (plan, food, checkin, meal, recipe): canned answers by call kind,
    every call recorded. `food_queue` / `meal_queue` give per-call answers in order (then the defaults)."""

    def __init__(self):
        self.plan = Plan(workout_type="HIIT", intensity=5, time_slot="07:00-07:40", duration_min=40,
                         why="Your readiness looks good, so let's push.", reasoning="stub reasoning",
                         food_before="A banana 30 minutes before", food_after="Paneer bhurji with 2 rotis",
                         follow_up="", exercises=[], rules_applied=[], blocks=[])
        self.checkin = parsed_checkin(4, 2)
        self.food_calories = 450.0
        self.food_queue: list[dict] = []          # estimate fields to change for the next food calls
        self.meal = ideas("Dal palak with jeera rice", "Spinach moong dal khichdi")
        self.meal_queue: list[MealIdeas] = []
        self.recipe = Recipe(portions=[Portion(food="moong dal", grams=60, household="1/3 cup dry"),
                                       Portion(food="spinach", grams=100, household="2 cups")],
                             steps=["Rinse the dal and rice.", "Pressure cook with spinach for 3 whistles."],
                             est_calories=520, est_protein_g=24)
        self.calls: list[tuple[str, str]] = []

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def __call__(self, kind, schema, system, user, user_id=None):
        self.calls.append((kind, user))
        if kind == "plan":
            return self.plan
        if kind == "checkin":
            return self.checkin
        if kind == "food":
            fields = {"entry_type": "meal", "est_protein_g": 20, "est_fiber_g": 6, "est_carbs_g": 60,
                      "est_calories": self.food_calories, "est_caffeine_mg": 0, "alcohol_drinks": 0,
                      "confidence": "med"}
            return FoodEstimate(**fields | (self.food_queue.pop(0) if self.food_queue else {}))
        if kind == "meal":
            return self.meal_queue.pop(0) if self.meal_queue else self.meal
        if kind == "recipe":
            return self.recipe
        raise AssertionError(f"unexpected LLM call {kind}")


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    for module in (graph, food, checkin, meals):
        monkeypatch.setattr(module, "structured_call", fake)
    return fake


@pytest.fixture
def patch_oura(monkeypatch):
    """Edit the loaded fixtures for one test: `patch_oura(readiness=64, workouts=[("running", "12:12", "13:03")],
    bedtime="01:30")`.
    Added workouts copy the shape of a real fixtures/workout.json record (same keys), on the fixtures' today.
    Call it before the first Oura read of the test (reads are cached)."""
    from connectors import oura as oura_connector
    raw = copy.deepcopy(oura_connector.load_fixtures())
    day = oura_connector.latest_day(raw).isoformat()
    monkeypatch.setattr(oura_connector, "load_fixtures", lambda: raw)

    def apply(readiness=None, workouts=(), bedtime=None):
        if readiness is not None:
            next(r for r in raw["daily_readiness"]["data"] if r["day"] == day)["score"] = readiness
        if bedtime is not None:      # when today's main sleep started (the night before today, local time)
            sleep = next(r for r in raw["sleep"]["data"] if r["day"] == day and r["type"] == "long_sleep")
            sleep["bedtime_start"] = f"{day}T{bedtime}:00.000-05:00"
        template = raw["workout"]["data"][0]
        for n, (activity, start, end) in enumerate(workouts):
            raw["workout"]["data"].append({**template, "id": f"test-workout-{n}", "activity": activity, "day": day,
                                           "intensity": "moderate", "start_datetime": f"{day}T{start}:00.000-05:00",
                                           "end_datetime": f"{day}T{end}:00.000-05:00"})
        return raw
    return apply


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
