"""The training module: check-ins, workout plans for right now, done workouts, hard-day rules.

Tools: modules/training/tools.py. Score arrives in M3.2 Phase 3.
"""
from datetime import date

from core import db
from core.labels import workout_label
from modules.base import NudgeSpec, ScoreComponent


class TrainingModule:
    name = "training"

    def __init__(self, health=None):
        self.health = health      # connectors.health.Health (device data); never the Oura client directly

    def setup_questions(self) -> list[str]:
        return ["What are you training for, and by when?",
                "How many days a week can you train, and what time of day do you prefer?",
                "Any injuries or movements to avoid?"]

    def tools(self) -> list:
        from modules.training.tools import TOOLS   # imported here: tools pull in the planner and the day view
        return TOOLS

    def context(self, user, day: date) -> str:
        lines = []
        pending = db.pending_plan(user.id)
        if pending and pending["date"] == day.isoformat():
            lines.append("A check-in question (energy and soreness) is waiting for an answer.")
        latest = db.latest_planned(user.id, day)
        if latest:
            plan = latest["plan_json"] or {}
            label = workout_label(plan.get("workout_type", "other"), plan.get("split_day"))
            status = "done" if latest["completed"] == 1 else "not done yet"
            lines.append(f"Latest plan today: {label}, {plan.get('duration_min', 0)} min ({status}).")
        else:
            lines.append("No workout planned today.")
        return "\n".join(lines)

    def nudges(self) -> list[NudgeSpec]:
        return [NudgeSpec("morning_checkin", "first message of the day, no check-in yet",
                          "Want to check in for today's workout?"),
                NudgeSpec("post_workout", "Oura records a workout matching today's plan",
                          "Nice work, I saw your workout. How did it feel?")]

    def score(self, user, day: date) -> ScoreComponent:
        raise NotImplementedError("Training score arrives in M3.2 Phase 3")

    def help_lines(self) -> list[str]:
        return ['"want to work out now": I ask how you feel, then plan a workout for right now',
                '"done", "did a 30 min walk at 6": I mark your plan done or add the workout',
                '"why?": the reasoning behind your plan']
