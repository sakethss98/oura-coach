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
        """[{"source", "kind", "activity" or "workout_type", "start", "end", "minutes", ...}]. `kind` is neutral,
        the same for every device: run | walk | strength | cycle | swim | hike | hiit | mobility | yoga | other.
        The goal engine reads only `kind` (the device's own name stays for the training module)."""

    def activity(self, user, day: date) -> dict | None:
        """{"steps", "active_calories", ...}"""


# One day as every engine sees it, whatever the device (connectors/health.neutral_day builds it):
# {"day", "readiness" (0-100 or None: not every device has one), "sleep_hours", "hrv_ms", "resting_hr", "steps",
#  "workouts": [{"kind", "start", "end", "minutes", "distance_km", "source"}]}. Missing values are None; a person
# with no wearable has only the workouts they add in chat.
NEUTRAL_DAY_KEYS = ("day", "readiness", "sleep_hours", "hrv_ms", "resting_hr", "steps", "workouts")
