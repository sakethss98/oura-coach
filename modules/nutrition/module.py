"""The nutrition module: food logging with rough estimates, edits, removals, meal ideas, daily targets.

Tools: modules/nutrition/tools.py (edit_food and suggest_meal arrive in Phase 4). Score: Phase 4.
"""
from datetime import date

from core import db
from core.day import day_bounds
from modules.base import NudgeSpec, ScoreComponent
from modules.training.baseline import food_totals


class NutritionModule:
    name = "nutrition"

    def __init__(self, health=None):
        self.health = health

    def setup_questions(self) -> list[str]:
        return ["Any diet you follow (vegetarian, vegan, no pork...)?",
                "Which cuisines do you usually eat?",
                "Is your goal to lose fat, gain muscle, or maintain?"]

    def tools(self) -> list:
        from modules.nutrition.tools import TOOLS   # imported here: tools pull in the planner and the day view
        return TOOLS

    def context(self, user, day: date) -> str:
        entries = db.food_between(user.id, *day_bounds(user, day))
        if not entries:
            return "No food logged today."
        t = food_totals(entries)
        return f"Food today: {t['entries']} entries, about {t['calories']:.0f} kcal and {t['protein_g']:.0f} g protein."

    def nudges(self) -> list[NudgeSpec]:
        return [NudgeSpec("log_meal", "no food logged for 6 hours during the day",
                          "Had anything to eat? Tell me and I'll log it.")]

    def score(self, user, day: date) -> ScoreComponent:
        raise NotImplementedError("Nutrition score arrives in M3.2 Phase 4")

    def help_lines(self) -> list[str]:
        return ['"had poha and chai at 8": I log it with a rough estimate',
                '"remove the pizza": I remove an entry (I ask which one if it is unclear)',
                '"what\'s left to eat today?": what is left of your daily targets']
