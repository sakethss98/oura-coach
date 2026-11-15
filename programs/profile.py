"""What the engine knows about the person, as plain data (no device names, no database).

modules/goals builds a Profile from their answers (the goal-agnostic questionnaire + the template's asks_user)
and from source-neutral signals (modules/goals/signals.py). The generator and validate_plan read only this.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Limit:
    """An injury or a limit, structured: the generator and policy_check read it, never the words."""
    body_area: str                      # "knee", "lower back"
    kind: str                           # pain | injury | condition
    avoid: list[str]                    # movements from config/movements.yaml
    intensity_cap: int | None = None    # 1-5, no session above it
    start: date | None = None           # None = from now on
    end: date | None = None             # None = until they say it's better

    def active(self, day: date) -> bool:
        return (self.start is None or self.start <= day) and (self.end is None or day <= self.end)


@dataclass
class Profile:
    days_per_week: int                  # days a week they can train
    anchor_weekday: int                 # the long session's day, 0 = Monday ... 6 = Sunday
    minutes_per_session: int | None = None    # a usual session's longest length
    longest_session_min: int | None = None    # the long day's longest length
    easy_pace: float | None = None      # min per km: their recent runs (or their answer)
    race_pace: float | None = None      # min per km: target time / distance, else predicted from their runs
    limits: list[Limit] = field(default_factory=list)
    # Per discipline, what they did before the plan starts: {"weekly": [3 weekly loads, oldest first],
    # "longest_30d": the longest session in the 30 days before}. Filled from signals; {} = nothing known.
    history: dict[str, dict] = field(default_factory=dict)
