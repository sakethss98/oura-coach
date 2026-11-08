"""Which model does which job: config/models.yaml maps each job (classifier, agent, structured, judge,
embeddings, template_draft) to a provider and model. The only place chat and embedding models are made.

Precedence for a job's model: an explicit override (a skill file's `model`, agent only) > OPENAI_MODEL in .env
(classifier, agent and structured only, as before M4) > models.yaml.
"""
import os
from functools import lru_cache

import yaml
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from core.config import ROOT

MODELS_PATH = ROOT / "config" / "models.yaml"
ENV_OVERRIDABLE = {"classifier", "agent", "structured"}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(MODELS_PATH.read_text())


def job_config(job: str) -> dict:
    jobs = config()["jobs"]
    if job not in jobs:
        raise KeyError(f"No job {job!r} in config/models.yaml")
    return jobs[job]


def model_name(job: str, override: str | None = None) -> str:
    if override:
        return override
    if job in ENV_OVERRIDABLE and os.getenv("OPENAI_MODEL"):
        return os.environ["OPENAI_MODEL"]
    return job_config(job)["model"]


def chat(job: str, override: str | None = None) -> ChatOpenAI:
    """The chat model for `job`. No temperature is set (gpt-5 models reject non-default values)."""
    c = job_config(job)
    if c["provider"] != "openai":
        raise ValueError(f"Provider {c['provider']!r} is not supported yet (job {job})")
    return ChatOpenAI(model=model_name(job, override), **(c.get("params") or {}))


def embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=model_name("embeddings"))


def usage(message) -> tuple[int | None, int | None]:
    """(input tokens, output tokens) of a model response, or (None, None) when it has no usage."""
    meta = getattr(message, "usage_metadata", None) or {}
    return meta.get("input_tokens"), meta.get("output_tokens")


def cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """USD for one call, from the prices in models.yaml (None for a model without a price or no usage)."""
    price = config().get("prices", {}).get(model)
    if price is None or input_tokens is None:
        return None
    return (input_tokens * price["input"] + (output_tokens or 0) * price["output"]) / 1_000_000
