"""The connector contract: where device data comes from.

Modules never call a device API; they ask a HealthSource ("today's workouts", "last night's sleep").
Every method returns plain dicts with local times; missing data is None (or [] for workouts).
"""
from datetime import date
from typing import Protocol


class HealthSource(Protocol):
    def readiness(self, user, day: date) -> dict | None:
        """{"score", "temperature_deviation"}"""

    def sleep(self, user, day: date) -> dict | None:
        """The main sleep that ended on `day`: {"score", "hours", "hrv_ms", "resting_hr"}"""

    def workouts(self, user, day: date) -> list[dict]:
        """[{"source", "activity" or "workout_type", "start", "end", "minutes", ...}]"""

    def activity(self, user, day: date) -> dict | None:
        """{"steps", "active_calories", ...}"""
