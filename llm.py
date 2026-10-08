"""The single place that talks to OpenAI. Every call is recorded in the `runs` table."""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

import db
from config import OPENAI_MODEL, PROMPTS_DIR


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def structured_call(kind: str, schema: type[BaseModel], system: str, user: str) -> BaseModel:
    """Ask the model for `schema`, record input and output, and return the parsed object."""
    llm = ChatOpenAI(model=OPENAI_MODEL).with_structured_output(schema)
    trace_input = {"system": system, "user": user}
    try:
        result = llm.invoke([SystemMessage(system), HumanMessage(user)])
    except Exception:
        db.log_run(kind, OPENAI_MODEL, trace_input, None)
        raise
    db.log_run(kind, OPENAI_MODEL, trace_input, result.model_dump())
    return result
