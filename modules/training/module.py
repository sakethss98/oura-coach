"""The training module: check-ins, workout plans for right now, done workouts, hard-day rules.

Tools: modules/training/tools.py. Score: modules/training/score.py.
"""
from datetime import date, datetime

from connectors.health import Health
from core import db
from core.labels import clock, workout_label
from modules.base import NudgeSpec, ScoreComponent
from modules.training.blocks import blocks_of
from modules.training.score import score_day


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
        consult = db.pending_confirm(user.id)
        if consult:
            r = consult["requested_json"]
            lines.append(f"Waiting for yes or no (confirm_override), asked at {clock(r['asked_at'][11:16])}: "
                         f"{r['question']!r}")
        expired = None if consult else db.expired_consult(user.id, day)
        if expired:
            r = expired["requested_json"]
            lines.append(f"Their workout question from {clock(r['asked_at'][11:16])} ({r['question'].splitlines()[-1]!r}) "
                         "expired unanswered after 2 hours; nothing was planned. A bare yes/no now: confirm_override "
                         "(it tells them).")
        latest = db.latest_planned(user.id, day)
        if latest:
            plan = latest["plan_json"] or {}
            label = " + ".join(workout_label(b["workout_type"], plan.get("split_day")) for b in blocks_of(plan))
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

    def score(self, user, day: date, now: datetime | None = None) -> ScoreComponent:
        """Did the day's plan happen (modules/training/score.py)? Marks what Oura recorded first."""
        import today   # the shared day view (root module); imported here so loading the module stays light
        rules = user.goals()["activity"]
        bundle = (self.health or Health()).bundle(user, day)
        today.sync_oura_completions(user, day, bundle["workouts_by_day"], rules)
        items = today.day_activity(user, day, bundle, rules)[day.isoformat()]
        return score_day(db.plans_between(user.id, day, day), items, rules)

    def help_lines(self) -> list[str]:
        return ['"want to work out now": I ask how you feel, then plan a workout for right now',
                '"done", "did a 30 min walk at 6": I mark your plan done or add the workout',
                '"why?": the reasoning behind your plan']
