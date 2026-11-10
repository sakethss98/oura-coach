import copy
import json
import os
import re
import sys
import uuid
from pathlib import Path

# No LangSmith traces from tests unless asked for (LANGSMITH_TESTS=1). Set before core.config runs
# load_dotenv, which never overrides a variable that is already set.
if not os.getenv("LANGSMITH_TESTS"):
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

import icalendar
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import agent, citations, classifier, db, reply_rules, users  # noqa: E402
from core.config import DB_PATH, FIXTURES_DIR  # noqa: E402
from modules.nutrition import food  # noqa: E402
from modules.nutrition import meals  # noqa: E402
from modules.nutrition.schemas import FoodEstimate, MealIdeas, MealOption, Portion, Recipe  # noqa: E402
from modules.training import planner as graph  # noqa: E402
from modules.training import checkin  # noqa: E402
from modules.training.schemas import Block, CheckinParse, Plan, Request  # noqa: E402
import today  # noqa: E402
from core import mcp_tools  # noqa: E402

needs_fixtures = pytest.mark.skipif(
    not (FIXTURES_DIR / "daily_readiness.json").exists(), reason="fixtures/ not present (gitignored)"
)


# --- live scenarios: run N times, report pass rates (docs/scenarios.md "How to grade") ----------------

LIVE_RUNS = int(os.getenv("LIVE_RUNS", "1"))      # LIVE_RUNS=3: every live scenario runs 3 times


def live(test):
    """A live scenario: skipped unless RUN_LIVE_LLM=1, run LIVE_RUNS times (parameter `attempt`)."""
    test = pytest.mark.usefixtures("attempt")(pytest.mark.live_llm(test))
    return pytest.mark.skipif(not os.getenv("RUN_LIVE_LLM"), reason="set RUN_LIVE_LLM=1 to call OpenAI")(test)


core = pytest.mark.core          # a ★ scenario: must pass every run
real_mcp = pytest.mark.real_mcp  # starts the real MCP server (tests/test_mcp_tools.py)
real_embeddings = pytest.mark.real_embeddings  # the citation check with real embeddings (tests/test_citation_check.py)


def pytest_configure(config):
    config.addinivalue_line("markers", "live_llm: a scenario against the real model")
    config.addinivalue_line("markers", "core: a ★ scenario (must pass every live run)")
    config.addinivalue_line("markers", "real_mcp: uses the real MCP server instead of the scripted search results")
    config.addinivalue_line("markers", "real_embeddings: the citation check calls the embeddings API")


def pytest_generate_tests(metafunc):
    if "attempt" in metafunc.fixturenames:
        metafunc.parametrize("attempt", range(1, LIVE_RUNS + 1), ids=lambda n: f"run{n}")


def pytest_terminal_summary(terminalreporter):
    """Pass rate per live scenario (★ = core)."""
    rates = {}
    for status in ("passed", "failed", "xfailed", "xpassed"):
        for report in terminalreporter.stats.get(status, []):
            if getattr(report, "when", "call") != "call" or "live_llm" not in report.keywords:
                continue
            name = re.sub(r"\[run\d+\]|run\d+-|-run\d+", "", report.nodeid.split("::")[-1])
            entry = rates.setdefault(name, {"passed": 0, "runs": 0, "core": "core" in report.keywords, "m4": False})
            entry["runs"] += 1
            entry["passed"] += status in ("passed", "xpassed")
            entry["m4"] |= status in ("xfailed", "xpassed")
    if not rates:
        return
    terminalreporter.write_sep("-", f"live scenarios, pass rate (LIVE_RUNS={LIVE_RUNS}, ★ = must pass every run)")
    for name, r in sorted(rates.items()):
        mark = "★" if r["core"] else "M4" if r["m4"] else " "
        terminalreporter.write_line(f"{mark:>2} {r['passed']}/{r['runs']}  {name}")


# --- LLM judge: only "did it answer what was asked" ----------------------------------------------

class Verdict(BaseModel):
    answered: bool = Field(description="true if the reply answers what they asked or does what they asked")
    reason: str = Field(description="one short sentence")


JUDGE = ("You check one reply from a coaching chatbot. The only question: did the reply answer what the person "
         "asked, or do what they asked? Judge only that, not tone, length or style. Answer false if it dodges, "
         "answers a different question, or only asks a question back when no clarification was needed.")


def judge(message: str, reply: str) -> Verdict:
    """The yes/no rubric from docs/scenarios.md (a real model call; live tests only)."""
    from core.llm import structured_call
    return structured_call("judge", Verdict, JUDGE, f"They wrote: {message}\nThe reply: {reply}", job="judge")


class CitationVerdict(BaseModel):
    supported: bool = Field(description="true if every claim marked [n] in the reply is supported by passage n")
    unsupported: list[str] = Field(description="the [n]-marked claims that passage n does not support (empty if none)")
    reason: str = Field(description="one short sentence")


CITATION_JUDGE = ("You check citations in one reply from a coaching chatbot. Claims in the reply are marked with [n]; "
                  "passage n is given. For each marked claim, decide whether passage n supports it (it says so, or it "
                  "clearly follows). Advice about the person's own day (readiness, their week) needs no support. "
                  "A claim attributed to the source that the passage doesn't contain is unsupported.")


def judge_citations(reply: str, sources: list[dict]) -> CitationVerdict:
    """G rubric check: "Is every [n]-cited claim supported by passage n?" (a real model call; live tests only)."""
    from core.llm import structured_call
    passages = "\n\n".join(f"[{s['n']}] {s['title']}: {s['passage']}" for s in sources)
    return structured_call("judge", CitationVerdict, CITATION_JUDGE, f"Passages:\n{passages}\n\nThe reply: {reply}",
                           job="judge")


class Pushback(BaseModel):
    noticed: bool = Field(description="true if it names what it noticed about their plan or their day")
    honest_call: bool = Field(description="true if it says plainly, and kindly, that the plan works against them")
    options: bool = Field(description="true if it offers one better option or two options")
    at_most_two_options: bool = Field(description="true if it offers at most two options (one suggestion, or two "
                                                  "choices); false for three or more")
    their_choice: bool = Field(description="true if it leaves the decision to them, e.g. a short question")
    guilt: bool = Field(description="true if it blames, shames or guilts them (missed, failed, should have, lazy)")
    reason: str = Field(description="one short sentence")


PUSHBACK_JUDGE = ("You check one reply from a running and nutrition coach. The person proposed something that works "
                  "against them. Answer each check yes or no about the reply only.")


def judge_pushback(message: str, reply: str) -> Pushback:
    """The pushback rubric (docs/scenarios.md M4-8..10): noticed, honest call, options (at most two), their
    choice, no guilt."""
    from core.llm import structured_call
    return structured_call("judge", Pushback, PUSHBACK_JUDGE, f"They wrote: {message}\nThe reply: {reply}",
                           job="judge")


# --- knowledge search: scripted tests never start an MCP server ---------------------------------------

GSSI_URLS = {
    "carbs": "https://www.gssiweb.org/en/sports-science-exchange/article/dietary-carbohydrate-and-the-endurance-athlete-contemporary-perspectives",
    "drinking": "https://www.gssiweb.org/en/sports-science-exchange/article/fluid-intake-strategies-for-optimal-hydration-and-performance-planned-drinking-vs.-drinking-to-thirst",
    "caffeine": "https://www.gssiweb.org/en/sports-science-exchange/article/caffeine-and-exercise-performance-an-update",
}
# Made-up passages (never article text) with real article urls, so scripted tests can check citing and copying.
FAKE_SOURCES = [
    {"title": "Carbohydrate (test)", "url": GSSI_URLS["carbs"], "section": "Before", "year": 2020,
     "passage": "Test passage one says that runners who eat enough carbohydrate the day before a long run tend to "
                "feel stronger in the last part of the run than runners who eat less."},
    {"title": "Drinking (test)", "url": GSSI_URLS["drinking"], "section": "Thirst", "year": 2021,
     "passage": "Test passage two says that drinking to thirst works well for shorter runs in mild weather."},
    {"title": "Caffeine (test)", "url": GSSI_URLS["caffeine"], "section": "Dose", "year": 2022,
     "passage": "Test passage three says that a modest dose taken about an hour before helps many people."},
]


@pytest.fixture(autouse=True)
def fake_gssi(request, monkeypatch):
    """search_gssi gets FAKE_SOURCES (no server, no embeddings) in scripted tests; yields the calls made.
    Live tests and @real_mcp tests use the real server."""
    if request.node.get_closest_marker("live_llm") or request.node.get_closest_marker("real_mcp"):
        yield None
        return
    calls = []

    def scripted_call(server, tool_name, args):
        calls.append((server, tool_name, args))
        return {"results": FAKE_SOURCES[:args.get("k", 4)]}

    monkeypatch.setattr(mcp_tools, "call", scripted_call)
    yield calls


def same_vector(texts: list[str]) -> list[list[float]]:
    """Every text gets the same vector: similarity always passes, so the key-term check alone decides."""
    return [[1.0, 0.0] for _ in texts]


@pytest.fixture(autouse=True)
def fake_embeddings(request, monkeypatch):
    """The citation check (core/citations.py) makes no embeddings call in scripted tests."""
    if not (request.node.get_closest_marker("live_llm") or request.node.get_closest_marker("real_embeddings")):
        monkeypatch.setattr(citations, "embed_texts", same_vector)


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
    last_offered: list = Field(default_factory=list)  # the tools the latest agent call was offered
    tool_choices: list = Field(default_factory=list)  # tool_choice of each bind_tools (None = the model picks)
    orders_seen: dict = Field(default_factory=dict)   # tool name -> instruction-like text found in its result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        for m in messages:          # tool results carry facts only: an instruction there gets read back as the reply
            if isinstance(m, ToolMessage) and reply_rules.instructions(m.content):
                self.orders_seen[m.name] = reply_rules.instructions(m.content)
        if not self.script:
            raise AssertionError("the agent called the model more often than scripted")
        message = self.script.pop(0)
        offered = set(self.tool_names)
        for tool_call in getattr(message, "tool_calls", None) or []:
            # Every scenario proves its route: the skill picked for the message must offer the tool used.
            if tool_call["name"] not in offered:
                raise AssertionError(f"{tool_call['name']} is not offered for this message (skills gave "
                                     f"{sorted(offered)}): fix the label in tests/data/classifier_set.jsonl "
                                     "or the skill's tools")
        if self.tool_names:
            self.last_offered = list(self.tool_names)
        self.tool_names = []                       # a later call (the rewrite) offers no tools unless bound again
        return ChatResult(generations=[ChatGeneration(message=message.model_copy())])

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [t.name for t in tools]
        self.tool_choices.append(kwargs.get("tool_choice"))
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


CLASSIFIER_SET = Path(__file__).resolve().parent / "data" / "classifier_set.jsonl"


class LabeledClassifier:
    """The classifier in scripted tests: each message's categories come from tests/data/classifier_set.jsonl,
    so every scripted scenario runs through its real route. An entry with `context` is used only when that
    text appears in the recent conversation or pending notes (the same "yes" can mean different things).
    An unlabeled message fails the test, or with CLASSIFIER_COLLECT=<file> is written there (route: other)."""

    def __init__(self):
        self.entries = [json.loads(line) for line in CLASSIFIER_SET.read_text().splitlines() if line.strip()]
        self.calls: list[tuple[str, list[str]]] = []
        self.unlabeled: list[str] = []      # checked when the test ends (the agent's classify node catches errors)

    def __call__(self, message, recent, notes, user_id=None, skills=None):
        context = "\n".join([text for _, text in recent] + list(notes))
        same = [e for e in self.entries if e["text"].strip() == message.strip()]
        fitting = [e for e in same if e.get("context") and e["context"] in context] or \
                  [e for e in same if not e.get("context")]
        if not fitting:
            self.unlabeled.append(f"{message!r} (context: {context[-200:]!r})")
            collect = os.getenv("CLASSIFIER_COLLECT")
            if collect:
                with open(collect, "a") as f:
                    f.write(json.dumps({"text": message, "context": context[-300:]}) + "\n")
            categories = []
        else:
            categories = classifier.clean(fitting[0]["categories"], list(skills or {}))
        self.calls.append((message, categories))
        return {"categories": categories, "reason": "labeled in tests/data/classifier_set.jsonl"}


@pytest.fixture
def chat_model(monkeypatch):
    model = ScriptedChat()
    monkeypatch.setattr(agent, "make_model", lambda override=None: model)
    model_classifier = LabeledClassifier()
    monkeypatch.setattr(classifier, "classify", model_classifier)
    yield model
    if model.orders_seen:
        pytest.fail(f"Tool results must carry facts only, these give instructions: {model.orders_seen}")
    if model_classifier.unlabeled and not os.getenv("CLASSIFIER_COLLECT"):
        pytest.fail("Label these messages in tests/data/classifier_set.jsonl: " + "; ".join(model_classifier.unlabeled))


def tool_results(messages) -> list[dict]:
    """Parsed JSON tool results in a list of messages."""
    return [json.loads(m.content) for m in messages if m.type == "tool" and m.content.startswith("{")]
