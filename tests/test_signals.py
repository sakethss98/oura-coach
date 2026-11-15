"""The goal engine reads only source-neutral signals (connectors/base.NEUTRAL_DAY_KEYS), so any device, or none,
plugs in without changing it."""
import re
from datetime import date, timedelta
from pathlib import Path

from connectors.base import NEUTRAL_DAY_KEYS
from connectors.health import neutral_day
from connectors.oura import OURA_KINDS
from programs import library
from programs.profile import Profile
from modules.goals import paces, signals
from modules.goals.feasibility import assess
from modules.goals.generator import generate

ROOT = Path(__file__).resolve().parents[1]
HALF = library.load("half_marathon")
START = date(2026, 11, 16)
RACE = date(2027, 2, 15)


def oura_metrics(day: date) -> dict:
    """A daily_metrics dict as connectors/oura.py builds it (Oura's own names stay inside the connector)."""
    return {"readiness_score": 78, "sleep_hours": 7.2, "hrv_ms": 48, "resting_hr": 52, "steps": 9000,
            "workouts": [{"activity": "running", "kind": OURA_KINDS["running"], "start": f"{day}T07:00",
                          "end": f"{day}T07:40", "minutes": 40, "distance_km": 6.1}]}


def test_a_device_day_and_a_manual_workout_become_one_neutral_day():
    d = date(2026, 11, 10)
    manual = [{"kind": "strength", "start": f"{d}T18:00", "end": f"{d}T18:40", "minutes": 40}]
    day = neutral_day(d, oura_metrics(d), manual)
    assert tuple(day) == NEUTRAL_DAY_KEYS
    assert day["readiness"] == 78 and [w["kind"] for w in day["workouts"]] == ["run", "strength"]
    assert {w["source"] for w in day["workouts"]} == {"device", "manual"}


def test_no_wearable_means_only_chat_workouts_and_the_engine_still_plans():
    days = {}
    for back in range(1, 22):
        d = START - timedelta(days=back)
        manual = [{"kind": "run", "start": None, "end": None, "minutes": 39, "distance_km": 6}] if back % 3 == 0 else []
        days[d.isoformat()] = neutral_day(d, None, manual)
    assert all(v["readiness"] is None and v["sleep_hours"] is None for v in days.values())
    history = signals.history(days, START, HALF)
    assert history["run"]["longest_30d"] == 6 and len(history["run"]["weekly"]) == 3
    runs = signals.runs(days, ["run"], START - timedelta(days=42), START - timedelta(days=1))
    p = Profile(days_per_week=5, anchor_weekday=6, minutes_per_session=110, longest_session_min=150,
                easy_pace=paces.easy_pace(runs), race_pace=paces.race_pace(None, 21.0975, runs), history=history)
    a = assess(HALF, "novice", history, START, RACE, 6)
    assert a["feasible"]
    assert generate(HALF, "novice", p, START, RACE, a["entry_week"], a["extra_weeks"])["errors"] == []


def test_a_second_device_plugs_in_with_the_same_neutral_shape():
    other = {(START - timedelta(days=k)).isoformat(): {
        "day": (START - timedelta(days=k)).isoformat(), "readiness": None, "sleep_hours": 6.9, "hrv_ms": 61,
        "resting_hr": 49, "steps": 12000,
        "workouts": [{"kind": "run", "minutes": 55, "distance_km": 9.0, "start": None, "end": None,
                      "source": "device"}] if k % 2 else []} for k in range(1, 22)}
    history = signals.history(other, START, HALF)
    assert history["run"]["longest_30d"] == 9 and signals.facts(HALF, history, 5)["long_session_km"] == 9


def test_the_engine_code_never_names_a_device_field():
    """modules/goals/*.py and programs/*.py read neutral fields only (template YAML text isn't scanned)."""
    banned = re.compile(r"\b(oura|running|walking|strengthTraining|readiness_score)\b", re.I)
    files = list((ROOT / "modules" / "goals").glob("*.py")) + list((ROOT / "programs").glob("*.py"))
    assert files
    found = [f"{f.relative_to(ROOT)}:{i}: {line.strip()}" for f in files
             for i, line in enumerate(f.read_text().splitlines(), 1) if banned.search(line)]
    assert found == []
