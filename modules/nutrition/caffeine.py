"""Caffeine and their day, in code: the dose for them today, and whether to have something now.

Pure. The rules are in config/fueling.yaml (`caffeine`: GSSI's range and timing; `caffeine_sleep`: our cutoff before
their usual bedtime and the daily maximum, labeled ours). Their usual bedtime is the average of their recent bedtimes
(core/score.usual_bedtime); without one, the bedtime rule is left out.

  dose(...)  -> {"mg": 110, "skip": None} for "about 110 mg for you today", or mg 0 with the reason to skip it
  call(...)  -> {"call": "yes" | "wait" | "no", "reason": "…"} for "should I have X now?", or None (no rule applies)
"""
import math
from datetime import datetime, timedelta

from core.labels import clock

DAY_MINUTES = 24 * 60
JUST_AFTER_BED_MIN = 4 * 60     # within 4 h after the usual bedtime it is still that night (past bedtime)


def hours_until(bedtime: datetime | None, now: datetime) -> float | None:
    """Hours from `now` to their usual bedtime's clock time (0 when it has passed tonight), or None."""
    if bedtime is None:
        return None
    left = (bedtime.hour * 60 + bedtime.minute - now.hour * 60 - now.minute) % DAY_MINUTES
    return 0.0 if left > DAY_MINUTES - JUST_AFTER_BED_MIN else round(left / 60, 1)


def _hours(h: float) -> str:
    return "under an hour" if h < 1 else f"about {round(h)} h"


def low_end(range_mg: list[float]) -> int:
    """The low end of their range, rounded up to 10 mg: never below the range (105 -> 110)."""
    return int(math.ceil(range_mg[0] / 10) * 10)


def dose(range_mg: list[float], so_far_mg: float, hours_to_bed: float | None, rules: dict) -> dict:
    """{"mg", "skip"}: the low end of their range (start low), or 0 when the day's maximum would be passed or it
    would be taken within the cutoff before their usual bedtime (skip = "bedtime" | "daily_max")."""
    mg = low_end(range_mg)
    if hours_to_bed is not None and hours_to_bed < rules["stop_hours_before_bed"]:
        return {"mg": 0, "skip": "bedtime"}
    if so_far_mg + mg > rules["daily_max_mg"]:
        return {"mg": 0, "skip": "daily_max"}
    return {"mg": mg, "skip": None}


def call(item_mg: float, item_kcal: float, so_far_mg: float, now: datetime, bedtime: datetime | None,
         next_run: dict | None, rules: dict, minutes_before: int, food_caps: list[dict]) -> dict | None:
    """Should they have it now? next_run: today's next planned workout not done, {"label", "start"} (a datetime).
    Caffeine: no within the cutoff before bed or past the daily maximum; wait when a workout starts later (have it
    about `minutes_before` before it); else yes. Other food: wait when it is a real meal or snack by goals.yaml
    recent_food_caps and a workout starts within that time; else None (no rule of ours applies)."""
    to_run = (next_run["start"] - now).total_seconds() / 60 if next_run else None
    if item_mg > 0:
        to_bed = hours_until(bedtime, now)
        if to_bed is not None and to_bed < rules["stop_hours_before_bed"]:
            return {"call": "no", "reason": f"your usual bedtime ({clock(bedtime)}) is {_hours(to_bed)} away, and "
                                            "caffeine this late can cut into your sleep"}
        if so_far_mg + item_mg > rules["daily_max_mg"]:
            return {"call": "no", "reason": f"you've had about {so_far_mg:.0f} mg of caffeine today, and this would "
                                            f"make {so_far_mg + item_mg:.0f} mg"}
        if to_run is not None and to_run > rules["wait_if_run_in_min"]:
            at = next_run["start"] - timedelta(minutes=minutes_before)
            return {"call": "wait", "reason": f"have it around {clock(at)}, about {minutes_before} min before your "
                                              f"{next_run['label']} at {clock(next_run['start'])}"}
        before_bed = f", well before your usual {clock(bedtime)} bedtime" if bedtime else ""
        had = f"you've had {so_far_mg:.0f} mg of caffeine today" if so_far_mg else "you've had no caffeine yet today"
        return {"call": "yes", "reason": f"it's {clock(now)}{before_bed}, and {had}"}
    if to_run is not None and 0 <= to_run:
        cap = next((c for c in sorted(food_caps, key=lambda c: c["within_min"])
                    if item_kcal >= c["min_calories"] and to_run <= c["within_min"]), None)
        if cap:
            return {"call": "wait", "reason": f"your {next_run['label']} starts at {clock(next_run['start'])}, so have "
                                              "it after"}
    return None
