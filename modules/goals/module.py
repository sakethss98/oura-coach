"""The goals module: a goal of any kind becomes a week-by-week plan from a template (programs/), kept going when
life happens, plus structured limits (injuries) and a long-term direction. Tools: modules/goals/tools.py.

It adds nothing to the daily score (the training module scores the day's workouts, program or not).
"""
from datetime import date, datetime

from core import db
from modules.base import NudgeSpec
from modules.goals import messages, profile as profiles, service


class GoalsModule:
    name = "goals"

    def __init__(self, health=None):
        self.health = health

    def setup_questions(self) -> list[str]:
        return [profiles.QUESTIONS[k] for k in profiles.CORE]

    def tools(self) -> list:
        from modules.goals.tools import TOOLS
        return TOOLS

    def context(self, user, day: date) -> str:
        lines = []
        goal = db.current_goal(user.id)
        if goal and goal["status"] == "setting_up":
            lines.append(f"Setting up their goal ({goal['text']!r}): their next message may answer the questions "
                         "(answer_goal).")
        elif goal and goal["status"] == "proposed":
            lines.append(f"Waiting for their yes or no to the plan for {goal['text']!r} (confirm_goal).")
        elif goal and goal["status"] == "not_feasible":
            lines.append(f"Their goal {goal['text']!r} didn't fit as asked; a new date or answer goes to answer_goal.")
        elif goal and goal["status"] == "active":
            found = service.active(user)
            today = service.today_of(found[2], day) if found else None
            sessions = messages.sessions_line(found[1], today["sessions"]) if today else "nothing"
            lines.append(f"Goal plan: {goal['text']!r}. Today's program: {sessions}.")
            if goal.get("options_json"):
                lines.append("After a break their plan no longer fits before the date: goal_status shows the choices "
                             "(choose_plan_option).")
        limits = db.active_limits(user.id, day)
        if limits:
            lines.append("Saved limits: " + "; ".join(f"{x['body_area']} (avoid {', '.join(x['avoid']) or 'nothing'}"
                                                      f"{', effort at most ' + str(x['intensity_cap']) if x['intensity_cap'] else ''})"
                                                      for x in limits) + ".")
        direction = db.current_direction(user.id)
        if direction and not direction["confirmed"]:
            lines.append("Waiting for their answer to the proposed direction rules (confirm_direction_rules).")
        return "\n".join(lines) if lines else "No goal plan set up."

    def nudges(self) -> list[NudgeSpec]:
        return []

    def score(self, user, day: date, now: datetime | None = None):
        return None                   # not part of the daily score

    def daily_summaries(self, user, days: list[date], metrics_by_day: dict) -> dict[str, dict]:
        """Per day: the program's sessions and whether each was done."""
        found = service.active(user)
        by_date = {d["date"]: d for d in found[2]} if found else {}
        out = {}
        for d in days:
            day = by_date.get(d)
            out[d.isoformat()] = {} if not day else {
                "program": messages.sessions_line(found[1], day["sessions"]),
                "program_status": [x.get("status", "planned") for x in day["sessions"]]}
        return out

    def help_lines(self) -> list[str]:
        return ['"I want to run a 10k in the spring": I ask a few questions, then build a week-by-week plan',
                '"what\'s today\'s session?", "am I on track?": your plan and how it\'s going',
                '"my knee hurts, no runs for a while": I plan around it until you say it\'s better']
