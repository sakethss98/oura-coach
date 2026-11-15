"""A secret in a server's error text never reaches a LangSmith trace or a tool result. The pinned USDA server's
errors carry its request url with the api key (tests/mcp_usda_error_server.py fails the same way, over real
stdio). Traces are captured at the last step before upload: the bytes the LangSmith client would send."""
import json

import langsmith
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from core import config, mcp_tools
from modules.nutrition import usda
from tests.conftest import call, needs_fixtures, real_mcp, say
from tests.test_replay import at, coach  # noqa: F401  (fixtures)

pytestmark = real_mcp
KEY = "made-up-usda-key-7f3a9c2e"          # not a real key: the test server puts it in its urls
OTHER = "not-in-env-key-41d8b0"            # a key we don't hold, in a url: hidden by its shape


@pytest.fixture
def server(monkeypatch):
    conf = {"usda": {"command": "python", "args": ["tests/mcp_usda_error_server.py"], "env": ["USDA_API_KEY"],
                     "timeout_s": 15}}
    monkeypatch.setattr(mcp_tools, "server_config", lambda: conf)
    monkeypatch.setattr(mcp_tools, "_servers", mcp_tools._Servers())
    monkeypatch.setenv("USDA_API_KEY", KEY)
    yield conf
    mcp_tools.close()


@pytest.fixture
def traces(monkeypatch):
    """Tracing on, through the app's own redacting client; every payload it would upload is kept, nothing sent."""
    sent = []
    client = config.tracing_client(api_key="test", api_url="http://langsmith.invalid", auto_batch_tracing=False,
                                   info={})
    monkeypatch.setattr(langsmith.Client, "request_with_retries",       # the client has __slots__: patch its class
                        lambda self, method, url, request_kwargs=None, **kw: sent.append(request_kwargs["data"]))
    langsmith.configure(client=client, enabled=True)
    yield sent
    wait_for_all_tracers()
    langsmith.configure(enabled=None, client=None)
    config.redact_traces()


def uploaded(sent) -> str:
    wait_for_all_tracers()
    return "\n".join(b.decode() if isinstance(b, bytes) else str(b) for b in sent)


def test_with_tracing_on_the_app_traces_through_the_redacting_client(monkeypatch):
    from langchain_core.tracers.langchain import get_client
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    try:
        assert config.redact_traces() and get_client()._anonymizer is config.scrub
    finally:
        langsmith.configure(client=None)


def test_with_tracing_off_no_client_is_set_up(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    made = []
    monkeypatch.setattr(config, "tracing_client", lambda **kw: made.append(kw))
    assert config.redact_traces() is False and made == []


def test_redact_hides_our_keys_and_any_key_in_a_url(monkeypatch):
    monkeypatch.setenv("USDA_API_KEY", KEY)
    text = f"for url 'https://x.gov/a?api_key={KEY}&q=1' and https://y.org/b?api_key={OTHER}"
    assert KEY not in config.redact(text) and OTHER not in config.redact(text)
    assert config.scrub({"a": [text, (text,)], text: 3}) == {
        "a": [config.redact(text), (config.redact(text),)], config.redact(text): 3}


def test_a_server_error_never_puts_the_key_in_a_trace_or_a_result(server, traces):
    text = mcp_tools.call("usda", "search_foods", {"query": "plain Greek yogurt", "page_size": 5})
    assert text.startswith("Error searching foods") and KEY not in text and "api_key=[redacted]" in text
    raised = mcp_tools.call("usda", "get_food_details", {"fdc_id": 171413})    # the adapters return it as text
    assert "503 Service Unavailable" in raised and KEY not in raised and "api_key=[redacted]" in raised
    item = {"name": "a yogurt", "usda_query": "plain Greek yogurt", "brand": None, "quantity": 1, "unit": "cup",
            "grams_guess": 150, "guess": {"protein_g": 15, "carbs_g": 6, "fiber_g": 0, "calories": 90,
                                          "caffeine_mg": 0, "alcohol_drinks": 0}}
    found = usda.lookup(item)
    assert found["approximate"] and KEY not in json.dumps(found)
    payload = uploaded(traces)
    assert "Error searching foods" in payload and "503 Service Unavailable" in payload   # the errors were traced
    assert KEY not in payload and "api_key=[redacted]" in payload


@needs_fixtures
def test_a_server_error_never_reaches_the_coachs_tool_results_or_traces(server, traces, coach, user, chat_model,
                                                                        fake_llm):
    from modules.nutrition.schemas import FoodItems
    from tests.test_food_scenarios import CHOBANI, CHOBANI_ITEMS, CHOBANI_Q
    from tests.test_replay import Chat
    fake_llm.food_items_queue = [FoodItems.model_validate(CHOBANI_ITEMS.model_dump())]
    reply = Chat(coach, user, chat_model).send(CHOBANI_Q, 9, 0, [call("food_facts", text=CHOBANI), say("")])
    assert "approximate" in reply and KEY not in reply
    results = [m for seen in chat_model.seen for m in seen if isinstance(m, ToolMessage)]
    assert results and all(KEY not in json.dumps([m.content, m.artifact], default=str) for m in results)
    payload = uploaded(traces)
    assert "food_facts" in payload and "Error searching foods" in payload and KEY not in payload
