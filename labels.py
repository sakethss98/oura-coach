"""Human words for internal values: workout labels, emoji, 12-hour times. Pure functions.

Everything the person reads in chat goes through here, so no snake_case reaches them.
"""
import re
from datetime import datetime

WORKOUT_LABELS = {
    "HIIT": "HIIT", "easy_run": "Easy run", "long_run": "Long run", "strength": "Strength",
    "walk": "Walk", "mobility": "Mobility", "rest": "Rest", "yoga": "Yoga", "other": "Workout",
}
OURA_LABELS = {"running": "Run", "walking": "Walk", "strengthTraining": "Strength", "yoga": "Yoga"}
EMOJI = {"HIIT": "🏃", "easy_run": "🏃", "long_run": "🏃", "strength": "🏋️", "walk": "🚶",
         "mobility": "🧘", "rest": "😴"}
PLAIN_METRIC = {"readiness_score": "readiness", "sleep_score": "sleep score", "hrv_ms": "HRV",
                "resting_hr": "resting heart rate"}


def workout_label(workout_type: str, split_day: str | None = None) -> str:
    label = WORKOUT_LABELS.get(workout_type, workout_type.replace("_", " ").capitalize())
    return f"{label} ({split_day})" if workout_type == "strength" and split_day else label


def oura_label(activity: str) -> str:
    """Oura activity names are camelCase ("strengthTraining"); turn unknown ones into words."""
    if activity in OURA_LABELS:
        return OURA_LABELS[activity]
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", activity).replace("_", " ").lower()
    return words.capitalize()


def emoji(workout_type: str) -> str:
    return EMOJI.get(workout_type, "💪")


def _parts(value) -> tuple[int, int]:
    if isinstance(value, datetime):
        return value.hour, value.minute
    hours, minutes = str(value)[:5].split(":")
    return int(hours), int(minutes)


def clock(value, with_suffix: bool = True) -> str:
    """"07:05" or a datetime -> "7:05am"."""
    hour, minute = _parts(value)
    suffix = "am" if hour < 12 else "pm"
    text = f"{hour % 12 or 12}:{minute:02d}"
    return text + suffix if with_suffix else text


def clock_range(start, end) -> str:
    """"18:30", "19:00" -> "6:30–7:00pm"; across noon -> "11:30am–12:15pm"."""
    same_half = (_parts(start)[0] < 12) == (_parts(end)[0] < 12)
    return f"{clock(start, with_suffix=not same_half)}–{clock(end)}"
