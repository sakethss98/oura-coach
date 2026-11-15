"""USDA FoodData Central lookups for food items, and the arithmetic, in code.

The model only reads the entry into items and amounts (modules/nutrition/schemas.FoodItems). For each item, code
searches USDA FoodData Central (our pinned MCP server, config/mcp.yaml `usda`), picks the closest food, turns their
amount into grams, and scales the food's per-100 g values. An item with no match, an amount code can't convert, or
the server down falls back to the model's guess for that item, marked approximate. The entry's numbers are the sum.

Parsing is built from responses saved in fixtures/usda/ (2026-11-15):
- search results: foodNutrients flat ({"nutrientNumber", "value"}, per 100 g); Branded foods have servingSize +
  servingSizeUnit ("g"), and no energy (208) value: code computes it from protein, carbs and fat (4/4/9 kcal per g).
- details: foodNutrients nested ({"nutrient": {"number"}, "amount"}, per 100 g); foodPortions as
  portionDescription ("1 cup, cooked", FNDDS), amount + modifier ("tablespoon", SR Legacy) or amount +
  measureUnit.name ("piece", Foundation), each with gramWeight.
- The search's dataType filter answered 400 for some single types, so code searches unfiltered and filters itself.
- Error results are plain text that includes the request url with the api key: mcp_tools.call scrubs it before
  code sees it, and LangSmith traces go through config.tracing_client's anonymizer; never shown, logged redacted.
"""
import logging
import re

from core import mcp_tools
from core.config import redact

log = logging.getLogger(__name__)
SERVER = "usda"
# The pinned server fails a whole search when one result has a nutrient without a value (its pydantic model needs
# one): smaller pages make that less likely, so a failed search is tried again with fewer results.
PAGE_SIZES = (25, 10, 5)
SOURCE = "USDA FoodData Central"
NUTRIENTS = {"203": "protein_g", "205": "carbs_g", "291": "fiber_g", "208": "calories", "262": "caffeine_mg",
             "221": "alcohol_g", "204": "fat_g"}
ALCOHOL_G_PER_DRINK = 14
GENERIC_TYPES = ("Foundation", "SR Legacy", "Survey (FNDDS)")
# Words that change a food a lot: a result with one the query doesn't have is a different food.
CHANGES = {"breaded", "fried", "battered", "sweetened", "candied", "frosted", "glazed", "dried", "powder", "raw",
           "canned", "decaffeinated", "nonalcoholic", "light", "flavored", "glutinous", "ingredient"}
WEIGHT_G = {"g": 1, "kg": 1000, "oz": 28.3495, "lb": 453.592, "ml": 1, "l": 1000}     # ml as g: water-like drinks
PORTION_WORDS = {"cup": ("cup",), "tbsp": ("tablespoon", "tbsp"), "tsp": ("teaspoon", "tsp"),
                 "piece": ("piece", "each", "medium", "large", "small", "whole"), "slice": ("slice",),
                 "can": ("can",), "bottle": ("bottle",), "glass": ("glass", "cup"), "bowl": ("bowl", "cup"),
                 "fl oz": ("fl oz",), "scoop": ("scoop",), "container": ("container",), "serving": ("serving",)}
SERVING_UNITS = {"serving", "container", "can", "bottle", "scoop", "piece"}


def _call(tool: str, args: dict):
    """The server's answer as data, or None (down, slow, or an error: logged without the api key)."""
    found = mcp_tools.call(SERVER, tool, args)
    if isinstance(found, (dict, list)):
        return found
    if found is not None:
        log.warning("USDA %s failed: %s", tool, redact(str(found))[:200])
    return None


def search(query: str) -> dict | None:
    for size in PAGE_SIZES:
        found = _call("search_foods", {"query": query, "page_size": size})
        if found is not None:
            return found
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def score(query: str, description: str) -> float:
    """How well a food's description fits the query: shared words, minus a food-changing word they didn't say,
    a little less for extra words."""
    q, d = _words(query), _words(description)
    if not q:
        return 0
    changed = (d & CHANGES) - q
    return len(q & d) / len(q) - 0.5 * len(changed) - 0.02 * len(d - q)


def pick(foods: list[dict], query: str, brand: str | None) -> dict | None:
    """The best match: a food of their brand when they named one, else a generic food (Foundation, SR Legacy,
    FNDDS). None when nothing shares at least half the query's words."""
    if brand:            # any food of their brand: Branded ones, and SR Legacy ones named for it ("…, CHOBANI")
        b = brand.lower()
        pool = [f for f in foods if b in (f.get("brandOwner") or "").lower() or b in f["description"].lower()]
    else:
        pool = [f for f in foods if f["dataType"] in GENERIC_TYPES]
    ranked = sorted(pool, key=lambda f: score(query, f["description"]), reverse=True)
    return ranked[0] if ranked and score(query, ranked[0]["description"]) >= 0.5 else None


def per_100g(food: dict) -> dict:
    """{field: amount per 100 g} from a search result (flat) or details (nested). Calories from protein, carbs
    and fat when the food has no energy value (Branded)."""
    out = {}
    for n in food.get("foodNutrients") or []:
        number = n.get("nutrientNumber") or (n.get("nutrient") or {}).get("number")
        value = n.get("value") if n.get("value") is not None else n.get("amount")
        if number in NUTRIENTS and value is not None:
            out[NUTRIENTS[number]] = float(value)
    if "calories" not in out and {"protein_g", "carbs_g"} <= set(out):
        out["calories"] = 4 * out["protein_g"] + 4 * out["carbs_g"] + 9 * out.get("fat_g", 0)
        out["calories_computed"] = True
    return out


def _portion_text(p: dict) -> str:
    if p.get("portionDescription"):
        return p["portionDescription"]
    return f"{p.get('amount') or 1:g} {p.get('modifier') or (p.get('measureUnit') or {}).get('name') or ''}".strip()


def _portion_count(text: str) -> float:
    found = re.match(r"\s*(\d+(?:\.\d+)?)", text)
    return float(found.group(1)) if found else 1.0


def grams(item: dict, food: dict, details: dict | None) -> tuple[float, str] | None:
    """(grams, how) for their amount, or None when code can't convert it. Weights and volumes directly; a branded
    serving, container or piece from its serving size; household measures from the food's portions."""
    quantity = item.get("quantity")
    unit = item.get("unit")
    if quantity is None and unit is None:
        return None
    quantity = 1 if quantity is None else quantity
    if unit in WEIGHT_G:
        return quantity * WEIGHT_G[unit], f"{quantity:g} {unit}"
    if food["dataType"] == "Branded" and unit in SERVING_UNITS and food.get("servingSize") \
            and (food.get("servingSizeUnit") or "").lower() in ("g", "grm", "ml", "mlt"):
        return quantity * food["servingSize"], f"{quantity:g} × {food['servingSize']:g} g serving"
    words = PORTION_WORDS.get(unit or "", ())
    if unit == "piece":       # "1 egg" for 2 eggs: a portion named after the food itself
        words = words + tuple(w for w in _words(f"{item['name']} {item['usda_query']}") if len(w) > 2)
    for p in (details or {}).get("foodPortions") or []:
        text = _portion_text(p).lower()
        if words and any(re.search(rf"\b{re.escape(w)}", text) for w in words) and p.get("gramWeight"):
            per_one = p["gramWeight"] / (p.get("amount") or _portion_count(text))
            return quantity * per_one, f"{quantity:g} {unit} ({per_one:g} g each)"
    return None


def lookup(item: dict) -> dict:
    """One item's numbers: {"name", "grams", "numbers" {protein_g, carbs_g, fiber_g, calories, caffeine_mg,
    alcohol_drinks}, "source": SOURCE | None, "food": the USDA description, "fdc_id", "approximate": bool, "why"}."""
    guess = item["guess"]
    fallback = {"name": item["name"], "grams": item.get("grams_guess"), "source": None, "food": None, "fdc_id": None,
                "approximate": True, "numbers": {k: guess[k] for k in ("protein_g", "carbs_g", "fiber_g", "calories",
                                                                        "caffeine_mg", "alcohol_drinks")}}
    query = " ".join(x for x in (item.get("brand"), item["usda_query"]) if x)
    found = search(query)
    if found is None:
        return {**fallback, "why": "USDA FoodData Central unavailable"}
    food = pick(found.get("foods") or [], item["usda_query"], item.get("brand"))
    if food is None:
        return {**fallback, "why": "no match in USDA FoodData Central"}
    details = None
    needs_portions = item.get("unit") not in WEIGHT_G and not (food["dataType"] == "Branded"
                                                               and item.get("unit") in SERVING_UNITS)
    if needs_portions and item.get("unit"):
        details = _call("get_food_details", {"fdc_id": food["fdcId"]})
    amount = grams(item, food, details)
    if amount is None:
        amount = (item.get("grams_guess"), "an estimated amount")
        approximate = True
    else:
        approximate = False
    weight, how = amount
    values = per_100g(details or food)
    scale = (weight or 0) / 100
    numbers = {k: round(values.get(k, 0) * scale, 1) for k in ("protein_g", "carbs_g", "fiber_g", "calories",
                                                               "caffeine_mg")}
    numbers["alcohol_drinks"] = round(values.get("alcohol_g", 0) * scale / ALCOHOL_G_PER_DRINK, 2)
    return {"name": item["name"], "grams": round(weight or 0), "how": how, "numbers": numbers, "source": SOURCE,
            "food": food["description"], "fdc_id": food["fdcId"], "approximate": approximate,
            "calories_computed": bool(values.get("calories_computed")), "why": None}


def total(items: list[dict]) -> dict:
    keys = ("protein_g", "carbs_g", "fiber_g", "calories", "caffeine_mg", "alcohol_drinks")
    return {k: round(sum(i["numbers"][k] for i in items), 2 if k == "alcohol_drinks" else 1) for k in keys}
