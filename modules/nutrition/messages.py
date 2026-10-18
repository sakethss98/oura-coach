"""What the person reads about food: one-line confirmations, targets and what is left."""
from core.labels import clock


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


def confirm_entry(row: dict, time_stated: bool = False) -> str:
    """One short line, e.g. "Logged poha at 8:05am: about 300 kcal, 10 g protein." """
    when = f" at {clock(row['timestamp'][11:16])}" if time_stated else ""
    return (f"Logged {row['raw_text']}{when}: about {row['est_calories']:.0f} kcal, "
            f"{row['est_protein_g']:.0f} g protein.")
