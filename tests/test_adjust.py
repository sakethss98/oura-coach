"""modules/goals/adjust.py: missed, moved, held, repeated, paused; each with its reason; idempotent."""
from datetime import date, timedelta

from programs import library
from programs.profile import Profile
from modules.goals.adjust import adjust
from modules.goals.generator import generate

T = library.load("half_marathon")
P = Profile(days_per_week=6, anchor_weekday=6, minutes_per_session=110, longest_session_min=150, easy_pace=6.5,
            race_pace=7.0)
START = date(2026, 11, 16)
RACE = date(2027, 2, 15)
DAYS = generate(T, "novice", P, START, RACE)["days"]


def day(d: date) -> dict:
    return next(x for x in DAYS if x["date"] == d)


def ran(*runs) -> dict:
    """signals_by_day with runs [(date, km)] (a neutral day: only the workouts matter here)."""
    out = {}
    for d, km in runs:
        out.setdefault(d.isoformat(), {"workouts": []})["workouts"].append(
            {"kind": "run", "minutes": km * 6.5, "distance_km": km, "start": None, "end": None, "source": "manual"})
    return out


def did_everything(until: date, skip=()) -> dict:
    out = {}
    for d in DAYS:
        if d["date"] < until and d["date"] not in skip:
            for x in d["sessions"]:
                kind = {"run": "run", "cross": "cycle", "strength": "strength"}[x["discipline"]]
                w = {"kind": kind, "minutes": x["amount"] if x["discipline"] != "run" else x["amount"] * 6.5,
                     "distance_km": x["amount"] if x["discipline"] == "run" else None, "start": None, "end": None,
                     "source": "device"}
                out.setdefault(d["date"].isoformat(), {"workouts": []})["workouts"].append(w)
    return out


FIRST_LONG = next(d["date"] for d in DAYS if d["anchor"])
TUESDAY = FIRST_LONG - timedelta(days=5)


def statuses(days, d):
    return [x.get("status", "planned") for x in next(x for x in days if x["date"] == d)["sessions"]]


def test_done_at_70_percent_and_a_missed_easy_run_is_dropped():
    signals = did_everything(FIRST_LONG + timedelta(days=1), skip={TUESDAY})
    signals[(TUESDAY + timedelta(days=2)).isoformat()]["workouts"][0]["distance_km"] *= 0.7   # Thu at 70%
    out = adjust(DAYS, signals, FIRST_LONG + timedelta(days=1), T, P, "novice")
    assert statuses(out["days"], TUESDAY) == ["missed", "missed"]          # the run and the lift after it
    assert statuses(out["days"], TUESDAY + timedelta(days=2)) == ["done"]
    assert [c["kind"] for c in out["changes"]] == ["dropped", "dropped", "repeat"]     # 2 missed: week repeats


FIT = Profile(**{**P.__dict__, "history": {"run": {"weekly": [20, 21, 22], "longest_30d": 8}}})


def test_a_missed_long_run_moves_to_a_free_day_before_the_next_long_one():
    signals = did_everything(FIRST_LONG, skip=set())
    out = adjust(DAYS, signals, FIRST_LONG + timedelta(days=1), T, FIT, "novice")
    assert out["errors"] == []
    moved = [c for c in out["changes"] if c["kind"] == "moved"]
    assert moved and "the long run session missed" in moved[0]["reason"]
    new_day = moved[0]["day"]
    assert FIRST_LONG + timedelta(days=2) <= new_day < FIRST_LONG + timedelta(days=6)    # not the light day after
    assert next(x for x in out["days"] if x["date"] == new_day)["sessions"][0]["moved_from"] == FIRST_LONG.isoformat()
    assert statuses(out["days"], FIRST_LONG) == ["rescheduled"]                    # counted once, on its new day


def test_a_move_that_would_break_the_growth_check_holds_the_next_long_run_instead():
    out = adjust(DAYS, did_everything(FIRST_LONG), FIRST_LONG + timedelta(days=1), T, P, "novice")   # no history
    assert out["errors"] == [] and not [c for c in out["changes"] if c["kind"] == "moved"]
    assert statuses(out["days"], FIRST_LONG) == ["missed"]


def test_with_no_free_day_the_next_long_run_is_held():
    first_long_week_3 = [d["date"] for d in DAYS if d["anchor"]][2]
    later = first_long_week_3 - timedelta(days=1)                # today = the day before the next long run
    signals = did_everything(later, skip={[d["date"] for d in DAYS if d["anchor"]][1]})
    out = adjust(DAYS, signals, later, T, P, "novice")
    held = [c for c in out["changes"] if c["kind"] == "held"]
    assert held and held[0]["day"] == first_long_week_3


def test_two_misses_in_a_week_repeat_that_week_but_never_the_taper():
    out = adjust(DAYS, did_everything(FIRST_LONG + timedelta(days=1), skip={TUESDAY, TUESDAY + timedelta(days=2)}),
                 FIRST_LONG + timedelta(days=1), T, P, "novice")
    repeat = [c for c in out["changes"] if c["kind"] == "repeat"]
    assert repeat and repeat[0]["reason"].startswith("3 sessions missed in week 1")
    nxt = [d for d in out["days"] if d["week"] == 2]
    assert all(x.get("repeat_of") == 1 for d in nxt for x in d["sessions"])
    last_training = max(d["week"] for d in DAYS if d["phase"] != "taper")
    before_race = RACE - timedelta(days=1)
    out = adjust(DAYS, did_everything(before_race, skip={d["date"] for d in DAYS if d["week"] == last_training}),
                 before_race, T, P, "novice")
    assert not [c for c in out["changes"] if c["kind"] == "repeat" and c["day"] == RACE]


def test_a_low_readiness_day_is_marked_moved():
    out = adjust(DAYS, {}, TUESDAY, T, P, "novice", band_by_day={TUESDAY: "recover"})
    assert statuses(out["days"], TUESDAY) == ["moved", "moved"]
    assert out["changes"][0]["reason"] == "low readiness: made easy"


def test_a_paused_week_is_skipped_and_the_first_week_back_repeats_the_last_week_done():
    week2 = [d["date"] for d in DAYS if d["week"] == 2]
    today = week2[0]
    out = adjust(DAYS, did_everything(today), today, T, P, "novice", paused=[(week2[0], week2[-1], "traveling")])
    assert all(s == "skipped" for d in week2 for s in statuses(out["days"], d))
    repeat = [c for c in out["changes"] if c["kind"] == "repeat"]
    assert repeat and repeat[0]["reason"] == "back after a paused week 2"
    assert all(x.get("repeat_of") == 1 for d in out["days"] if d["week"] == 3 for x in d["sessions"])


def test_running_it_again_changes_nothing():
    signals = did_everything(FIRST_LONG + timedelta(days=1), skip={TUESDAY, FIRST_LONG})
    once = adjust(DAYS, signals, FIRST_LONG + timedelta(days=1), T, P, "novice")
    twice = adjust(once["days"], signals, FIRST_LONG + timedelta(days=1), T, P, "novice")
    assert twice["changes"] == [] and twice["days"] == once["days"]


def _weeks(numbers):
    return [d["date"] for d in DAYS if d["week"] in numbers]


def test_a_pause_that_breaks_the_checks_changes_nothing_and_returns_the_options():
    week5 = _weeks({5})
    out = adjust(DAYS, did_everything(week5[0]), _weeks({6})[0], T, FIT, "novice",
                 paused=[(week5[0], week5[-1], "traveling")])
    assert out["errors"] and out["changes"] == [] and out["days"] == DAYS            # nothing is written
    later, skip = out["options"]
    assert later["kind"] == "later_date" and later["event"] > RACE and later["event"].weekday() == RACE.weekday()
    assert skip == {"kind": "skip_lighter", "skip_weeks": [6, 9], "weeks": 7, "resume_week": 4, "event": RACE}


def test_a_long_pause_offers_only_a_later_date_when_skipping_light_weeks_isnt_enough():
    paused = _weeks({5, 6, 7})
    out = adjust(DAYS, did_everything(paused[0]), _weeks({8})[0], T, FIT, "novice",
                 paused=[(paused[0], paused[-1], "injured")])
    assert [o["kind"] for o in out["options"]] == ["later_date"] and out["options"][0]["resume_week"] == 4
