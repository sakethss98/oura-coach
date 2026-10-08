"""Daily food targets and what is left of them. Pure functions; the LLM never does this math.

Targets come from Oura personal_info (age, weight, height, sex) and the coefficients in
the `nutrition` section of goals.yaml:
  BMR      = Mifflin-St Jeor: 10*kg + 6.25*cm - 5*age, +5 (male) / -161 (female)
  calories = BMR * activity_factor - deficit_kcal (never below BMR)
  protein  = kg * protein_g_per_kg
  fat      = fat_pct % of calories (9 kcal/g)
  carbs    = the calories left after protein (4 kcal/g) and fat
  fiber    = fiber_g_per_1000kcal per 1000 kcal
"""
from datetime import datetime

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
