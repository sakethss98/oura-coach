"""Daily food targets, what is left of them, one meal's share, and code-picked meals.
Pure functions (meals are read from config/meals.yaml); the LLM never does this math.

Targets come from Oura personal_info (age, weight, height, sex) and the coefficients in
the `nutrition` section of goals.yaml:
  BMR      = Mifflin-St Jeor: 10*kg + 6.25*cm - 5*age, +5 (male) / -161 (female)
  calories = BMR * activity_factor - deficit_kcal (never below BMR)
  protein  = kg * protein_g_per_kg
  fat      = fat_pct % of calories (9 kcal/g)
  carbs    = the calories left after protein (4 kcal/g) and fat
  fiber    = fiber_g_per_1000kcal per 1000 kcal
"""
import re
from datetime import date, datetime

import yaml

from core.config import ROOT

TARGET_KEYS = ["calories", "protein_g", "carbs_g", "fat_g", "fiber_g"]
SEX_OFFSET = {"male": 5, "female": -161}


def daily_targets(body: dict, goals_nutrition: dict) -> dict:
    """{"targets": {...} or None, "inputs": {...}, "reason": str | None}."""
    weight = goals_nutrition.get("weight_kg") or body.get("weight_kg")
    inputs = {
        "age": body.get("age"),
        "weight_kg": weight,
        "weight_source": "goals.yaml" if goals_nutrition.get("weight_kg") else "Oura",
        "height_cm": body.get("height_cm"),
        "sex": body.get("sex"),
    }
    missing = [k for k in ("age", "weight_kg", "height_cm") if inputs[k] is None]
    if inputs["sex"] not in SEX_OFFSET:
        missing.append("sex")
    if missing:
        return {"targets": None, "inputs": inputs,
                "reason": f"Oura personal_info is missing {', '.join(missing)}, so no food targets today."}

    bmr = 10 * weight + 6.25 * inputs["height_cm"] - 5 * inputs["age"] + SEX_OFFSET[inputs["sex"]]
    calories = max(bmr * goals_nutrition["activity_factor"] - goals_nutrition["deficit_kcal"], bmr)
    protein = weight * goals_nutrition["protein_g_per_kg"]
    fat = calories * goals_nutrition["fat_pct"] / 100 / 9
    carbs = max(calories - protein * 4 - fat * 9, 0) / 4
    fiber = calories / 1000 * goals_nutrition["fiber_g_per_1000kcal"]
    inputs["bmr"] = round(bmr)
    return {
        "targets": {"calories": round(calories), "protein_g": round(protein), "carbs_g": round(carbs),
                    "fat_g": round(fat), "fiber_g": round(fiber)},
        "inputs": inputs,
        "reason": None,
    }


def remaining(targets: dict | None, totals: dict) -> dict | None:
    """Targets minus food so far, never below zero. Food logs have no fat estimate, so fat is left out."""
    if targets is None:
        return None
    eaten = {"calories": totals["calories"], "protein_g": totals["protein_g"],
             "carbs_g": totals["carbs_g"], "fiber_g": totals["fiber_g"]}
    return {k: max(round(targets[k] - v), 0) for k, v in eaten.items()}


def recent_food(entries: list[dict], now: datetime) -> list[dict]:
    """Entries eaten before `now`, as minutes ago + estimated calories (input for the food caps)."""
    result = []
    for e in entries:
        eaten_at = datetime.fromisoformat(e["timestamp"])
        if eaten_at <= now:
            result.append({"minutes_ago": round((now - eaten_at).total_seconds() / 60),
                           "calories": e["est_calories"] or 0, "text": e["raw_text"]})
    return result


# Code-picked food lines, used when there is no LLM text that fits the final plan
# (no time left today, or code changed the workout the LLM wrote food for). Meals: config/meals.yaml.
MEALS_PATH = ROOT / "config" / "meals.yaml"
PROTEIN_LEVELS = [(40, "high_protein"), (15, "medium_protein"), (0, "light")]
MEAL_SHARE_MAX = 0.35      # one meal is at most this share of the day's targets
BEFORE = {
    "HIIT": "A banana or 2 dates about 30 minutes before",
    "easy_run": "A banana or a small bowl of poha if you're hungry",
    "long_run": "Poha or 2 slices of toast with peanut butter about an hour before",
    "strength": "A bowl of curd with fruit about an hour before",
}


def dish_key(dish: str) -> str:
    """The words that name a dish ("Paneer bhurji (150 g) with 2 chapatis" -> "paneer bhurji"), so the same
    dish with other sides or portions counts as a repeat."""
    words = [w for w in re.sub(r"\(.*?\)", " ", dish.lower()).replace("-", " ").split() if w.isalpha()]
    words = [w for w in words if w not in {"a", "an", "the", "of", "quick", "simple", "easy", "spicy", "light"}]
    cut = next((i for i, w in enumerate(words) if w in {"with", "and", "plus", "on", "in"}), len(words))
    return " ".join(words[:min(cut, 2)] or words[:2])


def fallback_meal(remaining: dict | None, diet: str = "vegetarian", day: date | None = None,
                  avoid: list[str] = ()) -> str:
    """One meal for the diet, picked by how much protein is still left today; rotates by date and skips
    dishes suggested recently, so the same dish doesn't come back every day."""
    meals = yaml.safe_load(MEALS_PATH.read_text())
    by_level = meals.get(diet) or meals["vegetarian"]
    protein_left = remaining["protein_g"] if remaining else 0
    level = next(name for floor, name in PROTEIN_LEVELS if protein_left >= floor)
    options = by_level[level]
    start = day.toordinal() % len(options) if day else 0
    rotated = options[start:] + options[:start]
    recent = {dish_key(a) for a in avoid}
    return next((m for m in rotated if dish_key(m) not in recent), rotated[0])


def meal_budget(targets: dict | None, left: dict | None, now: datetime) -> dict | None:
    """How big one meal should be: what is left today split over the meals still to come (3 before
    11:00, 2 before 17:00, then 1), at most 35% of the daily target (a normal meal, even when little was
    logged), at least 200 kcal."""
    if targets is None or left is None:
        return None
    meals_left = 3 if now.hour < 11 else 2 if now.hour < 17 else 1
    return {"calories": max(min(left["calories"] / meals_left, MEAL_SHARE_MAX * targets["calories"]), 200),
            "protein_g": max(min(left["protein_g"] / meals_left, MEAL_SHARE_MAX * targets["protein_g"]), 10)} | {
        "meals_left_today": meals_left}


def fallback_before(workout_type: str) -> str:
    """A light pre-workout option; nothing needed before a walk, mobility or rest."""
    return BEFORE.get(workout_type, "")
