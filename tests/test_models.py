"""config/models.yaml: which model does which job, the OPENAI_MODEL override, cost, and what runs records."""
import json

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from core import db, llm, models


def test_each_job_has_its_model(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert models.model_name("agent") == "gpt-5-mini"
    assert models.model_name("judge") == "gpt-5"
    assert models.model_name("embeddings") == "text-embedding-3-small"
    assert models.job_config("template_draft")["model"]          # declared, unused in M4


def test_openai_model_env_overrides_only_the_jobs_it_used_to(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-nano")
    assert {j: models.model_name(j) for j in ("classifier", "agent", "structured")} == dict.fromkeys(
        ("classifier", "agent", "structured"), "gpt-5-nano")
    assert models.model_name("judge") == "gpt-5" and models.model_name("embeddings") == "text-embedding-3-small"
    assert models.model_name("agent", override="gpt-5") == "gpt-5"   # a skill's model wins


def test_params_reach_the_model(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    classifier = models.chat("classifier")
    assert (classifier.model_name, classifier.reasoning_effort) == ("gpt-5-mini", "minimal")


def test_cost_from_the_price_table():
    assert models.cost("gpt-5-mini", 1_000_000, 100_000) == 0.25 + 0.2
    assert models.cost("gpt-5-mini", None, None) is None and models.cost("some-other-model", 10, 10) is None


class Answer(BaseModel):
    ok: bool


class FakeStructured:
    """What with_structured_output(include_raw=True) gives back, with token usage on the raw message."""
    def __init__(self, model):
        self.model = model

    def with_structured_output(self, schema, include_raw=False):
        return self

    def invoke(self, messages):
        raw = AIMessage(content="", usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128})
        return {"raw": raw, "parsed": Answer(ok=True), "parsing_error": None}


def test_every_run_records_job_model_tokens_and_latency(temp_db, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(models, "chat", lambda job, override=None: FakeStructured(models.model_name(job)))
    assert llm.structured_call("food", Answer, "system", "user", 1).ok
    llm.structured_call("judge", Answer, "system", "user", job="judge")
    food, judge = reversed(db.recent_runs(2))
    assert (food["kind"], food["job"], food["model"], food["input_tokens"], food["output_tokens"]) == (
        "food", "structured", "gpt-5-mini", 120, 8)
    assert (judge["job"], judge["model"]) == ("judge", "gpt-5")
    assert food["latency_ms"] is not None and json.loads(food["output_json"]) == {"ok": True}
