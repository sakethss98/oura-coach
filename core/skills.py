"""Skill files: one per message category (skills/<category>.md). Adding a category = adding a file.

Each file has YAML frontmatter and a short body:
  name, description, examples   what the classifier reads to pick categories (core/classifier.py)
  tools                         the only tools the agent gets for this category (names; [] = none)
  sources                       where its answers come from (documentation, evals)
  model                         optional: overrides the agent's model (config/models.yaml) for this category
  rubric                        yes/no checks for evals: [{check, by: code | judge}]
  decline                       optional: a fixed reply sent by code instead of calling the agent (off_topic)
The body is what the agent reads for that category: what to look for in each source and how to combine them.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.config import ROOT

SKILLS_DIR = ROOT / "skills"
OTHER = "other"          # no skill fits: the general agent with every tool and every body


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    examples: tuple[str, ...]
    tools: tuple[str, ...]
    body: str
    sources: tuple[str, ...] = ()
    model: str | None = None
    rubric: tuple[dict, ...] = ()
    decline: str | None = None
    path: Path | None = field(default=None, compare=False)


def parse(path: Path) -> Skill:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: a skill file starts with YAML frontmatter between --- lines")
    _, front, body = text.split("---\n", 2)
    try:
        meta = yaml.safe_load(front) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path.name}: the frontmatter is not valid YAML ({e})") from e
    missing = {"name", "description", "examples", "tools"} - set(meta)
    if missing:
        raise ValueError(f"{path.name}: missing {sorted(missing)}")
    if meta["name"] != path.stem:
        raise ValueError(f"{path.name}: name {meta['name']!r} must match the file name")
    return Skill(name=meta["name"], description=meta["description"].strip(), examples=tuple(meta["examples"]),
                 tools=tuple(meta["tools"] or ()), body=body.strip(), sources=tuple(meta.get("sources") or ()),
                 model=meta.get("model"), rubric=tuple(meta.get("rubric") or ()), decline=meta.get("decline"),
                 path=path)


def load(directory: Path | None = None) -> dict[str, Skill]:
    """Every skill in the folder, by name (read fresh each time: the files are small)."""
    folder = directory or SKILLS_DIR
    return {s.name: s for s in (parse(p) for p in sorted(folder.glob("*.md")))}


def validate(skills: dict[str, Skill], tool_names: set[str]) -> None:
    """Fail loudly on a tool a skill names that doesn't exist (a typo would silently remove a tool)."""
    for s in skills.values():
        unknown = set(s.tools) - tool_names
        if unknown:
            raise ValueError(f"skills/{s.name}.md names unknown tools: {sorted(unknown)}")
        if s.name == OTHER:
            raise ValueError(f"{OTHER!r} is reserved for messages no skill fits")


def chosen(skills: dict[str, Skill], categories: list[str]) -> list[Skill]:
    """The skills of the categories, in order; empty for "other" (or nothing known)."""
    return [skills[c] for c in categories if c in skills]
