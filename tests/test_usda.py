"""modules/nutrition/usda.py: matching, per-100 g values, amounts to grams, sums, and the server down. Built from
the real responses saved in fixtures/usda/ (gitignored)."""
import json

import pytest

from core import mcp_tools
from core.config import FIXTURES_DIR
from modules.nutrition import usda
from tests.conftest import needs_usda_fixtures

pytestmark = needs_usda_fixtures
GUESS = {"protein_g": 5, "carbs_g": 5, "fiber_g": 1, "calories": 100, "caffeine_mg": 0, "alcohol_drinks": 0}


def saved(name: str) -> dict:
    return json.loads((FIXTURES_DIR / "usda" / name).read_text())


def item(name, query, quantity=None, unit=None, brand=None, grams_guess=150):
    return {"name": name, "usda_query": query, "brand": brand, "quantity": quantity, "unit": unit,
            "grams_guess": grams_guess, "guess": GUESS}


def test_the_closest_food_wins_and_a_food_changing_word_loses():
    foods = saved("search.json")["chicken breast cooked"]["foods"]
    assert foods[0]["description"].startswith("Chicken breast tenders, breaded")             # USDA's own first hit
    best = usda.pick(foods, "chicken breast, meat only, roasted", None)
    assert "breaded" not in best["description"].lower() and "breast" in best["description"].lower()
    assert usda.pick(foods, "lentil soup", None) is None                                     # nothing close: no match


def test_a_brand_matches_its_own_foods_only():
    foods = saved("search.json")["Chobani plain Greek yogurt"]["foods"]
    assert usda.pick(foods, "plain Greek yogurt", "Chobani")["description"] == "Yogurt, Greek, nonfat, plain, CHOBANI"


def test_values_per_100_g_from_search_and_details_and_calories_from_macros_for_branded():
    branded = saved("search.json")["chobani plain greek yogurt"]["foods"][0]          # Branded: no energy value
    values = usda.per_100g(branded)
    assert values["protein_g"] == 9.44 and values["calories_computed"]
    assert values["calories"] == pytest.approx(4 * 9.44 + 4 * 3.89 + 9 * 1.94)
    coffee = usda.per_100g(saved("details.json")["2710375"])                           # details: nested shape
    assert coffee["caffeine_mg"] > 0 and "calories_computed" not in coffee


@pytest.mark.parametrize("fdc_id, unit, quantity, expected", [
    ("171413", "tbsp", 1, 13.5),        # SR Legacy: amount + modifier "tablespoon"
    ("2708408", "cup", 1, None),        # FNDDS: "1 cup" style descriptions
    ("2707154", "piece", 2, 100),       # "1 egg" for 2 eggs
    ("2710616", "can", 1, 360),         # "1 can or bottle (12 fl oz)"
])
def test_household_amounts_become_grams_from_the_foods_portions(fdc_id, unit, quantity, expected):
    details = saved("details.json")[fdc_id]
    food = {"dataType": details["dataType"]}
    grams, how = usda.grams(item("x", details["description"], quantity, unit), food, details)
    if expected is not None:
        assert grams == pytest.approx(expected)
    assert grams > 0 and str(quantity) in how


def test_weights_convert_directly_and_branded_servings_use_their_size():
    assert usda.grams(item("x", "x", 200, "g"), {"dataType": "SR Legacy"}, None) == (200, "200 g")
    assert usda.grams(item("x", "x", 6, "oz"), {"dataType": "SR Legacy"}, None)[0] == pytest.approx(170.1, 0.01)
    branded = saved("search.json")["chobani plain greek yogurt"]["foods"][0]
    assert usda.grams(item("x", "x", 1, "container"), branded, None)[0] == 180
    assert usda.grams(item("x", "x", None, None), branded, None) is None                  # no amount: code can't say


def test_a_mixed_dish_is_summed_in_code(request):
    request.getfixturevalue("usda")                  # the saved responses answer (the fixture shares a name)
    found = [usda.lookup(item("chicken", "chicken breast, meat only, roasted", 200, "g")),
             usda.lookup(item("rice", "rice, white, cooked, no added fat", 1, "cup")),
             usda.lookup(item("olive oil", "olive oil", 1, "tbsp"))]
    assert [i["grams"] for i in found] == [200, 158, 14] and not any(i["approximate"] for i in found)
    total = usda.total(found)
    assert total["protein_g"] == pytest.approx(sum(i["numbers"]["protein_g"] for i in found))
    assert total["calories"] == pytest.approx(sum(i["numbers"]["calories"] for i in found))


def test_the_server_down_uses_the_models_guess_marked_approximate():
    found = usda.lookup(item("a Chobani", "plain Greek yogurt", 1, "container", "Chobani"))
    assert found["approximate"] and found["source"] is None and found["numbers"]["protein_g"] == 5
    assert found["why"] == "USDA FoodData Central unavailable"


def test_a_failed_search_is_tried_with_fewer_results_and_its_error_never_shown(monkeypatch, caplog):
    sizes = []

    def server(name, tool_name, args):
        sizes.append(args["page_size"])
        if args["page_size"] > 10:      # the pinned server's error text, with the key in the url
            return ("Error searching foods: 1 validation error for SearchResponse "
                    "https://api.nal.usda.gov/fdc/v1/foods/search?api_key=SECRETKEY123&query=x")
        return {"foods": []}
    monkeypatch.setenv("USDA_API_KEY", "SECRETKEY123")
    monkeypatch.setattr(mcp_tools, "call", server)
    assert usda.search("x") == {"foods": []} and sizes == [25, 10]
    assert "SECRETKEY123" not in caplog.text
