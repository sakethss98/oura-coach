"""The connector layer as modules see it: Oura (read-only) + manual exercise behind one object.

Modules call this, never the Oura client. Merging overlapping workouts into one activity is
training logic and lives in modules/training/activity.py.
"""
from datetime import date, datetime, timedelta

from connectors.manual import ManualSource
from connectors.oura import HISTORY_DAYS, OuraSource, body_metrics, daily_metrics


class Health:
    def __init__(self, use_fixtures: bool = False):
        self.oura = OuraSource(use_fixtures)
        self.manual = ManualSource()

    # --- HealthSource, combined ---------------------------------------------

    def readiness(self, user, day: date) -> dict:
        return self.oura.readiness(user, day)

    def sleep(self, user, day: date) -> dict:
        return self.oura.sleep(user, day)

    def activity(self, user, day: date) -> dict:
        return self.oura.activity(user, day)

    def workouts(self, user, day: date) -> list[dict]:
        """Oura and manual workouts of the day, unmerged."""
        return self.oura.workouts(user, day) + self.manual.workouts(user, day)

    def refresh(self, user, day: date, now: datetime | None = None) -> bool:
        """Re-fetch Oura now; True if anything changed."""
        return self.oura.refresh(user, day, now)

    # --- what the training planner needs in one call -------------------------

    def bundle(self, user, day: date, refresh: bool = False, now: datetime | None = None,
               allow_stale: bool = False) -> dict:
        """Today's Oura metrics, the 7 days before (baseline), body data, and Oura + manual
        workouts for today and the 6 days before (hard-day rules), keyed by YYYY-MM-DD."""
        raw = self.oura.raw(user, day, refresh=refresh, now=now, allow_stale=allow_stale)
        metrics = {d: daily_metrics(raw, d, user.tz) for d in (day - timedelta(days=i) for i in range(HISTORY_DAYS + 1))}
        days = [day - timedelta(days=i) for i in range(7)]
        return {
            "today": metrics[day],
            "history": [metrics[day - timedelta(days=i)] for i in range(HISTORY_DAYS, 0, -1)],
            "body": body_metrics(raw),
            "workouts_by_day": {d.isoformat(): metrics[d]["workouts"] for d in days},
            "manual_by_day": {d.isoformat(): self.manual.workouts(user, d) for d in days},
            "workout_records_fetched": len(raw["workout"]["data"]),
        }
