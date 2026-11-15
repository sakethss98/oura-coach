"""The questionnaire, and the Profile the engine reads (programs/profile.py), built from their answers and their
data. The core questions fit any goal; a template's `asks_user` adds the rest. Their data answers a question when
it can (a recent long session, their pace, how often they train), and data beats an earlier answer.

Answers are stored in goal_answers as {key: {"value", "source": "user" | "data"}}.
"""
from datetime import date, timedelta

from programs.profile import Limit, Profile
from programs.schema import Template
from modules.goals import paces, signals

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# The goal-agnostic core, in the order they're asked (two at a time). event_date only for a dated template.
CORE = ["event_date", "days_per_week", "minutes_per_session", "longest_session_min", "level", "limits",
        "equipment"]
QUESTIONS = {
    "event_date": "What's the date of it?",
    "days_per_week": "How many days a week can you train?",
    "minutes_per_session": "How long can a usual session be, in minutes?",
    "longest_session_min": "And on your longest day, how many minutes can you go?",
    "level": "How much do you train now: new to it, some, or regularly?",
    "limits": "Any injuries or limits I should plan around?",
    "equipment": "Any equipment: none, some at home, or a gym?",
    "long_session_day": "Which day suits your long session?",
    "target_time": "Do you have a target time? (No is fine.)",
    "easy_pace": "What's your usual easy pace per km (like 6:30)?",
}
ASKED_AT_A_TIME = 2
PACE_WEEKS = 6
NO_TARGET = "none"


def from_data(t: Template, signals_by_day: dict, start: date) -> dict:
    """The answers their data gives: {key: value}. Pace only from plausible runs; level from how often they train."""
    out = {}
    history = signals.history(signals_by_day, start, t)
    if history:
        sessions = sum(len(signals.workouts(signals_by_day, d.kinds, start - timedelta(days=28),
                                            start - timedelta(days=1))) for d in t.disciplines.values())
        out["level"] = "regular" if sessions >= 12 else "some"
    pace_kinds = sorted({k for d in t.disciplines.values() if d.measure == "km" for k in d.kinds})
    runs = signals.runs(signals_by_day, pace_kinds, start - timedelta(weeks=PACE_WEEKS), start - timedelta(days=1))
    pace = paces.easy_pace(runs)
    if pace:
        out["easy_pace"] = round(pace, 2)
    return out


def needed(t: Template) -> list[str]:
    keys = [k for k in CORE if k != "event_date" or (t.event and t.event.dated)]
    return keys + [k for k in t.asks_user if k not in keys]


def open_keys(t: Template, answers: dict) -> list[str]:
    """What is still to ask, in order. easy_pace only when nothing else gives a pace."""
    return [k for k in needed(t) if k not in answers]


def questions(keys: list[str]) -> list[str]:
    return [QUESTIONS[k] for k in keys[:ASKED_AT_A_TIME]]


def parse_pace(text: str) -> float | None:
    """"6:30" (per km) -> 6.5 min per km; None if unreadable or implausible."""
    minutes = paces.parse_time(f"0:{text.strip()}") if text and ":" in text else None
    return minutes if minutes and paces.PLAUSIBLE_PACE[0] <= minutes <= paces.PLAUSIBLE_PACE[1] else None


def limits_of(rows: list[dict]) -> list[Limit]:
    return [Limit(r["body_area"], r["kind"], r["avoid"], r["intensity_cap"],
                  date.fromisoformat(r["start_date"]) if r["start_date"] else None,
                  date.fromisoformat(r["end_date"]) if r["end_date"] else None) for r in rows]


def build(t: Template, answers: dict, signals_by_day: dict, start: date, limit_rows: list[dict]) -> Profile:
    """The Profile from their answers ({key: value}) and data."""
    history = signals.history(signals_by_day, start, t)
    pace_kinds = sorted({k for d in t.disciplines.values() if d.measure == "km" for k in d.kinds})
    runs = signals.runs(signals_by_day, pace_kinds, start - timedelta(weeks=PACE_WEEKS), start - timedelta(days=1))
    target = answers.get("target_time")
    race_km = t.event.amount if t.event and t.event.measure == "km" else None
    race_pace = paces.race_pace(target if isinstance(target, (int, float)) else None, race_km, runs) if race_km else None
    anchor = WEEKDAYS.index(answers["long_session_day"]) if answers.get("long_session_day") in WEEKDAYS else 6
    return Profile(days_per_week=int(answers.get("days_per_week") or 7), anchor_weekday=anchor,
                   minutes_per_session=answers.get("minutes_per_session"),
                   longest_session_min=answers.get("longest_session_min"),
                   easy_pace=answers.get("easy_pace"), race_pace=race_pace or answers.get("easy_pace"),
                   limits=limits_of(limit_rows), history=history)
