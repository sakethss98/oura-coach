"""Structured LLM calls (job `structured`, or `judge` for rubric checks). Every call is recorded in the
`runs` table with its job, model, tokens and latency."""
import time

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from core import db, models
from core.config import PROMPTS_DIR


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def structured_call(kind: str, schema: type[BaseModel], system: str, user: str,
                    user_id: int | None = None, job: str = "structured") -> BaseModel:
    """Ask the model for `schema`, record input and output, and return the parsed object."""
    model = models.model_name(job)
    llm = models.chat(job).with_structured_output(schema, include_raw=True)
    trace_input = {"system": system, "user": user}
    started = time.perf_counter()
    try:
        answer = llm.invoke([SystemMessage(system), HumanMessage(user)])
        if answer["parsed"] is None:
            raise answer["parsing_error"] or ValueError(f"No {schema.__name__} in the model's answer")
    except Exception:
        db.log_run(kind, model, trace_input, None, user_id, job=job,
                   latency_ms=round((time.perf_counter() - started) * 1000))
        raise
    input_tokens, output_tokens = models.usage(answer["raw"])
    db.log_run(kind, model, trace_input, answer["parsed"].model_dump(), user_id, job=job, input_tokens=input_tokens,
               output_tokens=output_tokens, latency_ms=round((time.perf_counter() - started) * 1000))
    return answer["parsed"]
