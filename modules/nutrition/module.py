"""The nutrition module: food logging with rough estimates, edits, removals, meal ideas, daily targets.

Tools: modules/nutrition/tools.py. Score: modules/nutrition/score.py.
"""
from datetime import date, datetime

from connectors.health import Health
from core import db
from core.day import day_bounds
from modules.base import NudgeSpec, ScoreComponent
from modules.nutrition.score import is_after_cutoff, load_rules, score_day
from modules.nutrition.targets import daily_targets
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

    def score(self, user, day: date, now: datetime | None = None) -> ScoreComponent:
        """Protein, calories and meals against the day's targets (modules/nutrition/score.py)."""
        body = (self.health or Health()).bundle(user, day, now=now, allow_stale=True)["body"]
        targets = daily_targets(body, user.goals()["nutrition"])["targets"]
        entries = db.food_between(user.id, *day_bounds(user, day))
        return score_day(entries, targets, load_rules(), user.goal_rigor, is_after_cutoff(user, day, now))

    def help_lines(self) -> list[str]:
        return ['"had poha and chai at 8", "a 30 cal soda": I log it (your numbers win over my estimate)',
                '"correction, it was 9g carbs", "edit that": I fix the entry, never add a second one',
                '"remove the pizza", "what did I eat on Oct 10?": remove or list entries',
                '"dinner ideas, I have dal and spinach": meal ideas with portions, then the recipe',
                '"what\'s left to eat today?": what is left of your daily targets']
