"""Real foods for the fueling lines around a run, picked in code: their own most-logged foods when one fits, else the
config/fueling.yaml `foods` fallbacks for their diet.

Pure: `pick(entries, rules, diet, eats_eggs)` takes food_log rows (raw_text, entry_type, timestamp, est_carbs_g,
est_calories, est_fiber_g, est_protein_g) and returns {"dinner", "snack", "after"}: each {"food": text, "usual": bool}
(usual = from their log). Names are compared by dish (targets.dish_key), so "had dal rice" and "dal rice for lunch" are
the same food.
"""
import re
from collections import Counter
from datetime import datetime

from modules.nutrition.targets import dish_key

LEAD = re.compile(r"^\s*(i\s+)?(just\s+)?(had|ate|eaten|drank|have had)\s+", re.I)
TAIL = re.compile(r"\s+(for|at|around|this|in the)\s+(breakfast|lunch|dinner|morning|evening|night|\d).*$", re.I)


def clean(raw_text: str) -> str:
    """"had dal and rice for dinner" -> "dal and rice" (as they wrote it otherwise: "Greek yogurt")."""
    return TAIL.sub("", LEAD.sub("", raw_text.strip())).strip(" .!")


def _most_logged(entries: list[dict]) -> str | None:
    """The food logged most often (ties: the most recent), by dish."""
    counts, last_seen, name = Counter(), {}, {}
    for i, e in enumerate(entries):                     # oldest first: the last one seen is the newest
        key = dish_key(clean(e["raw_text"]))
        if key:
            counts[key] += 1
            last_seen[key], name[key] = i, clean(e["raw_text"])
    if not counts:
        return None
    return name[max(counts, key=lambda k: (counts[k], last_seen[k]))]


def pick(entries: list[dict], rules: dict, diet: str, eats_eggs: bool) -> dict:
    p, fallback = rules["pick"], rules[diet]

    def hour(e):
        return datetime.fromisoformat(e["timestamp"]).hour

    def n(e, field):
        return e.get(field) or 0

    protein = [e for e in entries if n(e, "est_protein_g") >= p["after_min_protein_g"]]
    carb_meals = [e for e in entries if e.get("entry_type") == "meal" and n(e, "est_carbs_g") >= p["dinner_min_carbs_g"]]
    found = {           # the dinner: an evening meal of theirs with carbs, else any such meal
        "dinner": _most_logged([e for e in carb_meals if p["dinner_hours"][0] <= hour(e) <= p["dinner_hours"][1]])
        or _most_logged(carb_meals),
        "snack": _most_logged([e for e in entries if e.get("entry_type") in ("snack", "meal")
                               and n(e, "est_carbs_g") >= p["snack_min_carbs_g"]
                               and n(e, "est_calories") <= p["snack_max_kcal"]
                               and n(e, "est_fiber_g") <= p["snack_max_fiber_g"]]),
        # after: a protein snack of theirs first (lighter after a run), else any of their protein foods
        "after": _most_logged([e for e in protein if e.get("entry_type") != "meal"]) or _most_logged(protein),
    }
    default = {"dinner": fallback["dinner"], "snack": fallback["snack"],
               "after": fallback["after_eggs"] if eats_eggs and fallback.get("after_eggs") else fallback["after"]}
    return {part: {"food": found[part], "usual": True} if found[part] else {"food": default[part], "usual": False}
            for part in default}


def phrase(food: dict) -> str:
    """"your usual dal and rice", or the fallback as it is ("a banana or toast")."""
    return f"your usual {food['food']}" if food["usual"] else food["food"]
