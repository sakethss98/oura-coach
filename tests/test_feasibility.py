"""modules/goals/feasibility.py and paces.py: which variant, from which week, whether it fits before the event,
and a finish time from their own runs (Riegel), never a fixed pace."""
from datetime import date

import pytest

from programs import library
from modules.goals import paces
from modules.goals.feasibility import assess, choose_variant, predict_finish, valid_entries, weeks_until

HALF = library.load("half_marathon")
HALF_KM = 21.0975
START = date(2026, 11, 16)
RACE = date(2027, 2, 15)       # a Monday (test data)
SUNDAY = 6


def test_the_variant_comes_from_their_level():
    assert choose_variant(HALF, {"long_session_km": 12, "days_per_week": 5}) == "intermediate"
    assert choose_variant(HALF, {"long_session_km": 12, "days_per_week": 3}) == "novice"
    assert choose_variant(HALF, {"long_session_km": 6, "days_per_week": 6}) == "novice"
    assert choose_variant(HALF, {}) == "novice"


def test_entry_weeks_follow_the_validators_growth_rule():
    assert valid_entries(HALF, "novice", {}) == [1]                       # nothing known: from the start
    assert valid_entries(HALF, "novice", {"run": {"weekly": [5, 6, 6], "longest_30d": 3}}) == []
    fit = valid_entries(HALF, "novice", {"run": {"weekly": [26, 28, 30], "longest_30d": 13}})
    assert fit[0] == 1 and fit[-1] > 6 and 6 not in fit and 9 not in fit     # never a lighter week


def test_a_far_event_uses_the_whole_build_and_holds_the_first_week_for_spare_weeks():
    a = assess(HALF, "novice", {"run": {"weekly": [14, 16, 17], "longest_30d": 6}}, START, RACE, SUNDAY)
    assert a["feasible"] and a["entry_week"] == 1 and a["weeks_left"] == 13 and a["extra_weeks"] == 1


def test_a_near_event_starts_later_if_they_are_fit_enough():
    fit = {"run": {"weekly": [26, 28, 30], "longest_30d": 13}}
    a = assess(HALF, "novice", fit, date(2026, 12, 21), RACE, SUNDAY)
    assert a["feasible"] and a["weeks_left"] == 8 and a["entry_week"] == 5 and a["extra_weeks"] == 0


def test_too_little_time_is_not_feasible_and_says_the_earliest_date():
    a = assess(HALF, "novice", {"run": {"weekly": [14, 16, 17], "longest_30d": 6}}, date(2027, 1, 4), RACE, SUNDAY)
    assert not a["feasible"] and a["weeks_left"] == 6
    assert a["entry_week"] == 2 and a["weeks_needed"] == 11              # short of time: the latest week they fit
    assert a["earliest_event"] == date(2027, 3, 22) and a["earliest_event"].weekday() == RACE.weekday()


def test_not_ready_for_week_one_says_what_week_one_needs():
    a = assess(HALF, "novice", {"run": {"weekly": [5, 6, 6], "longest_30d": 3}}, START, RACE, SUNDAY)
    assert not a["feasible"] and a["entry_week"] is None
    assert a["build_to"] == {"run": pytest.approx(4 * 1.609344 / 1.25)}      # week 1's 4 mi long run / 1.25


def test_weeks_until_counts_the_event_week():
    assert weeks_until(START, RACE, SUNDAY) == 13
    assert weeks_until(date(2027, 2, 9), RACE, SUNDAY) == 1


def test_riegel_from_a_known_10k():
    assert paces.riegel(50, 10, HALF_KM) == pytest.approx(50 * (HALF_KM / 10) ** 1.06)
    assert 110 < paces.riegel(50, 10, HALF_KM) < 111                     # 50:00 10k -> about 1:50:20


def test_the_fastest_projection_is_used_and_short_or_implausible_runs_are_not():
    runs = [{"minutes": 30, "distance_km": 4},           # too short to predict from
            {"minutes": 37, "distance_km": 2.2},          # a GPS-less record: 17 min/km, not a pace
            {"minutes": 33, "distance_km": 5},
            {"minutes": 70, "distance_km": 10}]
    best = paces.predict(runs, HALF_KM)
    assert best["from"] == {"minutes": 33, "distance_km": 5}
    assert paces.predict(runs[:2], HALF_KM) is None


def test_predict_finish_compares_with_their_target_and_works_without_one():
    found = predict_finish([{"minutes": 32, "distance_km": 5}], HALF_KM, paces.parse_time("2:30"))
    assert found["within"] is True and 146 < found["predicted_minutes"] < 148          # about 2:27
    runs = [{"minutes": 33, "distance_km": 5}]
    assert predict_finish(runs, HALF_KM, 150)["within"] is False                       # about 2:31.7
    assert predict_finish(runs, HALF_KM, None)["within"] is None
    assert predict_finish([], HALF_KM, 150)["predicted_minutes"] is None


def test_paces_come_from_their_runs_or_their_target_never_a_constant():
    runs = [{"minutes": 39, "distance_km": 6}, {"minutes": 65, "distance_km": 10}, {"minutes": 33, "distance_km": 5}]
    assert paces.easy_pace(runs) == pytest.approx(6.5)
    assert paces.easy_pace([]) is None
    assert paces.race_pace(150, HALF_KM, []) == pytest.approx(150 / HALF_KM)
    assert paces.race_pace(None, HALF_KM, runs) == pytest.approx(paces.predict(runs, HALF_KM)["minutes"] / HALF_KM)
    assert paces.race_pace(None, HALF_KM, []) is None


def test_parse_time():
    assert paces.parse_time("2:30") == 150 and paces.parse_time("1:59:59") == pytest.approx(119 + 59 / 60)
    assert paces.parse_time("2:75") is None and paces.parse_time("soon") is None
