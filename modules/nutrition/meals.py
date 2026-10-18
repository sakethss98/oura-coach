"""Meal ideas and recipes. The LLM proposes dishes; code sizes them for one meal, keeps them varied and
saves them.

Variety is enforced in code, not only asked for: a dish suggested in the last 5 days (`meal_suggestions`,
compared by its name words, so "paneer bhurji with rotis" repeats "Paneer bhurji wrap") is dropped. If
every option was a repeat, the LLM is asked once more with the rejected dishes named, then code falls back
to config/meals.yaml (also skipping recent dishes). Dishes they ate today or yesterday count as repeats
too. The ideas draw on what the person actually logs (its ingredients), but not the same dish again.
"""
import json
from collections import Counter
from datetime import date, datetime, timedelta

from connectors.health import Health
from core import db, memory
from core.llm import load_prompt, structured_call
from modules.nutrition.messages import format_meal_ideas, format_recipe
from modules.nutrition.schemas import MealIdeas, Recipe
from modules.nutrition.targets import daily_targets, dish_key, fallback_meal, meal_budget, remaining
from modules.training.baseline import food_totals
import today   # the shared day view: food_on, recent_meal_ideas

MAX_OPTIONS = 2
OFTEN_LOGGED_DAYS = 30
OFTEN_LOGGED_COUNT = 10
ATE_RECENTLY_DAYS = 2      # what they ate today or yesterday isn't suggested again either


def often_logged(user, now: datetime) -> list[str]:
    """What they log most in the last 30 days, most frequent first (their real foods)."""
    entries = db.food_between(user.id, now - timedelta(days=OFTEN_LOGGED_DAYS), now)
    counts = Counter(e["raw_text"].strip().lower() for e in entries)
    return [text for text, _ in counts.most_common(OFTEN_LOGGED_COUNT)]


def _meal_size(user, day: date, now: datetime, use_fixtures: bool) -> dict | None:
    """One meal's share of what is left today (code math), or None without targets."""
    body = Health(use_fixtures).bundle(user, day, now=now, allow_stale=True)["body"]
    targets = daily_targets(body, user.goals()["nutrition"])["targets"]
    left = remaining(targets, food_totals(today.food_on(user, day)))
    size = meal_budget(targets, left, now)
    return {k: round(v) for k, v in size.items()} if size else None


def _fresh(options: list[dict], avoid: list[str]) -> list[dict]:
    """Options that aren't a recent dish (or the same dish twice)."""
    seen = {dish_key(a) for a in avoid}
    kept = []
    for o in options:
        key = dish_key(o["dish"])
        if key and key not in seen:
            kept.append(o)
            seen.add(key)
    return kept


def suggest(user, day: date, now: datetime, use_fixtures: bool, meal: str | None = None,
            ingredients: list[str] | None = None, constraints: str | None = None) -> dict:
    """1-2 meal ideas sized for one meal: {"options": [...], "size": {...}, "text": message}."""
    size = _meal_size(user, day, now, use_fixtures)
    ate = [e["raw_text"] for e in db.food_between(user.id, now - timedelta(days=ATE_RECENTLY_DAYS), now)]
    avoid = today.recent_meal_ideas(user, day) + ate
    context = {
        "meal": meal or "their next meal",
        "now": f"{now:%H:%M}",
        "size_for_this_meal": size,
        "they_have": ingredients or [],
        "constraints": constraints,
        "saved_facts": memory.for_prompt(user, day),
        "foods_they_often_log": often_logged(user, now),
        "do_not_suggest": avoid,
    }
    options = []
    for _ in range(2):                                     # the second try names what was rejected
        ideas = structured_call("meal", MealIdeas, load_prompt("meal"), json.dumps(context, indent=2), user.id)
        proposed = [o.model_dump() for o in ideas.options]
        options = _fresh(proposed, avoid)
        if options:
            break
        context["rejected_as_repeats"] = [o["dish"] for o in proposed]
    if not options:
        dish = fallback_meal(size and {"protein_g": size["protein_g"]}, user.goals()["nutrition"]["diet"], day, avoid)
        options = [{"dish": dish, "portions": [], "est_calories": None, "est_protein_g": None}]
    options = options[:MAX_OPTIONS]
    db.add_meal_suggestions(user.id, day, [o["dish"] for o in options])
    return {"options": options, "size": size, "text": format_meal_ideas(options, size, meal)}


def recipe(user, day: date, now: datetime, use_fixtures: bool, dish: str, change: str | None = None,
           ingredients: list[str] | None = None) -> dict:
    """The recipe for one dish (or a revised version: `change` = "higher protein"), sized for one meal.
    The dish stays the one they picked: the title is code's, the LLM only fills portions and steps."""
    size = _meal_size(user, day, now, use_fixtures)
    context = {"dish": dish, "change": change, "size_for_this_meal": size, "they_have": ingredients or [],
               "saved_facts": memory.for_prompt(user, day)}
    result = structured_call("recipe", Recipe, load_prompt("recipe"), json.dumps(context, indent=2), user.id)
    if dish_key(dish) not in {dish_key(d) for d in today.recent_meal_ideas(user, day)}:
        db.add_meal_suggestions(user.id, day, [dish])
    title = f"{dish} ({change})" if change else dish
    return {"dish": dish, "text": format_recipe(title, result.model_dump())}
