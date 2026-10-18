"""The training module: check-ins, workout plans for right now, done workouts, hard-day rules.

Tools: modules/training/tools.py. Score: modules/training/score.py.
"""
from datetime import date, datetime, timedelta

from connectors.health import Health
from core import db, memory
from core.labels import clock, workout_label
from modules.base import NudgeSpec, ScoreComponent
from modules.training.blocks import blocks_of
from modules.training.score import counts, recovery_reason, rest_week, score_day


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
        goals = user.goals()
        rules = goals["activity"]
        bundle = (self.health or Health()).bundle(user, day, now=now)
        today.sync_oura_completions(user, day, bundle["workouts_by_day"], rules)
        week_items = today.day_activity(user, day, bundle, rules)
        items = week_items[day.isoformat()]
        plans = db.plans_between(user.id, day, day)
        if not plans and not items and bundle["today"]["steps"] is None:
            return ScoreComponent("training", None, "no activity data")    # no wearable data: left out, not 0
        day_over = now is None or now >= datetime.combine(day, user.score_cutoff, user.tz)
        return score_day(plans, items, rules, day_over, self._rest_week(user, day, bundle, week_items, goals))

    @staticmethod
    def _rest_week(user, day: date, bundle: dict, week_items: dict, goals: dict) -> dict:
        """Each of the 7 days ending `day`: known (any device data or activity), worked out, recovery rest."""
        metrics = {m["day"]: m for m in bundle["history"] + [bundle["today"]]}
        days = []
        for i in range(6, -1, -1):
            d = day - timedelta(days=i)
            m, items = metrics.get(d.isoformat(), {}), week_items.get(d.isoformat(), [])
            context = [f["text"] for f in memory.facts(user, d) if f["kind"] == "context"]
            days.append({"known": bool(items) or m.get("steps") is not None or m.get("readiness_score") is not None,
                         "worked_out": any(counts(item, goals["activity"]) for item in items),
                         "recovery": recovery_reason(m.get("readiness_score"), goals["schedule"]["readiness_floor"],
                                                     context)})
        return rest_week(days, goals["schedule"]["rest_days_per_week"])

    def help_lines(self) -> list[str]:
        return ['"want to work out now": I ask how you feel, then plan a workout for right now',
                '"done", "did a 30 min walk at 6": I mark your plan done or add the workout',
                '"why?": the reasoning behind your plan']
