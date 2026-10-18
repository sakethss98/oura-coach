"""The daily score (0-100): a weighted sum of components, computed in code. The LLM only words it.

Components:
- training, nutrition: each enabled module's score() (modules/*/score.py).
- recovery, movement: core, from device data (connectors/health.py).
  Recovery measures what the person controls, never raw readiness or sleep quality: last night's time in
  bed vs a target, bedtime vs their usual, and (only on a low-readiness day) taking it easy. A low
  readiness or a restless night alone never lowers it. Movement = steps / the template's step target.
Weights, targets and thresholds: config/score.yaml (user_settings.goal_template + goal_rigor).

Missing data: a component with no data (no wearable, no steps) is left out and the rest re-weighted,
never scored 0. Food not logged is the person's own log, so nutrition then scores 0 ("not logged").

Final: a day's score is final once its cutoff (user_settings.score_cutoff) has passed. Nothing runs on its
own, so the first message after the cutoff (or any later day) finalizes every day not yet final in the last
7 (`finalize_due`, called by the agent before it acts on the message): stored with final=1, never recomputed.
Before that, get_score stores the provisional score so it can be compared. The comparison is to the
person's own past: the average of their final scores over the 7 days before. No streaks.
One Oura fetch serves every day being scored (a pinned copy of the cache).
"""
import logging
import math
from datetime import date, datetime, timedelta

import yaml

from connectors.health import Health
from core import db
from core.config import ROOT
from core.day import day_of
from core.labels import clock
from modules.base import ScoreComponent
from modules.registry import enabled_modules
import today   # the shared day view (merged activity for the low-readiness check)

SCORE_PATH = ROOT / "config" / "score.yaml"
FINALIZE_DAYS = 7          # how far back an unscored day is still finalized (the cached Oura fetch covers it)
log = logging.getLogger(__name__)


def load_config() -> dict:
    return yaml.safe_load(SCORE_PATH.read_text())


# --- pure parts -----------------------------------------------------------------

def _evening_minutes(t: datetime) -> int:
    """Minutes after 18:00, so bedtimes either side of midnight compare (23:30 -> 330, 00:30 -> 390)."""
    return (t.hour * 60 + t.minute - 18 * 60) % (24 * 60)


def usual_bedtime(previous: list[datetime]) -> datetime | None:
    """The average of earlier bedtimes (as a clock time on the last of them), or None without any."""
    if not previous:
        return None
    mean = round(sum(_evening_minutes(b) for b in previous) / len(previous))
    return previous[-1].replace(hour=(18 + mean // 60) % 24, minute=mean % 60)


def _linear(off: float, full_within: float, zero_at: float) -> float:
    if off <= full_within:
        return 1.0
    return 0.0 if off >= zero_at else round(1 - (off - full_within) / (zero_at - full_within), 3)


def recovery_component(time_in_bed_h: float | None, bedtime: datetime | None, previous_bedtimes: list[datetime],
                       readiness: int | None, readiness_floor: int, hard_workout: bool, bed_target_h: float,
                       rules: dict, day_over: bool) -> ScoreComponent:
    """Last night's time in bed and bedtime, and a low-readiness day taken easy. Parts with no data are
    left out; with none, the component is left out."""
    parts = []
    if time_in_bed_h is not None:
        short = max(bed_target_h - time_in_bed_h, 0)
        parts.append({"name": "time_in_bed", "fraction": _linear(short, 0, rules["time_in_bed_zero_under_h"]),
                      "note": f"{time_in_bed_h:.1f} h in bed (target {bed_target_h:g} h)"})
    usual = usual_bedtime(previous_bedtimes) if len(previous_bedtimes) >= rules["bedtime_min_nights"] else None
    if bedtime is not None and usual is not None:
        off = abs(_evening_minutes(bedtime) - _evening_minutes(usual))
        parts.append({"name": "bedtime", "fraction": _linear(off, rules["bedtime_full_within_min"],
                                                             rules["bedtime_zero_after_min"]),
                      "note": f"bed at {clock(bedtime)}, usual {clock(usual)}"})
    if readiness is not None and readiness < readiness_floor:
        parts.append({"name": "low_readiness_day", "fraction": 0.0 if hard_workout else 1.0,
                      "note": "trained hard on a low-readiness day" if hard_workout
                      else "took it easy on a low-readiness day"})
    if not parts:
        return ScoreComponent("recovery", None, "no sleep data")
    weights = rules["parts"]
    fraction = sum(p["fraction"] * weights[p["name"]] for p in parts) / sum(weights[p["name"]] for p in parts)
    hint = f"bed close to your usual {clock(usual)} tonight" if usual is not None and not day_over else None
    return ScoreComponent("recovery", round(fraction, 3), " · ".join(p["note"] for p in parts), {"parts": parts},
                          hint)


def movement_component(steps: int | None, target: int, rules: dict, day_over: bool) -> ScoreComponent:
    if steps is None:
        return ScoreComponent("movement", None, "no step data")
    left = max(target - steps, 0)
    hint = None
    if left and not day_over:
        minutes = min(60, 5 * math.ceil(left / rules["steps_per_walk_min"] / 5))
        hint = f"a {minutes}-minute walk ({left:,} steps to go)"
    return ScoreComponent("movement", round(min(steps / target, 1.0), 3), f"{steps:,} of {target:,} steps",
                          {"steps": steps, "target": target}, hint)


def compute_score(components: list[ScoreComponent], weights: dict) -> dict:
    """{"score": 0-100 | None, "parts": [{name, points, of, note}], "left_out": [...], "next": [...]}.
    Components without data are left out and the others' weights scaled up to 100."""
    scored = [c for c in components if c.fraction is not None and weights.get(c.name)]
    left_out = [{"name": c.name, "note": c.note} for c in components if c.fraction is None]
    if not scored:
        return {"score": None, "parts": [], "left_out": left_out, "next": []}
    total = sum(weights[c.name] for c in scored)
    parts = [{"name": c.name, "points": round(c.fraction * weights[c.name] * 100 / total),
              "of": round(weights[c.name] * 100 / total), "note": c.note} for c in scored]
    score = round(sum(c.fraction * weights[c.name] * 100 / total for c in scored))
    return {"score": score, "parts": parts, "left_out": left_out, "next": [c.next for c in scored if c.next]}


def headline(score: int | None, final: bool, average: int | None, day: date, today_: date) -> str:
    """"74 so far, up from your 68 average" (the person against their own past, never against others)."""
    if score is None:
        return "Not enough data to score this day yet."
    when = "so far" if not final else "today" if day == today_ else f"on {day:%A}"
    text = f"{score} {when}"
    if average is None:
        return text
    if score > average:
        return f"{text}, up from your {average} average"
    if score == average:
        return f"{text}, right at your {average} average"
    return f"{text}, a little under your {average} average"


# --- collecting the inputs --------------------------------------------------------

def is_over(user, day: date, now: datetime) -> bool:
    return now >= datetime.combine(day, user.score_cutoff, user.tz)


def scoring_health(user, now: datetime, use_fixtures: bool) -> Health:
    """Health over one Oura fetch (today and the 7 days before), shared by every day being scored."""
    Health(use_fixtures).oura.raw(user, day_of(user, now), now=now)
    return Health(use_fixtures, pinned=db.get_oura_cache(user.id))


def score_of_day(user, day: date, now: datetime, health: Health) -> dict:
    """Every component for `day` as of `now`, combined (not stored)."""
    config, goals = load_config(), user.goals()
    tpl = config["templates"][user.goal_template][user.goal_rigor]
    over = is_over(user, day, now)
    bundle = health.bundle(user, day, now=now)
    night = bundle["today"]
    previous = [datetime.fromisoformat(h["bedtime_start"]) for h in bundle["history"] if h["bedtime_start"]]
    items = today.day_activity(user, day, bundle, goals["activity"])[day.isoformat()]
    components = [m.score(user, day, now) for m in enabled_modules(user, health=health)]
    components += [
        recovery_component(night["time_in_bed_hours"],
                           datetime.fromisoformat(night["bedtime_start"]) if night["bedtime_start"] else None,
                           previous, night["readiness_score"], goals["schedule"]["readiness_floor"],
                           any(i["hard"] for i in items), tpl["bed_hours_target"], config["recovery"], over),
        movement_component(night["steps"], tpl["steps_target"], config["movement"], over),
    ]
    return {"day": day.isoformat(), "final": over, **compute_score(components, tpl["weights"])}


def own_average(user, day: date) -> int | None:
    """The average of their final scores over the days before `day` (None until there are enough)."""
    rules = load_config()["compare"]
    past = db.final_scores_between(user.id, day - timedelta(days=rules["days"]), day - timedelta(days=1))
    return round(sum(p["score"] for p in past) / len(past)) if len(past) >= rules["min_days"] else None


def get_score(user, day: date, now: datetime, use_fixtures: bool) -> dict:
    """The day's score with its breakdown and the comparison to their own average. A final score is
    read back as stored; otherwise it is computed now and stored (provisional until the cutoff)."""
    stored = db.get_daily_score(user.id, day)
    if stored and stored["final"]:
        result = stored["result"]
    else:
        result = score_of_day(user, day, now, scoring_health(user, now, use_fixtures))
        db.save_daily_score(user.id, day, result, result["final"])
    average = own_average(user, day)
    return {**result, "average": average,
            "headline": headline(result["score"], result["final"], average, day, day_of(user, now))}


def finalize_due(user, now: datetime, use_fixtures: bool) -> list[date]:
    """Store the final score of every day in the last 7 whose cutoff has passed and that isn't final yet
    (from the first day they used the coach). Returns the days it finalized."""
    first, today_ = db.first_active_day(user.id), day_of(user, now)
    if first is None:
        return []
    days = [today_ - timedelta(days=i) for i in range(FINALIZE_DAYS - 1, -1, -1)]
    due = [d for d in days if d >= first and is_over(user, d, now)
           and not ((s := db.get_daily_score(user.id, d)) and s["final"])]
    if due:
        health = scoring_health(user, now, use_fixtures)
        for d in due:
            db.save_daily_score(user.id, d, score_of_day(user, d, now, health), final=True)
    return due


def finalize_quietly(user, now: datetime, use_fixtures: bool) -> None:
    """finalize_due for the agent: a failure (e.g. Oura down) never blocks their message."""
    try:
        finalize_due(user, now, use_fixtures)
    except Exception as e:
        log.warning("Finalizing daily scores failed: %s", type(e).__name__)
