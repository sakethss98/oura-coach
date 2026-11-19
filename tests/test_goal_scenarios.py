"""Part E scenarios (docs/scenarios.md E-1..E-16): goal programs through the agent, scripted and live.
The race here (a half marathon on Mon Feb 15, 2027, under 2:30) is test data only. Fixtures' today is Wed Oct 7, 2026.
Code-only scenarios (E-8, E-9, E-16) are engine tests in test_generator.py / test_program_validate.py."""
import re
from datetime import date, timedelta

import pytest

from connectors.health import Health, neutral_day
from core import db
from core.agent import MEDICAL, NOT_MEASURED
from core.reply_rules import GUILT
from modules.goals import direction, service
from tests.conftest import call, judge, live, needs_fixtures, parsed_checkin, say
from evals.setups import ANSWERS, RACE, history, novice_runs, set_up  # noqa: F401  (shared with evals)
from tests.test_replay import Chat, at, coach  # noqa: F401  (coach is a fixture)

pytestmark = needs_fixtures          # the scenarios run on fixtures/ (gitignored personal data)

GOAL = "I want to run a half marathon on Feb 15 2027"


@pytest.fixture
def runner(patch_oura):
    raw = patch_oura()
    today = at(9).date()
    history(raw, today, novice_runs())
    return raw


# --- E-1 set up a dated goal, E-13 a target time ----------------------------------------------------------

def test_e_1_questions_two_at_a_time_then_the_plan_then_yes(coach, user, chat_model, runner):
    chat = Chat(coach, user, chat_model)
    first = chat.send(GOAL, 9, 0, [call("create_goal", goal_text=GOAL, date_text="Feb 15 2027"), say("")])
    assert first == ("A few questions so the plan fits you:\nHow many days a week can you train?\n"
                     "How long can a usual session be, in minutes?")              # date and level: already known
    chat.send("5 days, up to 90 minutes", 9, 2, [call("answer_goal", days_per_week=5, minutes_per_session=90), say("")])
    chat.send("150 minutes on Sundays, no injuries", 9, 4, [
        call("answer_goal", longest_session_min=150, limits="none", long_session_day="sunday"), say("")])
    preview = chat.send("a gym, and under 2:30", 9, 6, [
        call("answer_goal", equipment="gym", target_time_text="2:30"), say("")])
    assert preview.startswith("Half marathon, Mon Feb 15 · 18 weeks from now.\nPlan: novice (Hal Higdon")
    assert "Long sessions on Sundays, 6.4 km → 16.1 km; strength about 2 times a week." in preview
    assert "about 2:27 looks likely (approximate), so 2:30 looks within reach." in preview
    assert preview.endswith("Set it up?") and not db.program_days(db.current_goal(user.id)["id"])   # nothing yet
    done = chat.send("yes", 9, 8, [call("confirm_goal", choice="yes"), say("")])
    goal = db.current_goal(user.id)
    assert goal["status"] == "active" and done.startswith("Done: your half marathon plan is set up for Mon Feb 15.")
    days = db.program_days(goal["id"])
    assert days[-1]["date"] == RACE and [x["type"] for x in days[-1]["sessions"]] == ["race"]


def test_e_1_a_yes_in_the_same_turn_as_the_question_sets_nothing_up(coach, user, chat_model, runner):
    goal = db.add_goal(user.id, "half marathon", "half_marathon", RACE, "setting_up")
    for key, value in {**ANSWERS, "level": "some", "easy_pace": 6.5, "event_date": RACE.isoformat()}.items():
        if key != "target_time":
            db.set_goal_answer(goal["id"], key, value, "user")
    Chat(coach, user, chat_model).send("no target time", 9, 0, [
        call("answer_goal", target_time_text="none"), call("confirm_goal", choice="yes"), say("")])
    assert db.current_goal(user.id)["status"] == "proposed"            # the model can't answer for them


def test_e_13_a_slower_5k_makes_the_target_a_stretch(coach, user, chat_model, patch_oura):
    raw = patch_oura()
    history(raw, at(9).date(), novice_runs((44, 36, 37)))              # about 7:15 per km
    chat = Chat(coach, user, chat_model)
    chat.send(GOAL, 9, 0, [call("create_goal", goal_text=GOAL, date_text="Feb 15 2027"), say("")])
    preview = chat.send("5 days, 90 min, 150 on Sundays, no injuries, a gym, under 2:30", 9, 2, [
        call("answer_goal", days_per_week=5, minutes_per_session=90, longest_session_min=150, limits="none",
             equipment="gym", long_session_day="sunday", target_time_text="2:30"), say("")])
    assert "looks likely (approximate), so 2:30 looks like a stretch." in preview


# --- E-2 a date too soon ----------------------------------------------------------------------------------

def test_e_2_a_date_too_soon_gets_the_earliest_that_fits(coach, user, chat_model, runner):
    chat = Chat(coach, user, chat_model)
    soon = "I want to run a half marathon on Nov 15"
    chat.send(soon, 9, 0, [call("create_goal", goal_text=soon, date_text="Nov 15"), say("")])
    reply = chat.send("5 days, 90 min, 150 on Sundays, no injuries, a gym, no target", 9, 2, [
        call("answer_goal", days_per_week=5, minutes_per_session=90, longest_session_min=150, limits="none",
             equipment="gym", long_session_day="sunday", target_time_text="none"), say("")])
    assert reply.startswith("Sun Nov 15 is 5 weeks away, and this plan needs 11. The earliest date that fits is "
                            "Sun Dec 27.")             # fit enough to start at week 2: 11 weeks, not 12
    assert db.current_goal(user.id)["status"] == "not_feasible" and not db.program_days(db.current_goal(user.id)["id"])


# --- E-3 today's session, E-11 a run then strength, E-6 on track ---------------------------------------------

def test_e_3_and_e_11_todays_session_is_the_programs_run_then_strength(coach, user, chat_model, runner):
    set_up(user, at(9).date() - timedelta(days=3))                   # starts Mon Oct 5: today is week 1's Wednesday
    reply = Chat(coach, user, chat_model).send("what's today's session?", 9, 0, [call("goal_today"), say("")])
    assert reply.startswith("Today (week 1 of ") and ", base): short easy run, 3.2 km (about 21 min), then " \
                                                       "lower-body strength, 40 min." in reply


def test_with_a_program_running_a_training_message_also_gets_the_programs_skill(coach, user, chat_model, runner):
    chat = Chat(coach, user, chat_model)
    chat.send("what's my plan today?", 9, 0, [say("Check in first.")])          # labeled training
    assert "goal_status" not in chat_model.last_offered                           # no program: training alone
    set_up(user, at(9).date() - timedelta(days=1))
    chat.send("what's my plan today?", 9, 30, [say("Today is an easy run.")])
    assert {"goal_today", "goal_status", "start_checkin"} <= set(chat_model.last_offered)


def test_e_6_on_track_says_sessions_done_and_weeks_left(coach, user, chat_model, runner):
    set_up(user, at(9).date() - timedelta(days=17))
    reply = Chat(coach, user, chat_model).send("am I on track?", 9, 0, [call("goal_status"), say("")])
    assert reply.startswith("Week 3 of ") and "sessions done so far." in reply and "weeks to Mon Feb 15." in reply


# --- E-4 a missed long run, E-5 two misses in a week (adjusted at ingest) -------------------------------------

KINDS = {"run": "run", "cross": "cycle", "strength": "strength"}


def did(days: list[dict], until: date, skip: set[date]) -> dict:
    """Neutral days where they did every planned session before `until` except on the `skip` days."""
    out = {}
    for d in days:
        if d["date"] < until and d["date"] not in skip:
            out[d["date"].isoformat()] = {"workouts": [
                {"kind": KINDS[x["discipline"]], "minutes": x["amount"] * 6.5 if x["discipline"] == "run" else x["amount"],
                 "distance_km": x["amount"] if x["discipline"] == "run" else None, "start": None, "end": None,
                 "source": "device"} for x in d["sessions"]]}
    return out


def test_e_4_a_missed_long_run_is_moved_or_the_next_held(coach, user, chat_model, runner, monkeypatch):
    goal = set_up(user, at(9).date() - timedelta(days=17), event=date(2026, 12, 13))      # no spare weeks
    days = db.program_days(goal["id"])
    last_long = max(d["date"] for d in days if d["anchor"] and d["date"] < at(9).date())
    monkeypatch.setattr(service, "signals_for", lambda *a, **k: did(days, at(9).date(), {last_long}))
    reply = Chat(coach, user, chat_model).send("am I on track?", 9, 0, [call("goal_status"), say("")])
    changes = [a for a in db.program_adjustments(goal["id"]) if a["kind"] in ("moved", "held")]
    assert changes and "the long run session missed on" in changes[0]["reason"]
    assert "Last change: the long run session missed on" in reply and "you missed" not in reply.lower()


def test_e_5_two_misses_in_a_week_repeat_that_week(coach, user, chat_model, runner, monkeypatch):
    goal = set_up(user, at(9).date() - timedelta(days=10), event=date(2026, 12, 20))     # this week is week 2
    days = db.program_days(goal["id"])
    week2 = [d["date"] for d in days if d["week"] == 2 and d["sessions"] and d["date"] < at(9).date()]
    assert len(week2) == 2                                                 # Monday and Tuesday, before today
    monkeypatch.setattr(service, "signals_for", lambda *a, **k: did(days, at(9).date(), set(week2)))
    Chat(coach, user, chat_model).send("am I on track?", 9, 0, [call("goal_status"), say("")])
    repeats = [a for a in db.program_adjustments(goal["id"]) if a["kind"] == "repeat"]
    assert repeats and repeats[0]["reason"].endswith("missed in week 2")
    assert all(x.get("repeat_of") == 2 for d in db.program_days(goal["id"]) if d["week"] == 3 for x in d["sessions"])


# --- E-7 a direction goal, E-10 checking it --------------------------------------------------------------------

def test_e_7_a_direction_becomes_rules_they_confirm_and_the_plan_follows(coach, user, chat_model, runner, monkeypatch):
    monkeypatch.setattr(direction, "propose", lambda text, user_id=None: [
        {"key": "strength_sessions_per_week", "value": 3, "why": "You want lifting to stay part of your week."}])
    chat = Chat(coach, user, chat_model)
    shown = chat.send("I want to be a runner who also lifts", 9, 0, [
        call("set_direction_goal", text="I want to be a runner who also lifts"), say("")])
    assert shown == ("Here's how I'd shape your plan around that:\n- 3 strength sessions a week (You want lifting "
                     "to stay part of your week.)\nKeep these, or change any?")
    saved = chat.send("yes", 9, 2, [call("confirm_direction_rules", choice="yes"), say("")])
    assert saved == "Saved: 3 strength sessions a week." and db.confirmed_direction_rules(user.id)
    goal = set_up(user, at(9).date())
    build = [d for d in db.program_days(goal["id"]) if d["phase"] == "build"]
    per_week = [sum(x["discipline"] == "strength" for d in build if d["week"] == w for x in d["sessions"])
                for w in sorted({d["week"] for d in build})]
    assert max(per_week) == 3 and min(per_week) >= 1           # 3 where their 90 minutes allow it


def test_e_10_direction_check_is_code_milestones_and_says_what_isnt_measured(coach, user, chat_model, runner):
    d = db.add_direction(user.id, "a runner who lifts", {"strength_sessions_per_week": 2, "max_run_days_per_week": 4})
    db.confirm_direction(d["id"], d["rules"])
    model_line = ("Not yet: you run about 3 days a week but there's no strength in the last 4 weeks (approximate). "
                  "I can't measure lifts or muscle.")
    reply = Chat(coach, user, chat_model).send("am I becoming a hybrid athlete by December?", 9, 0, [
        call("direction_check"), say(model_line)])
    # Code says what isn't measured, in one fixed line, and drops the model's own sentence saying it (H-2).
    assert reply == model_line.replace(" I can't measure lifts or muscle.", "") + f"\n\n{NOT_MEASURED}"
    result = next(m for m in chat_model.seen[-1] if getattr(m, "name", None) == "direction_check")
    assert '"actual": 0.2' in result.content and "not_measured" in result.content     # one short lift in 4 weeks


# --- E-12 an ad-hoc run + lift on a program day -----------------------------------------------------------

def test_e_12_a_run_then_weights_takes_the_programs_run(coach, user, chat_model, runner, fake_llm):
    set_up(user, at(9).date() - timedelta(days=3))
    fake_llm.checkin = parsed_checkin(4, 1, blocks=[("easy_run", None), ("strength", None)])
    chat = Chat(coach, user, chat_model)
    chat.send("give me a run followed by weights", 9, 0, [
        call("start_checkin", request_text="a run followed by weights"), say("")])
    chat.send("4 1", 9, 2, [call("submit_checkin", answer="4 1"), say("")])
    plan = db.latest_planned(user.id, at(9).date())["plan_json"]
    assert [b["workout_type"] for b in plan["blocks"]][:2] == ["easy_run", "strength"]
    assert plan["blocks"][0]["duration_min"] == 21                    # the program's 3.2 km at their 6:30 pace


# --- E-14 a knee limit ----------------------------------------------------------------------------------------

OFFER = "Sorry about the knee. Want me to plan around your knee, with no running impact, until it's better?"


def test_e_14_a_knee_limit_is_saved_on_yes_and_the_next_plan_has_no_run(coach, user, chat_model, fake_llm):
    chat = Chat(coach, user, chat_model)
    first = chat.send("my knee hurts when I run", 9, 0, [say(OFFER)])
    assert first == f"{OFFER}\n\n{MEDICAL}" and not db.active_limits(user.id)
    saved = chat.send("yes", 9, 2, [call("save_limit", body_area="knee", kind="pain", avoid=["running_impact"],
                                         intensity_cap=None, start_text=None, end_text=None), say("")])
    assert saved == ("Saved: no running impact for your knee until you tell me it's better.\n\n"
                     f"{MEDICAL}")                   # every pain or injury reply, a saved limit too (H-1)
    fake_llm.plan = fake_llm.plan.model_copy(update={"workout_type": "easy_run", "intensity": 3})
    chat.send("want to work out now", 9, 4, [call("start_checkin", request_text=None), say("")])
    chat.send("4 1", 9, 6, [call("submit_checkin", answer="4 1"), say("")])
    plan = db.latest_planned(user.id, at(9).date())["plan_json"]
    assert plan["workout_type"] in ("walk", "mobility")


def test_a_pain_or_illness_answer_ends_with_the_codes_line_never_the_models_own(coach, user, chat_model):
    reply = Chat(coach, user, chat_model).send("I have a cold, should I run?", 9, 0, [say(
        "With only a runny nose, an easy 20 min jog is usually fine. This is not medical advice. "
        "See a doctor if you get a fever. Easy jog or rest?")])
    assert reply == ("With only a runny nose, an easy 20 min jog is usually fine. See a doctor if you get a fever. "
                     f"Easy jog or rest?\n\n{MEDICAL}")
    assert reply.count("medical advice") == 1


def test_e_14_a_yes_to_the_offer_always_saves_the_limit(coach, user, chat_model):
    chat = Chat(coach, user, chat_model)
    chat.send("my knee hurts when I run", 9, 0, [say(OFFER)])
    chat.send("yes", 9, 2, [call("save_limit", body_area="knee", kind="pain", avoid=["running_impact"],
                                 intensity_cap=None, start_text=None, end_text=None), say("")])
    assert chat_model.tool_choices[-2:] == ["save_limit", None]          # code made the save happen first
    assert db.active_limits(user.id)


def test_e_14_saved_without_the_tool_is_never_said(coach, user, chat_model):
    false = "Saved: no running impact for your knee until you tell me it's better."
    reply = Chat(coach, user, chat_model).send("my knee hurts when I run", 9, 0, [say(false), say(false)])
    assert "Saved" not in reply and not db.active_limits(user.id)


# --- E-15 no wearable -------------------------------------------------------------------------------------------

def test_e_15_no_wearable_asks_their_pace_and_still_plans(coach, user, chat_model, monkeypatch):
    def chat_only(self, user_, start, end, now=None):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        manual = self.manual_by_day(user_, start, end)
        return {d.isoformat(): neutral_day(d, None, manual.get(d.isoformat(), [])) for d in days}
    monkeypatch.setattr(Health, "signals", chat_only)
    chat = Chat(coach, user, chat_model)
    chat.send(GOAL, 9, 0, [call("create_goal", goal_text=GOAL, date_text="Feb 15 2027"), say("")])
    reply = chat.send("5 days, 90 min, 150 on Sundays, some running, no injuries, no gear, no target, 6:45 a km",
                      9, 2, [call("answer_goal", days_per_week=5, minutes_per_session=90, longest_session_min=150,
                                  level="some", limits="none", equipment="none", long_session_day="sunday",
                                  target_time_text="none", easy_pace_text="6:45"), say("")])
    assert reply.endswith("Set it up?") and "from week 1" in reply
    chat.send("yes", 9, 4, [call("confirm_goal", choice="yes"), say("")])
    assert db.current_goal(user.id)["status"] == "active"


# --- live ---------------------------------------------------------------------------------------------------

def _steps(chat, messages_):
    return [chat.send(m, 9, 2 * i) for i, m in enumerate(messages_)]


@live
def test_live_e_1_set_up_a_goal(coach, user, runner):
    chat = Chat(coach, user)
    replies = _steps(chat, [GOAL, "5 days, up to 90 minutes", "150 minutes on Sundays, no injuries",
                            "a gym, and under 2:30", "yes"])
    chat.print("scenario E-1: set up a dated goal")
    goal = db.current_goal(user.id)
    assert any("Set it up?" in r for r in replies[:-1]) and goal and goal["status"] == "active"
    assert db.program_days(goal["id"])[-1]["date"] == RACE


@live
def test_live_e_2_a_date_too_soon(coach, user, runner):
    chat = Chat(coach, user)
    replies = _steps(chat, ["I want to run a half marathon on Nov 15",
                            "5 days, 90 min, 150 on Sundays, no injuries, a gym, no target time"])
    chat.print("scenario E-2: a date too soon")
    assert "earliest date that fits" in " ".join(replies) and db.current_goal(user.id)["status"] == "not_feasible"


@live
def test_live_e_3_and_e_11_todays_session(coach, user, runner):
    set_up(user, at(9).date() - timedelta(days=3))
    chat = Chat(coach, user)
    reply = chat.send("what's today's session?", 9, 0)
    chat.print("scenario E-3/E-11: today's session")
    assert "short easy run, 3.2 km" in reply and "then lower-body strength" in reply


@live
def test_live_e_4_e_5_e_6_on_track(coach, user, runner):
    goal = set_up(user, at(9).date() - timedelta(days=17))
    chat = Chat(coach, user)
    reply = chat.send("am I on track?", 9, 0)
    chat.print("scenario E-4/E-5/E-6: on track after missed sessions")
    assert "sessions done so far" in reply and "Last change: " in reply
    assert "repeat" in {a["kind"] for a in db.program_adjustments(goal["id"])}
    assert not GUILT.search(reply), reply


@live
def test_live_e_7_direction_goal(coach, user, runner):
    chat = Chat(coach, user)
    replies = _steps(chat, ["I want to be a runner who also lifts", "yes"])
    chat.print("scenario E-7: a direction goal")
    assert "Keep these, or change any?" in replies[0] and db.confirmed_direction_rules(user.id)


@live
def test_live_e_10_direction_check(coach, user, runner):
    d = db.add_direction(user.id, "a runner who lifts", {"strength_sessions_per_week": 2})
    db.confirm_direction(d["id"], d["rules"])
    chat = Chat(coach, user)
    reply = chat.send("am I becoming a hybrid athlete by December?", 9, 0)
    chat.print("scenario E-10: direction check")
    assert any(w in reply.lower() for w in ("lift", "muscle", "measure"))
    verdict = judge("am I becoming a hybrid athlete by December?", reply)
    assert verdict.answered and not GUILT.search(reply), verdict.reason
    assert db.confirmed_direction_rules(user.id) == {"strength_sessions_per_week": 2}     # never replaced


@live
def test_live_e_12_run_then_weights_on_a_program_day(coach, user, runner):
    set_up(user, at(9).date() - timedelta(days=3))
    chat = Chat(coach, user)
    _steps(chat, ["give me a run followed by weights", "4 1"])
    chat.print("scenario E-12: a run then weights on a program day")
    plan = db.latest_planned(user.id, at(9).date())["plan_json"]
    assert [b["workout_type"] for b in plan["blocks"]][:2] == ["easy_run", "strength"]


@live
def test_live_e_13_target_time(coach, user, runner):
    chat = Chat(coach, user)
    replies = _steps(chat, [GOAL, "5 days, 90 minutes a session, 150 minutes on Sundays, no injuries, a gym, "
                                  "and I'd like to finish under 2:30"])
    chat.print("scenario E-13: a target time")
    assert any("within reach" in r or "like a stretch" in r for r in replies)


@live
def test_live_e_14_knee_limit(coach, user):
    chat = Chat(coach, user)
    replies = _steps(chat, ["my knee hurts when I run, can you plan around it?", "yes, until it's better"])
    chat.print("scenario E-14: a knee limit")
    limits = db.active_limits(user.id)
    assert limits and "running_impact" in limits[0]["avoid"]
    assert "Saved: no running impact" in " ".join(replies) and "for your knee" in " ".join(replies)


@live
def test_live_e_15_no_wearable(coach, user, monkeypatch):
    def chat_only(self, user_, start, end, now=None):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return {d.isoformat(): neutral_day(d, None, []) for d in days}
    monkeypatch.setattr(Health, "signals", chat_only)
    chat = Chat(coach, user)
    replies = _steps(chat, [GOAL, "5 days, 90 min, 150 on Sundays, some running, no injuries, no gear, no target, "
                                  "about 6:45 per km", "yes"])
    chat.print("scenario E-15: no wearable")
    goal = db.current_goal(user.id)
    assert goal and goal["status"] in ("active", "proposed", "setting_up")
    assert any("Set it up?" in r or "?" in r for r in replies)


# --- every text the person reads is plain words ------------------------------------------------------------------

NOT_PLAIN = re.compile(r"\b[a-z]+_[a-z_]+\b|\bnull\b|\bNone\b|\bwindow\b")


def test_goal_texts_have_no_snake_case_null_or_window(user):
    from programs import library
    from programs.profile import Profile
    from modules.goals import feasibility, messages
    from modules.goals.generator import generate
    t = library.load("half_marathon")
    p = Profile(days_per_week=5, anchor_weekday=6, minutes_per_session=90, longest_session_min=150, easy_pace=6.5,
                race_pace=7.1, history={"run": {"weekly": [16, 16, 18], "longest_30d": 6}})
    start = date(2026, 10, 8)
    a = feasibility.assess(t, "novice", p.history, start, RACE, 6)
    days = generate(t, "novice", p, start, RACE, a["entry_week"], a["extra_weeks"])["days"]
    finish = {"predicted_minutes": 147, "target_minutes": 150, "within": True, "from": {"distance_km": 5.0}}
    texts = [messages.preview(t, "novice", a, RACE, days, p, finish), messages.set_up(t, days, RACE),
             messages.not_feasible(t, feasibility.assess(t, "novice", p.history, date(2027, 1, 4), RACE, 6), RACE),
             messages.not_feasible(t, feasibility.assess(t, "novice", {"run": {"weekly": [5, 6, 6], "longest_30d": 3}},
                                                         start, RACE, 6), RACE),
             messages.status_text(t, days, date(2026, 11, 20), RACE, [{"kind": "held", "reason": "the long run "
                                                                       "session missed on Sun Nov 15"}]),
             messages.limit_saved("knee", ["running_impact", "deep_knee_bend"], 3, None),
             messages.options_text([{"kind": "later_date", "event": date(2027, 3, 1), "resume_week": 4},
                                    {"kind": "skip_lighter", "skip_weeks": [6, 9], "resume_week": 4}]),
             messages.direction_proposed([{"key": "protein_g_per_kg", "value": 1.8, "why": "To support lifting."}],
                                         direction.catalog()),
             messages.no_template("learn to juggle")]
    texts += [messages.today_text(t, d, days[-1]["week"], p) for d in days if d["sessions"]]
    for text in texts:
        assert not NOT_PLAIN.search(text), text


def test_e_1_answers_they_never_gave_are_not_saved(coach, user, chat_model, runner):
    chat = Chat(coach, user, chat_model)
    chat.send(GOAL, 9, 0, [call("create_goal", goal_text=GOAL, date_text="Feb 15 2027"), say("")])
    reply = chat.send("5 days, up to 90 minutes", 9, 2, [
        call("answer_goal", days_per_week=5, minutes_per_session=90),
        call("answer_goal", longest_session_min=150, limits="none", equipment="gym", long_session_day="sunday"),
        say("")])
    answers = service.answers(db.current_goal(user.id))
    assert answers["days_per_week"] == 5 and answers["minutes_per_session"] == 90
    assert not {"longest_session_min", "limits", "equipment", "long_session_day"} & set(answers)    # never theirs
    assert reply.count("A few questions so the plan fits you:") == 1          # the same questions, sent once


def test_e_13_a_target_time_inside_words_is_read(coach, user, chat_model, runner):
    goal = db.add_goal(user.id, "half marathon", "half_marathon", RACE, "setting_up")
    Chat(coach, user, chat_model).send("a gym, and under 2:30", 9, 0, [
        call("answer_goal", equipment="gym", target_time_text="under 2:30"), say("")])
    assert service.answers(db.get_goal(goal["id"]))["target_time"] == 150


def test_e_10_a_question_never_replaces_confirmed_direction_rules(coach, user, chat_model, runner, monkeypatch):
    d = db.add_direction(user.id, "a runner who lifts", {"strength_sessions_per_week": 2})
    db.confirm_direction(d["id"], d["rules"])
    monkeypatch.setattr(direction, "propose", lambda *a, **k: pytest.fail("no new proposal for a question"))
    Chat(coach, user, chat_model).send("am I becoming a hybrid athlete by December?", 9, 0, [
        call("set_direction_goal", text="hybrid athlete by December"), call("direction_check"),
        say("Not yet, going by your last 4 weeks (approximate); I can't measure lifts or muscle.")])
    assert db.confirmed_direction_rules(user.id) == {"strength_sessions_per_week": 2}


def test_e_7_the_preview_says_when_the_lifts_a_rule_asks_dont_fit(coach, user, chat_model, runner):
    d = db.add_direction(user.id, "a runner who lifts", {"strength_sessions_per_week": 2})
    db.confirm_direction(d["id"], d["rules"])
    goal = db.add_goal(user.id, "half marathon", "half_marathon", RACE, "setting_up")
    for key, value in {**ANSWERS, "level": "some", "easy_pace": 6.5, "event_date": RACE.isoformat()}.items():
        db.set_goal_answer(goal["id"], key, value, "user")
    preview, _ = service.proposal(user, db.get_goal(goal["id"]), at(9).date(), at(9), True)
    assert ("Your rules ask for 2 lifts a week; 1 fits, because a run plus a lift needs about 92 min and you have "
            "90. More time on those days, or another training day, would fit both.\nSet it up?") in preview
