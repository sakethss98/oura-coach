"""The daily score (core/score.py): pure parts first, then the stored score on fixtures."""
import json
from datetime import date, datetime, time, timedelta

import pytest

from core import db, score
from core.config import TZ
from core.score import compute_score, headline, movement_component, recovery_component, usual_bedtime
from modules.base import ScoreComponent
from tests.conftest import needs_fixtures
from today import fixtures_now

RULES = score.load_config()
RECOVERY, MOVEMENT = RULES["recovery"], RULES["movement"]
WEIGHTS = RULES["templates"]["endurance_body_comp"]["moderate"]["weights"]


def night(hour, minute=0, day=7):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


USUAL_2330 = [night(23, 20, 3), night(23, 40, 4), night(23, 30, 5)]


def recovery(time_in_bed=7.5, bedtime=night(23, 30), previous=USUAL_2330, readiness=80, hard=False, over=False):
    return recovery_component(time_in_bed, bedtime, previous, readiness, 70, hard, 7.5, RECOVERY, over)


# --- compute_score ------------------------------------------------------------------------

def test_weighted_sum_of_components():
    parts = [ScoreComponent("training", 1.0, "run done"), ScoreComponent("nutrition", 0.5, "half"),
             ScoreComponent("recovery", 1.0, "slept"), ScoreComponent("movement", 0.0, "0 steps")]
    result = compute_score(parts, WEIGHTS)
    assert result["score"] == 35 + 15 + 20 + 0
    assert [(p["name"], p["points"], p["of"]) for p in result["parts"]] == [
        ("training", 35, 35), ("nutrition", 15, 30), ("recovery", 20, 20), ("movement", 0, 15)]


def test_a_component_without_data_is_left_out_and_the_rest_re_weighted():
    parts = [ScoreComponent("training", 1.0, "run done"), ScoreComponent("nutrition", 1.0, "on target"),
             ScoreComponent("recovery", None, "no sleep data"), ScoreComponent("movement", None, "no step data")]
    result = compute_score(parts, WEIGHTS)
    assert result["score"] == 100                                                 # never 0 for missing data
    assert [p["of"] for p in result["parts"]] == [54, 46]                        # 35:30, scaled to 100
    assert result["left_out"] == [{"name": "recovery", "note": "no sleep data"},
                                  {"name": "movement", "note": "no step data"}]


def test_nothing_to_score_gives_no_score():
    assert compute_score([ScoreComponent("movement", None, "no step data")], WEIGHTS)["score"] is None


@pytest.mark.parametrize("score_, final, average, expected", [
    (74, False, 68, "74 so far, up from your 68 average"), (68, True, 68, "68 today, right at your 68 average"),
    (60, False, 68, "60 so far, a little under your 68 average"), (74, False, None, "74 so far"),
    (None, False, 68, "Not enough data to score this day yet.")])
def test_headline_compares_to_their_own_average(score_, final, average, expected):
    assert headline(score_, final, average, date(2026, 10, 7), date(2026, 10, 7)) == expected


def test_headline_for_an_earlier_day_names_it():
    assert headline(70, True, None, date(2026, 10, 6), date(2026, 10, 7)) == "70 on Tuesday"


# --- recovery: what they control -------------------------------------------------------------

def test_on_target_and_on_time_is_full():
    r = recovery()
    assert r.fraction == 1.0 and r.note == "7.5 h in bed (target 7.5 h) · bed at 11:30pm, usual 11:30pm"


def test_low_readiness_alone_does_not_lower_it_and_taking_it_easy_counts():
    easy = recovery(readiness=55)
    assert easy.fraction == 1.0 and "took it easy on a low-readiness day" in easy.note
    hard = recovery(readiness=55, hard=True)
    assert hard.fraction == pytest.approx(0.8) and "trained hard on a low-readiness day" in hard.note


def test_a_restless_night_with_enough_time_in_bed_is_not_penalized():
    # sleep quality, efficiency and the sleep score are never inputs: only time in bed and bedtime
    assert recovery(time_in_bed=7.6).fraction == 1.0


def test_short_time_in_bed_and_a_late_bedtime_lower_it_gradually():
    assert recovery(time_in_bed=6.25).details["parts"][0]["fraction"] == 0.5       # 1.25 h short of 2.5
    assert recovery(bedtime=night(0, 30, 8)).details["parts"][1]["fraction"] == 0.5  # 60 min late, across midnight


def test_usual_bedtime_averages_across_midnight():
    assert usual_bedtime([night(23, 30, 3), night(0, 30, 5)]).strftime("%H:%M") == "00:00"


def test_without_sleep_data_recovery_is_left_out():
    assert recovery(time_in_bed=None, bedtime=None).fraction is None


def test_too_few_nights_skips_bedtime_consistency():
    r = recovery(previous=USUAL_2330[:2])
    assert [p["name"] for p in r.details["parts"]] == ["time_in_bed"]


def test_hints_look_ahead_only_until_the_day_is_over():
    assert recovery().next == "bed close to your usual 11:30pm tonight"
    assert recovery(over=True).next is None


# --- movement ----------------------------------------------------------------------------------

def test_movement_is_steps_against_the_target_with_a_walk_hint():
    m = movement_component(5800, 8000, MOVEMENT, day_over=False)
    assert (m.fraction, m.note, m.next) == (0.725, "5,800 of 8,000 steps", "a 20-minute walk (2,200 steps to go)")
    assert movement_component(9000, 8000, MOVEMENT, day_over=False).fraction == 1.0
    assert movement_component(None, 8000, MOVEMENT, day_over=False).fraction is None


# --- stored scores on fixtures ---------------------------------------------------------------------

def at(hour, minute=0):
    return fixtures_now(time(hour, minute))


def started(user):
    """A message on the fixtures' today, so the day counts as one they used the coach."""
    db.add_message(1, 1, at(7), "user", "hi")


@needs_fixtures
def test_get_score_stores_a_provisional_score_and_the_same_data_gives_the_same_score(user):
    first = score.get_score(user, at(12).date(), at(12), True)
    second = score.get_score(user, at(12).date(), at(12, 5), True)
    assert first == second and not first["final"]
    stored = db.get_daily_score(1, at(12).date())
    assert (stored["final"], stored["score"]) == (False, first["score"])


@needs_fixtures
def test_after_the_cutoff_the_score_is_final_and_food_no_longer_changes_it(user):
    started(user)
    assert score.finalize_due(user, at(20, 59), True) == []                      # before the cutoff
    assert score.finalize_due(user, at(21, 0), True) == [at(12).date()]
    frozen = db.get_daily_score(1, at(12).date())
    db.add_food(1, "a big dinner", {"entry_type": "meal", "est_calories": 900, "est_protein_g": 60}, at(21, 10))
    later = score.get_score(user, at(12).date(), at(21, 15), True)
    assert later["final"] and later["score"] == frozen["score"]
    fresh = score.score_of_day(user, at(12).date(), at(21, 15), score.scoring_health(user, at(21, 15), True))
    assert fresh["score"] != frozen["score"]                                     # the dinner would have changed it
    assert score.finalize_due(user, at(21, 20), True) == []                      # already final


@needs_fixtures
def test_days_before_they_started_are_never_scored(user):
    assert score.finalize_due(user, at(22), True) == []                          # no message, food or plan yet


@needs_fixtures
def test_the_comparison_is_the_average_of_their_last_7_final_scores(user):
    day = at(12).date()
    for back, value in ((1, 70), (2, 66), (3, 68), (9, 10)):                     # day 9 is outside the 7 days
        db.save_daily_score(1, day - timedelta(days=back), {"score": value, "parts": []}, final=True)
    db.save_daily_score(1, day - timedelta(days=4), {"score": 99, "parts": []}, final=False)   # not final
    result = score.get_score(user, day, at(12), True)
    assert result["average"] == 68
    assert result["headline"].startswith(f"{result['score']} so far, ")


@needs_fixtures
def test_no_wearable_data_leaves_device_parts_out(user, patch_oura):
    raw = patch_oura()
    day = at(12).date().isoformat()
    for endpoint in ("daily_activity", "daily_readiness", "daily_sleep", "workout"):
        raw[endpoint]["data"] = [r for r in raw[endpoint]["data"] if r["day"] != day]
    raw["sleep"]["data"] = [r for r in raw["sleep"]["data"] if r["day"] != day]
    db.add_food(1, "poha", {"entry_type": "meal", "est_calories": 400, "est_protein_g": 12}, at(8))
    result = score.get_score(user, at(12).date(), at(12), True)
    assert [p["name"] for p in result["parts"]] == ["nutrition"]
    assert {p["name"] for p in result["left_out"]} == {"training", "recovery", "movement"}
    assert result["parts"][0]["of"] == 100
