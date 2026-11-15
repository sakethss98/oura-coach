"""programs/validate.py: every template (library or drafted) and every generated plan must pass the safety checks.
Each bad template here breaks exactly one rule."""
import copy
from datetime import date, timedelta

import pytest

from programs import library
from programs.profile import Limit, Profile
from programs.schema import Template
from programs.validate import validate_plan, validate_template
from modules.goals.generator import generate


def tiny(weeks: list[list[dict]], **changes) -> Template:
    """A one-discipline template: each week a list of sessions {type, amount, at}."""
    data = {
        "name": "tiny", "title": "Tiny", "source": "drafted", "status": "approved",
        "disciplines": {"run": {"measure": "km", "kinds": ["run"], "session_types": {
            "easy": {"label": "easy run", "intensity": 3, "pace": "easy", "movements": ["running_impact"]},
            "hard": {"label": "hard run", "intensity": 5, "hard": True, "pace": "easy", "movements": ["running_impact"]},
            "long": {"label": "long run", "intensity": 4, "hard": True, "long": True, "pace": "easy", "movements": ["running_impact"]},
            "race": {"label": "the race", "intensity": 5, "hard": True, "event": True, "pace": "easy"}}}},
        "shared": {"max_sessions_per_week": 6, "max_hard_per_week": 2},
        "variants": {"only": {"source": "test", "weeks": [
            {"phase": "build", "sessions": [{"discipline": "run", **s} for s in w]} for w in weeks]}},
    }
    for key, value in changes.items():
        data[key] = value
    return Template.model_validate(data)


def week(easy: float, long: float) -> list[dict]:
    return [{"type": "easy", "amount": easy, "at": -4}, {"type": "long", "amount": long, "at": 0}]


@pytest.mark.parametrize("t", library.all_templates() + library.all_templates(library.EXAMPLES),
                         ids=lambda t: t.name)
def test_every_library_and_example_template_passes(t):
    assert validate_template(t) == []


def test_a_week_35_percent_over_the_last_three_fails_and_25_passes():
    assert validate_template(tiny([week(10, 10), week(12.5, 12.5)])) == []                 # +25%: the limit
    errors = validate_template(tiny([week(10, 10), week(13.5, 12.5)]))                     # +30% load
    assert any("load" in e and "30% over the highest of the 3 weeks before" in e for e in errors)
    errors = validate_template(tiny([week(10, 10), week(14, 13)]))                         # +35%
    assert any("35% over" in e for e in errors)


def test_a_lighter_week_does_not_reset_the_baseline():
    # 20 -> 12 (lighter) -> 24: compared with 20, not 12, so +20% passes.
    assert validate_template(tiny([week(10, 10), week(6, 6), week(12, 12)])) == []


def test_a_long_session_40_percent_over_the_last_30_days_fails():
    errors = validate_template(tiny([week(10, 10), week(6, 14)]))           # weekly load flat, long +40%
    assert len(errors) == 1 and "long run session (14.0 km) is 40% over the longest in the 30 days" in errors[0]


def test_seven_training_days_leave_no_rest_day():
    days = [{"type": "easy", "amount": 3, "at": a} for a in range(-6, 0)] + [{"type": "long", "amount": 5, "at": 0}]
    errors = validate_template(tiny([days], shared={"max_sessions_per_week": 7, "max_hard_per_week": 2}))
    assert any("leave fewer than 1 rest day" in e for e in errors)


def test_too_many_hard_sessions_fail():
    days = [{"type": "hard", "amount": 3, "at": -5}, {"type": "hard", "amount": 3, "at": -3},
            {"type": "long", "amount": 6, "at": 0}]
    assert any("3 hard sessions, more than 2" in e for e in validate_template(tiny([days])))


def test_a_dated_event_needs_a_taper():
    event = {"dated": True, "measure": "km", "amount": 21.1, "taper_weeks": 1}
    peak = [{"type": "easy", "amount": 10, "at": -4}, {"type": "long", "amount": 10, "at": -1}]
    no_taper = tiny([week(10, 10), peak + [{"type": "race", "amount": 21.1, "at": 0}]], event=event)
    assert any("no taper in run" in e for e in validate_template(no_taper))
    tapered = tiny([week(10, 10), [{"type": "easy", "amount": 5, "at": -4},
                                   {"type": "race", "amount": 21.1, "at": 0}]], event=event)
    assert validate_template(tapered) == []
    assert any("needs a taper" in e for e in validate_template(tiny(
        [week(10, 10), [{"type": "race", "amount": 21.1, "at": 0}]], event={**event, "taper_weeks": 0})))
    assert any("event is missing" in e for e in validate_template(tiny([week(10, 10)], event=event)))


def test_an_unknown_movement_fails():
    t = tiny([week(10, 10)])
    t.disciplines["run"].session_types["easy"].movements = ["cartwheels"]
    assert validate_template(t) == ["run.easy: unknown movement 'cartwheels' (config/movements.yaml)"]


def test_a_placement_on_a_missing_type_fails():
    t = tiny([week(10, 10)], placement=[{"kind": "keep_away_from_long", "session": "run.tempo", "before": 2}])
    assert validate_template(t) == ["'run.tempo' names a session type that doesn't exist"]


def test_a_template_breaking_its_own_rest_before_long_rule_fails():
    t = tiny([[{"type": "easy", "amount": 5, "at": -1}, {"type": "long", "amount": 5, "at": 0}]],
             placement=[{"kind": "rest_before_long", "days": 1}])
    assert any("the day before the long one" in e for e in validate_template(t))


def test_a_drafted_35_percent_template_never_makes_a_plan_and_pending_review_never_does():
    profile = Profile(days_per_week=6, anchor_weekday=6, easy_pace=6.5)
    bad = tiny([week(10, 10), week(14, 13)])
    with pytest.raises(library.NotApproved, match="35% over"):
        generate(bad, "only", profile, date(2026, 11, 16))
    pending = tiny([week(10, 10), week(11, 11)], status="pending_review")
    assert validate_template(pending) == []
    with pytest.raises(library.NotApproved, match="pending_review"):
        generate(pending, "only", profile, date(2026, 11, 16))


# --- plans: what only the person decides ---------------------------------------------------------------------

RACE_DAY = date(2027, 2, 15)


def _plan(**profile):
    t = library.load("half_marathon")
    p = Profile(**{"days_per_week": 6, "anchor_weekday": 6, "minutes_per_session": 120, "longest_session_min": 180,
                   "easy_pace": 6.5, "race_pace": 7.0, **profile})
    return t, p, generate(t, "novice", p, date(2026, 11, 16), RACE_DAY)


def test_a_session_longer_than_their_time_fails_the_plan():
    t, p, plan = _plan()
    assert plan["errors"] == []
    short_of_time = Profile(**{**p.__dict__, "longest_session_min": 90})        # the 16 km long run takes ~105 min
    assert any("more than the 90 min they have" in e for e in validate_plan(plan["days"], short_of_time, t, "novice"))


def test_a_run_for_someone_who_must_avoid_running_impact_fails_the_plan():
    t, p, plan = _plan()
    knee = Profile(**{**p.__dict__, "limits": [Limit("knee", "pain", ["running_impact"], 3)]})
    errors = validate_plan(plan["days"], knee, t, "novice")
    assert any("uses running_impact, which their knee must avoid" in e for e in errors)
    assert any("over the cap of 3 for their knee" in e for e in errors)


def test_a_plan_without_a_rest_day_in_a_week_fails():
    t, p, plan = _plan()
    days = copy.deepcopy(plan["days"])
    for d in days[:7]:
        d["sessions"] = d["sessions"] or [{"discipline": "cross", "type": "cross", "amount": 20, "minutes": None,
                                           "intensity": 2}]
    assert any("has no rest day" in e for e in validate_plan(days, p, t, "novice"))


def test_a_hard_session_labeled_recovery_still_counts_as_load():
    def with_recovery(intensity: int):
        t = tiny([week(10, 10), week(10, 10) + [{"type": "rec", "amount": 30, "at": -2}]])
        t.disciplines["run"].session_types["rec"] = t.disciplines["run"].session_types["easy"].model_copy(
            update={"intensity": intensity, "recovery": True, "hard": intensity >= 4})
        return t
    assert validate_template(with_recovery(2)) == []                                   # easy recovery: not load
    errors = validate_template(with_recovery(5))                                       # a hard "recovery" session
    assert any("load 50.0 km is 150% over" in e for e in errors)


def test_a_rule_can_apply_to_one_variant_and_must_name_a_real_one():
    t = library.load("half_marathon")
    assert [r.kind for r in t.rules("novice")].count("rest_before_long") == 1
    assert "rest_before_long" not in [r.kind for r in t.rules("intermediate")]
    bad = tiny([week(10, 10)], placement=[{"kind": "rest_before_long", "days": 1, "variants": ["elite"]}])
    assert validate_template(bad) == ["placement rest_before_long: unknown variant 'elite'"]


def test_no_lower_body_lift_within_seven_days_of_the_event():
    t, p, plan = _plan()
    days = copy.deepcopy(plan["days"])
    five_before = next(d for d in days if d["date"] == RACE_DAY - timedelta(days=5))
    five_before["sessions"] = five_before["sessions"] + [{"discipline": "strength", "type": "lower", "amount": 40,
                                                          "minutes": None, "intensity": 3}]
    assert any("strength.lower within 7 days of the event" in e for e in validate_plan(days, p, t, "novice"))
