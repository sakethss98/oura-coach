"""Direction goals: a long-term wish ("run and lift together") becomes rules from config/direction_rules.yaml.
The model proposes keys and values from that list only; code clamps them, they confirm once, and only confirmed
rules are read (by the generator and the food targets). Milestones are computed here, never by the model."""
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from programs.validate import CONFIG
from modules.goals import signals


@lru_cache(maxsize=1)
def catalog() -> dict:
    return yaml.safe_load((CONFIG / "direction_rules.yaml").read_text())["rules"]


RuleKey = Literal["strength_sessions_per_week", "min_strength_in_taper", "max_run_days_per_week", "protein_g_per_kg"]


class ProposedRule(BaseModel):
    key: RuleKey
    value: float
    why: str = Field(description="one short plain sentence about their words")


class Proposal(BaseModel):
    rules: list[ProposedRule] = Field(default_factory=list, max_length=4)


def clamp(key: str, value: float) -> float:
    rule = catalog()[key]
    low, high = rule["range"]
    step = rule["step"]
    value = min(max(value, low), high)
    return round(round(value / step) * step, 1) if step < 1 else int(round(value))


def propose(text: str, user_id: int | None = None) -> list[dict]:
    """[{key, value, why}] from their words: one structured call, values clamped by code."""
    from core.llm import load_prompt, structured_call
    listed = "\n".join(f"- {k}: {r['label']} (range {r['range'][0]}-{r['range'][1]})" for k, r in catalog().items())
    prompt = load_prompt("direction_rules").replace("{catalog}", listed)
    found = structured_call("direction", Proposal, prompt, text, user_id)
    seen, out = set(), []
    for r in found.rules:
        if r.key not in seen:
            seen.add(r.key)
            out.append({"key": r.key, "value": clamp(r.key, r.value), "why": r.why})
    return out


def strength_by_phase(rules: dict) -> dict[str, int]:
    """The generator's strength sessions per phase from confirmed rules ({} = the template's own)."""
    out = {}
    if "strength_sessions_per_week" in rules:
        out.update({p: int(rules["strength_sessions_per_week"]) for p in ("base", "build", "peak")})
    if "min_strength_in_taper" in rules:
        out["taper"] = int(rules["min_strength_in_taper"])
    return out


def days_by_discipline(rules: dict) -> dict[str, int]:
    """The generator's most days a week per discipline from confirmed rules (run days)."""
    return {"run": int(rules["max_run_days_per_week"])} if "max_run_days_per_week" in rules else {}


def milestones(rules: dict, signals_by_day: dict, today: date, protein_days: tuple[int, int] | None) -> list[dict]:
    """Each confirmed rule against their last 4 weeks: [{rule, target, actual, met}]. Lifts aren't measured."""
    start = today - timedelta(days=28)
    out = []
    if "strength_sessions_per_week" in rules:
        done = len(signals.workouts(signals_by_day, ["strength"], start, today - timedelta(days=1))) / 4
        out.append({"rule": catalog()["strength_sessions_per_week"]["label"], "target": rules["strength_sessions_per_week"],
                    "actual": round(done, 1), "met": done >= rules["strength_sessions_per_week"]})
    if "max_run_days_per_week" in rules:
        days = len({w["day"] for w in signals.workouts(signals_by_day, ["run"], start, today - timedelta(days=1))}) / 4
        out.append({"rule": catalog()["max_run_days_per_week"]["label"], "target": rules["max_run_days_per_week"],
                    "actual": round(days, 1), "met": days <= rules["max_run_days_per_week"]})
    if "protein_g_per_kg" in rules and protein_days:
        hit, logged = protein_days
        out.append({"rule": "days at the protein target", "target": rules["protein_g_per_kg"],
                    "actual": f"{hit} of {logged} logged days", "met": logged > 0 and hit / logged >= 0.7})
    return out
