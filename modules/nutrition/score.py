"""The nutrition share of the daily score. Pure function; thresholds from config/score.yaml `nutrition`.

Three parts (weights in score.yaml):
- protein: linear from 0 g to the daily target (full at the target or above).
- calories: full within ±10% of the target (moderate rigor; light 15, intense 5), sloping to 0 at ±30%
  (light 40, intense 20). Eating far less is never rewarded: after the day's cutoff, under 0.6 × the
  target scores 0 for this part, whatever the slope says.
- meals: entries logged as meals or snacks, full at 3.
Nothing logged = 0 with the note "not logged" (just the fact). Without targets (Oura body data
missing) only the meals part is scored.
"""
from datetime import datetime

import yaml

from core.config import ROOT
from modules.base import ScoreComponent
from modules.training.baseline import food_totals

SCORE_PATH = ROOT / "config" / "score.yaml"
MEAL_TYPES = {"meal", "snack"}


def load_rules() -> dict:
    return yaml.safe_load(SCORE_PATH.read_text())["nutrition"]


def calorie_fraction(calories: float, target: float, full_pct: float, zero_pct: float) -> float:
    off = abs(calories - target) / target * 100
    if off <= full_pct:
        return 1.0
    if off >= zero_pct:
        return 0.0
    return round(1 - (off - full_pct) / (zero_pct - full_pct), 3)


def score_day(entries: list[dict], targets: dict | None, rules: dict, rigor: str = "moderate",
              after_cutoff: bool = True) -> ScoreComponent:
    """`entries`: the day's food_log rows; `targets`: nutrition.targets daily targets (or None);
    `after_cutoff`: the day is over (the calorie floor applies)."""
    if not entries:
        fraction = 0.0 if rules["no_food_scores_zero"] else None
        return ScoreComponent("nutrition", fraction, "not logged", {"parts": []},
                              None if after_cutoff else "logging what you eat today")

    totals = food_totals(entries)
    meals = sum(1 for e in entries if e["entry_type"] in MEAL_TYPES)
    parts = [{"name": "meals", "fraction": min(meals / rules["meals_for_full"], 1.0),
              "note": f"{meals} meal{'s' if meals != 1 else ''} logged"}]
    if targets:
        protein, calories = totals["protein_g"], totals["calories"]
        calories_part = calorie_fraction(calories, targets["calories"], rules["calories_full_pct"][rigor],
                                         rules["calories_zero_pct"][rigor])
        calorie_note = f"{calories:.0f} of {targets['calories']} kcal"
        if after_cutoff and calories < rules["calorie_floor"] * targets["calories"]:
            calories_part, calorie_note = 0.0, f"{calorie_note}, well under your target"
        elif calories_part == 1.0:
            calorie_note += ", on target"
        parts = [{"name": "protein", "fraction": round(min(protein / targets["protein_g"], 1.0), 3),
                  "note": f"protein {protein:.0f} of {targets['protein_g']} g"},
                 {"name": "calories", "fraction": calories_part, "note": calorie_note}] + parts

    weights = {p["name"]: rules["parts"][p["name"]] for p in parts}
    fraction = sum(p["fraction"] * weights[p["name"]] for p in parts) / sum(weights.values())
    return ScoreComponent("nutrition", round(fraction, 3), " · ".join(p["note"] for p in parts),
                          {"parts": parts, "totals": totals}, None if after_cutoff else _hint(totals, targets))


def _hint(totals: dict, targets: dict | None) -> str | None:
    """What would still help today: the protein (and calories) left, as plain numbers."""
    if not targets:
        return None
    protein_left = targets["protein_g"] - totals["protein_g"]
    kcal_left = targets["calories"] - totals["calories"]
    if protein_left >= 15:
        return f"about {protein_left:.0f} g more protein" + (f" in about {kcal_left:.0f} kcal" if kcal_left > 0 else "")
    return None


def is_after_cutoff(user, day, now: datetime | None) -> bool:
    """A day is over once its cutoff has passed (no `now` = a past day)."""
    return now is None or now >= datetime.combine(day, user.score_cutoff, user.tz)
