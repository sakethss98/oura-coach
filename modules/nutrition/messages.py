"""What the person reads about food: one-line confirmations, targets, what is left, meal ideas."""
import json

from core.labels import clock

NUMBER_TEXT = [("est_calories", "{:.0f} kcal"), ("est_protein_g", "{:.0f} g protein"), ("est_carbs_g", "{:.0f} g carbs"),
               ("est_fiber_g", "{:.0f} g fiber"), ("est_caffeine_mg", "{:.0f} mg caffeine")]


def _num(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.0f}{unit}"


def kcal_protein(numbers: dict) -> str:
    return f"{numbers['calories']:.0f} kcal · {numbers['protein_g']:.0f}g protein"


def format_targets(targets: dict) -> str:
    if targets["targets"] is None:
        return f"Targets: unavailable ({targets['reason']})"
    t, i = targets["targets"], targets["inputs"]
    return (f"Targets today: {t['calories']} kcal, protein {t['protein_g']}g, carbs {t['carbs_g']}g, "
            f"fat {t['fat_g']}g, fiber {t['fiber_g']}g\n"
            f"  (from {i['age']} y, {i['weight_kg']} kg [{i['weight_source']}], {i['height_cm']} cm, {i['sex']}; "
            f"BMR {i['bmr']} kcal)")


def format_remaining(remaining: dict | None) -> str:
    if remaining is None:
        return "Left today: n/a"
    return (f"Left today: {remaining['calories']} kcal, protein {remaining['protein_g']}g, "
            f"carbs {remaining['carbs_g']}g, fiber {remaining['fiber_g']}g")


def format_food_today(totals: dict) -> str:
    if not totals["entries"]:
        return "Food so far: nothing logged"
    return (f"Food so far: {totals['entries']} entries, ~{totals['calories']:.0f} kcal, "
            f"protein {totals['protein_g']:.0f}g, carbs {totals['carbs_g']:.0f}g, fiber {totals['fiber_g']:.0f}g")


def items_of(row: dict) -> list[dict]:
    return json.loads(row.get("nutrition_json") or "[]")


def all_looked_up(items: list[dict]) -> bool:
    """Every item came from USDA FoodData Central with its amount converted by code (no guess anywhere)."""
    return bool(items) and all(i.get("source") and not i.get("approximate") for i in items)


def source_line(items: list[dict]) -> str | None:
    """The code-built credit for numbers from USDA: "Numbers: USDA FoodData Central (A · B)", and which items are
    rough guesses. None when no item came from USDA."""
    found = [i for i in items if i.get("source")]
    if not found:
        return None
    names = " · ".join(dict.fromkeys(i["food"] for i in found))
    notes = []
    if any(i.get("calories_computed") for i in found):
        notes.append("calories from its protein, carbs and fat")
    guessed = [i["name"] for i in items if not i.get("source")]
    estimated = [i["name"] for i in found if i.get("approximate")]
    if estimated:
        notes.append(f"amount estimated for {', '.join(estimated)}")
    if guessed:
        notes.append(f"rough estimate for {', '.join(guessed)}")
    return f"Numbers: USDA FoodData Central ({names})" + (f"; {'; '.join(notes)}" if notes else "")


def food_answer(text: str, items: list[dict], totals: dict) -> str:
    """The answer to "how much protein is in X?": every number from lookup_and_sum (the same as logging it)."""
    exact = all_looked_up(items)
    about = "" if exact else "about "
    parts = [f"{about}{totals['protein_g']:.0f} g protein", f"{totals['calories']:.0f} kcal",
             f"{totals['carbs_g']:.0f} g carbs", f"{totals['fiber_g']:.0f} g fiber"]
    if totals.get("caffeine_mg"):
        parts.append(f"{totals['caffeine_mg']:.0f} mg caffeine")
    weighed = [i for i in items if i.get("grams")]
    weights = (f"{weighed[0]['grams']:.0f} g" if len(items) == 1 and weighed else
               ", ".join(f"{i['name']} {i['grams']:.0f} g" for i in weighed))
    line = f"{text}" + (f" ({weights})" if weights else "") + ": " + ", ".join(parts) + "."
    if not any(i.get("source") for i in items):
        line += " These are approximate (not from USDA FoodData Central)."
    return line


def confirm_entry(row: dict, time_stated: bool = False, verb: str = "Logged") -> str:
    """One short line, e.g. "Logged poha at 8:05am: about 300 kcal, 10 g protein." Numbers they stated are
    shown as theirs: "Updated poppi soda: 30 kcal, 9 g carbs, 3 g fiber (your numbers); about 0 g protein." """
    when = f" at {clock(row['timestamp'][11:16])}" if time_stated else ""
    theirs = set(json.loads(row.get("user_numbers_json") or "[]"))
    stated = [fmt.format(row[c]) for c, fmt in NUMBER_TEXT if c in theirs]
    estimated = [fmt.format(row[c] or 0) for c, fmt in NUMBER_TEXT[:2] if c not in theirs]
    about = "" if all_looked_up(items_of(row)) else "about "
    parts = ([", ".join(stated) + " (your numbers)"] if stated else []) + (
        [about + ", ".join(estimated)] if estimated else [])
    return f"{verb} {row['raw_text']}{when}: {'; '.join(parts)}."


def _portions(portions: list[dict]) -> str:
    """"Dal palak 250 g (1 katori) · jeera rice 150 g (3/4 cup)"."""
    return " · ".join(f"{p['food']} {p['grams']} g ({p['household']})" for p in portions)


def _about(option: dict) -> str:
    if option.get("est_calories") is None:
        return ""
    return f" · about {option['est_calories']:.0f} kcal, {option['est_protein_g']:.0f} g protein"


def format_meal_ideas(options: list[dict], size: dict | None, meal: str | None) -> str:
    """Numbered dishes with portions in grams and a household measure, sized for one meal."""
    head = f"For {meal or 'your next meal'}"
    head += f", about {size['calories']} kcal and {size['protein_g']} g protein:" if size else ":"
    lines = [head]
    for n, o in enumerate(options, 1):
        lines.append(f"{n}. {o['dish']}{_about(o)}")
        if o["portions"]:
            lines.append(f"   {_portions(o['portions'])}")
    return "\n".join(lines)


def format_recipe(title: str, recipe: dict) -> str:
    lines = [f"{title}{_about(recipe)}", _portions(recipe["portions"])]
    lines += [f"{n}. {step}" for n, step in enumerate(recipe["steps"][:6], 1)]
    return "\n".join(lines)
