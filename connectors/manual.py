"""ManualSource: exercise the person adds in chat because Oura missed it ("did a 30 min walk at 6").

Exercise only: there is no manual sleep, readiness or activity. Implements `workouts`;
the other HealthSource methods return None (nothing manual to report).
"""
from datetime import date, datetime

from core import db
from core.day import day_bounds


class ManualSource:
    def add_workout(self, user, workout_type: str, start: datetime, end: datetime, minutes: int | None,
                    feeling: str | None = None, plan_id: int | None = None) -> dict:
        return db.add_manual_workout(user.id, workout_type, start, end, minutes, feeling, plan_id)

    def workouts(self, user, day: date) -> list[dict]:
        start, end = day_bounds(user, day)
        return [
            {"source": "manual", "id": w["id"], "workout_type": w["workout_type"],
             "start": w["start"], "end": w["end"], "minutes": w["minutes"], "feeling": w["feeling"]}
            for w in db.manual_workouts_between(user.id, start, end)
        ]

    def readiness(self, user, day: date) -> None:
        return None

    def sleep(self, user, day: date) -> None:
        return None

    def activity(self, user, day: date) -> None:
        return None
