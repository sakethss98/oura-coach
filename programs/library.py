"""Where templates come from, and the gate before one makes a plan.

Library templates are hand-written files in programs/library/ (offered to people) and programs/examples/
(never offered: they show the schema fits other goals). M5 adds drafted templates: written by the model,
stored with source "drafted" and status "pending_review". Any template, from a file or a draft, makes a plan
only when it passes validate_template AND its status is "approved" (`require_approved`).
"""
from pathlib import Path

import yaml

from programs.schema import Template
from programs.validate import validate_template

ROOT = Path(__file__).resolve().parent
LIBRARY = ROOT / "library"
EXAMPLES = ROOT / "examples"


class NotApproved(ValueError):
    pass


def parse(data: dict) -> Template:
    return Template.model_validate(data)


def load(name: str, folder: Path = LIBRARY) -> Template:
    return parse(yaml.safe_load((folder / f"{name}.yaml").read_text()))


def all_templates(folder: Path = LIBRARY) -> list[Template]:
    return [load(p.stem, folder) for p in sorted(folder.glob("*.yaml"))]


def offered() -> list[Template]:
    """The templates a goal can be matched to: library files that are approved and say which goals they fit."""
    return [t for t in all_templates() if t.status == "approved" and t.matches]


def require_approved(t: Template) -> None:
    """Raise NotApproved unless the template may make a plan: valid and approved."""
    errors = validate_template(t)
    if errors:
        raise NotApproved(f"{t.name} doesn't pass the safety checks: {errors[0]}")
    if t.status != "approved":
        raise NotApproved(f"{t.name} is {t.status} ({t.source}): only an approved template makes a plan")
