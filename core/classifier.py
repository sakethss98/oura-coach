"""Which categories (skills/<name>.md) a message belongs to: one structured call (job `classifier`).

Input: their message, the last few turns, and what is pending (a check-in answer, a yes/no question, the
coach's last question from an earlier conversation). Output: 1-2 category names (main first) or "other".
The category list, definitions and examples come from the skill files at call time, so a new skill file
is a new category with no code change.
"""
from typing import Literal

from pydantic import BaseModel, Field, create_model

from core import skills as skill_files
from core.llm import load_prompt, structured_call

MAX_CATEGORIES = 2


def route_schema(names: list[str]) -> type[BaseModel]:
    """{categories: [names...], reason} with the names of today's skill files."""
    name = Literal[tuple(names + [skill_files.OTHER])]
    return create_model("Route", categories=(list[name], Field(description="1-2 category names, main first")),
                        reason=(str, Field(description="one short sentence")))


def describe(skills: dict) -> str:
    lines = []
    for s in skills.values():
        lines.append(f"- {s.name}: {s.description}")
        lines += [f"    e.g. {e!r}" for e in s.examples]
    return "\n".join(lines)


def classify(message: str, recent: list[tuple[str, str]], notes: list[str], user_id: int | None = None,
             skills: dict | None = None) -> dict:
    """{"categories": [...], "reason"}. `recent` = [(role, text)] before this message, oldest first;
    `notes` = pending state in plain words."""
    skills = skills if skills is not None else skill_files.load()
    names = list(skills)
    system = f"{load_prompt('classifier')}\n\nCategories:\n{describe(skills)}"
    parts = []
    if recent:
        parts.append("Recent conversation:\n" + "\n".join(f"{role}: {text}" for role, text in recent))
    if notes:
        parts.append("Pending:\n" + "\n".join(f"- {n}" for n in notes))
    parts.append(f"Message to route: {message}")
    route = structured_call("classifier", route_schema(names), system, "\n\n".join(parts), user_id, job="classifier")
    return {"categories": clean(route.categories, names), "reason": route.reason}


def clean(categories: list[str], names: list[str]) -> list[str]:
    """Known names, no repeats, at most two; "off_topic" next to a real category is dropped; [] = other."""
    found = []
    for c in categories:
        if c in names and c not in found:
            found.append(c)
    if len(found) > 1 and "off_topic" in found:
        found.remove("off_topic")
    return found[:MAX_CATEGORIES]
