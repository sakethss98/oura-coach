"""modules/goals/generator.py: template + profile -> every day of the plan, by the template's own rules.
The race here (a half marathon on Mon Feb 15, 2027) is test data only."""
from datetime import date, timedelta

import pytest

from programs import library
from programs.profile import Limit, Profile
from programs.validate import validate_plan
from modules.goals.feasibility import assess
from modules.goals.generator import generate

START = date(2026, 11, 16)                  # a Monday
RACE = date(2027, 2, 15)                    # a Monday
SUNDAY = 6
NOVICE_LONG_MI = [4, 4, 5, 5, 6, 7, 8, 9, 10]          # Higdon Novice 1, without the 5K / 10K weeks


def profile(**changes) -> Profile:
    return Profile(**{"days_per_week": 6, "anchor_weekday": SUNDAY, "minutes_per_session": 110,
                      "longest_session_min": 150, "easy_pace": 6.5, "race_pace": 7.0,
                      "history": {"run": {"weekly": [14, 16, 17], "longest_30d": 6}}, **changes})


def plan(variant="novice", p=None, start=START, event=RACE, template="half_marathon", folder=library.LIBRARY):
    t = library.load(template, folder)
    p = p or profile()
    a = assess(t, variant, p.history, start, event, p.anchor_weekday)
    result = generate(t, variant, p, start, event, a["entry_week"], a["extra_weeks"])
    assert result["errors"] == [], result["errors"]
    return t, p, result


def sessions(result, name=None):
    return [(d, x) for d in result["days"] for x in d["sessions"]
            if name is None or f"{x['discipline']}.{x['type']}".startswith(name)]


def anchors(result):
    return [d["date"] for d in result["days"] if d["anchor"]]


@pytest.mark.parametrize("variant", ["novice", "intermediate"])
def test_both_variants_make_a_valid_plan(variant):
    p = profile(history={"run": {"weekly": [26, 28, 28], "longest_30d": 10}}) if variant == "intermediate" else None
    t, p, result = plan(variant, p)
    assert validate_plan(result["days"], p, t, variant) == []


def test_the_long_run_sequence_is_higdons():
    t, p, result = plan()
    longs = [round(x["amount"] / 1.609344, 2) for _, x in sessions(result, "run.long")]
    assert longs == [4] + NOVICE_LONG_MI                 # one extra week first: the first week, held


def test_two_strength_sessions_in_every_build_week_and_none_on_the_long_day():
    t, p, result = plan()
    for n in {d["week"] for d in result["days"] if d["phase"] == "build"}:
        assert len([1 for d, _ in sessions(result, "strength") if d["week"] == n]) == 2
    assert not [d for d, _ in sessions(result, "strength") if d["anchor"]]


def test_no_legs_two_days_before_or_the_day_after_a_long_run_and_legs_mid_week():
    t, p, result = plan()
    long_days = anchors(result)
    for d, _ in sessions(result, "strength.lower"):
        for a in long_days:
            assert not 0 < (a - d["date"]).days <= 2 and (d["date"] - a).days != 1
    offsets = {(next(a for a in long_days if a >= d["date"]) - d["date"]).days for d, _ in sessions(result, "strength.lower")}
    assert offsets <= {4, 5}


def test_the_day_after_a_long_run_is_light_only_and_the_day_before_is_rest():
    t, p, result = plan()
    by_date = {d["date"]: d["sessions"] for d in result["days"]}
    for a in anchors(result):
        assert all(t.session_type(x["discipline"], x["type"]).recovery for x in by_date.get(a + timedelta(days=1), []))
        assert by_date[a - timedelta(days=1)] == []


def test_every_calendar_week_keeps_a_rest_day_and_hybrid_days_free_a_second():
    t, p, result = plan()
    days = result["days"]
    for i in range(0, len(days) - 6, 7):
        week = days[i:i + 7]
        if week[0]["date"].weekday() == 0:
            assert sum(1 for d in week if not d["sessions"]) >= 2      # the lifts go after runs, not on rest days


def test_a_lift_after_the_weeks_longest_midweek_run_is_upper_body():
    t, p, result = plan("intermediate", profile(history={"run": {"weekly": [26, 28, 28], "longest_30d": 10}}))
    for d, x in sessions(result, "strength"):
        runs = [y["amount"] for e in result["days"] if e["week"] == d["week"] and not e["anchor"]
                for y in e["sessions"] if y["discipline"] == "run"]
        if any(y["discipline"] == "run" and y["amount"] == max(runs) for y in d["sessions"]):
            assert x["type"] == "upper"


def test_the_race_is_on_the_event_day_with_rest_before_it():
    t, p, result = plan()
    last = result["days"][-1]
    assert last["date"] == RACE and [x["type"] for x in last["sessions"]] == ["race"]
    assert result["days"][-2]["sessions"] == [] and result["days"][-2]["date"] == date(2027, 2, 14)


def test_fewer_days_a_week_drops_cross_training_first():
    t, p, result = plan(p=profile(days_per_week=4))
    assert not sessions(result, "cross")
    assert len(sessions(result, "run.long")) == len(NOVICE_LONG_MI) + 1
    assert any("cross.cross (fewer days a week)" in c for c in result["changes"])


def test_a_knee_limit_on_the_hike_example_swaps_deep_knee_work_and_caps_effort():
    knee = Limit("knee", "pain", ["deep_knee_bend", "jumping"], intensity_cap=3)
    p = Profile(days_per_week=5, anchor_weekday=5, minutes_per_session=90, longest_session_min=480,
                limits=[knee], history={"hike": {"weekly": [0, 300, 400], "longest_30d": 300}})
    t, p, result = plan("standard", p, date(2027, 3, 1), date(2027, 5, 1), "day_hike", library.EXAMPLES)
    assert sessions(result, "strength.knee_friendly") and not sessions(result, "strength.lower")
    assert all(x["intensity"] <= 3 for _, x in sessions(result))
    assert any("strength.lower -> strength.knee_friendly (their knee)" in c for c in result["changes"])
    assert result["days"][-1]["date"] == date(2027, 5, 1) and sessions(result, "hike.summit")


def test_without_any_history_the_plan_starts_at_week_one():
    t, p, result = plan(p=profile(history={}))
    assert round(sessions(result, "run.long")[0][1]["amount"] / 1.609344, 2) == 4


def test_race_week_has_no_lower_body_lift_only_upper_body():
    for variant, p in (("novice", None), ("intermediate", profile(history={"run": {"weekly": [26, 28, 28],
                                                                                     "longest_30d": 10}}))):
        t, p, result = plan(variant, p)
        last_week = [(d, x) for d, x in sessions(result, "strength") if RACE - d["date"] <= timedelta(days=7)]
        assert last_week and all(x["type"] == "upper" for _, x in last_week)


def test_rest_before_the_long_run_is_a_novice_rule_intermediate_keeps_higdons_saturday():
    _, _, novice = plan()
    _, _, inter = plan("intermediate", profile(history={"run": {"weekly": [26, 28, 28], "longest_30d": 10}}))
    def day_before_long(result):
        by_date = {d["date"]: d["sessions"] for d in result["days"]}
        return [by_date.get(a - timedelta(days=1)) for a in anchors(result)[:-1]]
    assert all(s == [] for s in day_before_long(novice))
    saturdays = day_before_long(inter)
    assert sum(1 for s in saturdays if s and s[0]["type"] in ("pace", "easy")) >= 8      # Higdon's Saturday runs


def test_at_most_n_run_days_a_week_drops_the_lightest_runs_first():
    t = library.load("half_marathon")
    p = profile()
    a = assess(t, "novice", p.history, START, RACE, p.anchor_weekday)
    result = generate(t, "novice", p, START, RACE, a["entry_week"], a["extra_weeks"], max_days={"run": 3})
    assert result["errors"] == []
    for n in {d["week"] for d in result["days"]}:
        run_days = [d for d in result["days"] if d["week"] == n and any(x["discipline"] == "run" for x in d["sessions"])]
        assert len(run_days) <= 3
    assert len(sessions(result, "run.long")) == len(NOVICE_LONG_MI) + 1          # never the long run
    assert any("run.short (at most 3 run days)" in c for c in result["changes"])


def test_too_little_time_for_the_lifts_a_rule_asks_is_said_with_the_reason_and_the_fixes():
    from modules.goals.messages import strength_short
    t = library.load("half_marathon")
    p = profile(minutes_per_session=90, easy_pace=6.5)
    a = assess(t, "novice", p.history, START, RACE, p.anchor_weekday)
    result = generate(t, "novice", p, START, RACE, a["entry_week"], a["extra_weeks"],
                      {"base": 2, "build": 2, "peak": 2})
    assert result["errors"] == [] and result["strength_short"]
    assert strength_short(result["strength_short"], {"strength_sessions_per_week": 2}, 6) == (
        "Your rules ask for 2 lifts a week; 1 fits, because a run plus a lift needs about 92 min and you have 90. "
        "More time on those days, or another training day, would fit both.")
    assert strength_short(result["strength_short"], {}, 6) is None             # no rule: nothing to say
    roomy = generate(t, "novice", profile(minutes_per_session=120), START, RACE, a["entry_week"], a["extra_weeks"],
                     {"base": 2, "build": 2, "peak": 2})
    assert strength_short(roomy["strength_short"], {"strength_sessions_per_week": 2}, 6) is None
