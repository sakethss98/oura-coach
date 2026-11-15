"""Goal tools for the agent: set up a goal (questions two at a time, then the plan before they say yes), today's
session, how it's going, injuries and limits, and a long-term direction. Every text is built by code and sent as
is (artifact {"verbatim": text}); results carry facts only.
"""
import json
import re
from datetime import date, timedelta
from typing import Literal

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from core import db
from core.timeparse import resolve_dates
from core.tools import Messages, as_json, turn
from programs import library
from modules.goals import direction, messages, paces, profile as profiles, service

Movement = Literal["running_impact", "jumping", "deep_knee_bend", "downhill", "overhead", "spinal_load", "grip",
                   "rotation", "swimming_shoulder"]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SENT = {"sent_as_is": True}


def _sent(facts: dict, text: str) -> tuple[str, dict]:
    return as_json({**facts, **SENT}), {"verbatim": text}


def _asked_this_turn(messages_: list, names: set[str]) -> bool:
    """A question from one of these tools went out in reply to their latest message: no answer yet."""
    last_human = max((i for i, m in enumerate(messages_) if isinstance(m, HumanMessage)), default=-1)
    return any(isinstance(m, ToolMessage) and m.name in names for m in messages_[last_human + 1:])


WORDS = {   # an answer only counts when their words say it (never one the model filled in)
    "equipment": {"gym": r"\bgym\b", "home": r"\bhome\b", "none": r"\b(no|none|nothing)\b"},
    "limits": {"none": r"\b(no|none|nothing|fine|healthy)\b", "saved": r"."},
    "level": {"new": r"\b(new|beginner|starting|start)\b", "some": r"\b(some|bit|sometimes|occasional\w*)\b",
              "regular": r"\b(regular\w*|often|every|most days|lots)\b"},
}
CLOCK = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")


def _their_words(messages_: list) -> str:
    return " ".join(str(m.content) for m in messages_ if isinstance(m, HumanMessage)).lower()


def _number_said(value: float, said: str) -> bool:
    """The number, or the same minutes in hours ("2.5 hours" for 150), is in their messages."""
    if re.search(rf"(?<![\d.:]){value:g}(?![\d.:])", said):
        return True
    hours = value / 60
    return bool(re.search(rf"(?<![\d.]){hours:g}\s*(h|hr|hrs|hours?)\b", said))


def _from_their_words(key: str, value, said: str) -> bool:
    if isinstance(value, (int, float)):
        return _number_said(value, said)
    if key == "long_session_day":
        return value in said or value[:3] in re.findall(r"\b[a-z]{3}", said)
    if key in WORDS:
        return bool(re.search(WORDS[key].get(value, r"$^"), said))
    return True


def _future_day(text: str | None, today: date) -> date | None:
    found = resolve_dates(text, None, today) if text else None
    return found[0] if found and found[0] > today else None


def _next_step(t, goal: dict) -> tuple[str, dict]:
    """The next questions, or (all answered) the plan they'd get."""
    answers = service.answers(goal)
    keys = profiles.open_keys(library.load(goal["template"]), answers)
    if keys:
        return _sent({"goal_id": goal["id"], "asked": keys[:profiles.ASKED_AT_A_TIME]},
                     messages.ask("A few questions so the plan fits you:", keys))
    text, preview = service.proposal(t.user, db.get_goal(goal["id"]), t.day, t.now, t.use_fixtures)
    status = db.get_goal(goal["id"])["status"]
    return _sent({"goal_id": goal["id"], "status": status, "feasible": preview.get("feasible")}, text)


@tool(response_format="content_and_artifact")
def create_goal(goal_text: str, date_text: str | None, config: RunnableConfig) -> tuple[str, dict]:
    """They want to work toward a goal ("I want to run a 10k in the spring"). goal_text: their goal in
    their own words; date_text: its date exactly as written ("May 3", "March 3 2027"), or null. Replaces a goal
    still being set up."""
    t = turn(config)
    current = db.current_goal(t.user.id)
    if current and current["status"] == "active":
        return as_json({"created": False, "reason": "a goal plan is already active", "goal": current["text"]}), {}
    db.end_goals(t.user.id)
    template = service.template_for(goal_text)
    if template is None:
        db.add_goal(t.user.id, goal_text, None, None, "no_template")
        return _sent({"created": False, "template": None}, messages.no_template(goal_text))
    goal = db.add_goal(t.user.id, goal_text, template.name, _future_day(date_text, t.day), "setting_up")
    if goal["event_date"]:
        db.set_goal_answer(goal["id"], "event_date", goal["event_date"], "user")
    start = t.day
    sig = service.signals_for(t.user, t.use_fixtures, start - timedelta(days=service.PROFILE_DAYS),
                              start - timedelta(days=1), t.now)
    service.refresh_data_answers(goal, template, sig, start)
    return _next_step(t, goal)


@tool(response_format="content_and_artifact")
def answer_goal(messages_: Messages, config: RunnableConfig, date_text: str | None = None,
                days_per_week: int | None = None,
                minutes_per_session: int | None = None, longest_session_min: int | None = None,
                level: Literal["new", "some", "regular"] | None = None, limits: Literal["none", "saved"] | None = None,
                equipment: Literal["none", "home", "gym"] | None = None, long_session_day: Weekday | None = None,
                target_time_text: str | None = None, easy_pace_text: str | None = None) -> tuple[str, dict]:
    """Their answers to the goal questions, only what they said in their messages (never fill in an answer they
    didn't give: code keeps only answers found in their words): date_text as written; target_time_text like "1:55"
    or "none"; easy_pace_text like "6:30"; limits "none" when they have no injuries (an injury: save_limit first,
    then limits "saved")."""
    t = turn(config)
    goal = db.current_goal(t.user.id)
    if not goal or goal["status"] not in ("setting_up", "proposed", "not_feasible") or not goal["template"]:
        return as_json({"saved": False, "reason": "no goal is being set up"}), {}
    unread, not_theirs = [], []
    said = _their_words(messages_)
    given = {"days_per_week": days_per_week, "minutes_per_session": minutes_per_session,
             "longest_session_min": longest_session_min, "level": level, "limits": limits, "equipment": equipment,
             "long_session_day": long_session_day}
    for key, value in given.items():
        if value is None:
            continue
        if _from_their_words(key, value, said):
            db.set_goal_answer(goal["id"], key, value, "user")
        else:
            not_theirs.append(key)
    for key, text in (("date", date_text), ("target time", target_time_text), ("pace", easy_pace_text)):
        if text and not (text.strip().lower() in said or any(c in said for c in CLOCK.findall(text))
                         or text.strip().lower() in ("none", "no")):
            not_theirs.append(key)
    if not_theirs:
        date_text = None if "date" in not_theirs else date_text
        target_time_text = None if "target time" in not_theirs else target_time_text
        easy_pace_text = None if "pace" in not_theirs else easy_pace_text
    if date_text:
        d = _future_day(date_text, t.day)
        if d:
            db.update_goal(goal["id"], event_date=d)
            db.set_goal_answer(goal["id"], "event_date", d.isoformat(), "user")
        else:
            unread.append("date")
    if target_time_text:
        lowered = target_time_text.strip().lower()
        clock = CLOCK.search(lowered)                       # "under 1:55" -> 1:55
        minutes = paces.parse_time(clock.group(0)) if clock else None
        if lowered in ("none", "no", "no target") or minutes:
            db.set_goal_answer(goal["id"], "target_time", minutes or profiles.NO_TARGET, "user")
        else:
            unread.append("target time")
    if easy_pace_text:
        pace = profiles.parse_pace(easy_pace_text)
        if pace:
            data = db.goal_answers(goal["id"]).get("easy_pace")
            if not data or data["source"] != "data":         # their runs beat an answer
                db.set_goal_answer(goal["id"], "easy_pace", pace, "user")
        else:
            unread.append("pace")
    if unread or not_theirs:
        facts = {"saved": True, **({"could_not_read": unread} if unread else {}),
                 **({"not_in_their_words": not_theirs} if not_theirs else {})}
        if unread:
            return as_json(facts), {}
        content, artifact = _next_step(t, db.get_goal(goal["id"]))
        return as_json({**facts, **json.loads(content)}), artifact
    return _next_step(t, db.get_goal(goal["id"]))


@tool(response_format="content_and_artifact")
def confirm_goal(choice: Literal["yes", "no"], messages_: Messages, config: RunnableConfig) -> tuple[str, dict]:
    """Their yes or no to "Set it up?" after the plan was shown."""
    t = turn(config)
    if _asked_this_turn(messages_, {"create_goal", "answer_goal"}):
        return as_json({"set_up": False, "reason": "the question was asked in this same turn: no answer yet"}), {}
    goal = db.current_goal(t.user.id)
    if not goal or goal["status"] != "proposed":
        return as_json({"set_up": False, "reason": "no plan is waiting for a yes"}), {}
    if choice == "no":
        db.update_goal(goal["id"], status="ended")
        return _sent({"set_up": False}, "No problem, nothing was set up. Tell me when you want to try another plan.")
    text = service.activate(t.user, goal, t.day, t.now, t.use_fixtures)
    return _sent({"set_up": db.get_goal(goal["id"])["status"] == "active"}, text)


@tool(response_format="content_and_artifact")
def goal_today(config: RunnableConfig) -> tuple[str, dict]:
    """Today's session from their goal plan ("what's today's run?", "what's on my plan today?")."""
    t = turn(config)
    found = service.active(t.user)
    if not found:
        return as_json({"plan": None, "status": "no goal plan is set up"}), {}
    goal, template, days = found
    p, _ = service.profile_for(t.user, goal, template, days[0]["date"], t.now, t.use_fixtures)
    today = service.today_of(days, t.day)
    return _sent({"date": t.day.isoformat()}, messages.today_text(template, today, days[-1]["week"], p))


@tool(response_format="content_and_artifact")
def goal_status(config: RunnableConfig) -> tuple[str, dict]:
    """How their goal plan is going ("am I on track?"): sessions done, the longest session, weeks left."""
    t = turn(config)
    found = service.active(t.user)
    if not found:
        return as_json({"plan": None, "status": "no goal plan is set up"}), {}
    goal, template, days = found
    event = date.fromisoformat(goal["event_date"]) if goal["event_date"] else None
    text = messages.status_text(template, days, t.day, event, db.program_adjustments(goal["id"]))
    if goal.get("options_json"):
        text += "\n" + messages.options_text(goal["options_json"])
    return _sent({"weeks": days[-1]["week"]}, text)


@tool(response_format="content_and_artifact")
def save_limit(body_area: str, kind: Literal["pain", "injury", "condition"], avoid: list[Movement],
               intensity_cap: int | None, start_text: str | None, end_text: str | None,
               config: RunnableConfig) -> tuple[str, dict]:
    """Save an injury or limit to plan around ("my knee hurts, no runs for a while"): body_area in their words
    ("knee"), the movements to avoid, an effort cap 1-5 (2 easy, 3 moderate) or null, and the days as written
    (null end = until they say it's better). Plans and workouts follow it from now on."""
    t = turn(config)
    start = end = None
    if start_text or end_text:
        found = resolve_dates(start_text, end_text, t.day)
        if not found:
            return as_json({"saved": False, "could_not_read": "the days"}), {}
        start, end = found if end_text else (found[0], None)
    cap = intensity_cap if intensity_cap and 1 <= intensity_cap <= 5 else None
    db.add_limit(t.user.id, body_area.strip().lower(), kind, list(dict.fromkeys(avoid)), cap, start, end)
    goal = db.current_goal(t.user.id)
    if goal and goal["status"] == "setting_up":
        db.set_goal_answer(goal["id"], "limits", "saved", "user")
    changes = []
    found = service.active(t.user)
    if found:
        goal, template, days = found
        changes = service.apply_limits(t.user, goal, template, days, t.day, t.now, t.use_fixtures)
    return _sent({"saved": True, "plan_days_changed": len(changes)},
                 messages.limit_saved(body_area.strip().lower(), avoid, cap, end))


@tool(response_format="content_and_artifact")
def end_limit(body_area: str, config: RunnableConfig) -> tuple[str, dict]:
    """They say an injury or limit is better ("my knee is fine now"): the limit for that body area ends."""
    t = turn(config)
    matching = [x for x in db.active_limits(t.user.id) if body_area.strip().lower() in x["body_area"]]
    if not matching:
        return as_json({"ended": False, "reason": "no saved limit for that"}), {}
    db.end_limits(t.user.id, [x["id"] for x in matching])
    return _sent({"ended": True}, messages.limit_ended(matching[0]["body_area"]))


@tool(response_format="content_and_artifact")
def choose_plan_option(option: Literal["later_date", "skip_lighter"], messages_: Messages,
                       config: RunnableConfig) -> tuple[str, dict]:
    """Their pick after a break made the plan not fit ("move the date" = later_date, "keep the date" =
    skip_lighter)."""
    t = turn(config)
    if _asked_this_turn(messages_, {"goal_status"}):
        return as_json({"changed": False, "reason": "the question was asked in this same turn: no answer yet"}), {}
    found = service.active(t.user)
    if not found or not found[0].get("options_json"):
        return as_json({"changed": False, "reason": "no choice is waiting"}), {}
    chosen = next((o for o in found[0]["options_json"] if o["kind"] == option), None)
    if chosen is None:
        return as_json({"changed": False, "reason": "that option isn't one of the choices"}), {}
    return _sent({"changed": True}, service.rebuild(t.user, found[0], chosen, t.day, t.now, t.use_fixtures))


@tool(response_format="content_and_artifact")
def set_direction_goal(text: str, messages_: Messages, config: RunnableConfig) -> tuple[str, dict]:
    """Their long-term direction for training, in their words ("I want to be a runner who also lifts"): code
    turns it into a few rules they can confirm. Not for a question about how it's going (direction_check)."""
    t = turn(config)
    current = db.current_direction(t.user.id)
    last = next((str(m.content) for m in reversed(messages_) if isinstance(m, HumanMessage)), "")
    if current and current["confirmed"] and last.strip().endswith("?"):
        return as_json({"saved": False, "reason": "their message asks a question and their direction rules are "
                                                  "already confirmed; nothing was replaced"}), {}
    rules = direction.propose(text, t.user.id)
    db.add_direction(t.user.id, text, {r["key"]: r["value"] for r in rules})
    return _sent({"proposed": len(rules)}, messages.direction_proposed(rules, direction.catalog()))


@tool(response_format="content_and_artifact")
def confirm_direction_rules(choice: Literal["yes", "change"], messages_: Messages, config: RunnableConfig,
                            strength_sessions_per_week: float | None = None, min_strength_in_taper: float | None = None,
                            max_run_days_per_week: float | None = None,
                            protein_g_per_kg: float | None = None) -> tuple[str, dict]:
    """Their answer to the proposed direction rules: "yes" keeps them; "change" with the new values they gave."""
    t = turn(config)
    if _asked_this_turn(messages_, {"set_direction_goal"}):
        return as_json({"saved": False, "reason": "the question was asked in this same turn: no answer yet"}), {}
    current = db.current_direction(t.user.id)
    if not current or current["confirmed"]:
        return as_json({"saved": False, "reason": "no rules are waiting"}), {}
    rules = dict(current["rules"])
    if choice == "change":
        given = {"strength_sessions_per_week": strength_sessions_per_week, "min_strength_in_taper": min_strength_in_taper,
                 "max_run_days_per_week": max_run_days_per_week, "protein_g_per_kg": protein_g_per_kg}
        rules.update({k: direction.clamp(k, v) for k, v in given.items() if v is not None})
    db.confirm_direction(current["id"], rules)
    return _sent({"saved": True, "rules": rules}, messages.direction_confirmed(rules, direction.catalog()))


@tool
def direction_check(config: RunnableConfig) -> str:
    """How they're doing against their confirmed direction rules over the last 4 weeks (facts for your answer to
    "am I training like a runner who lifts?"). Lifts, muscle and body composition aren't measured."""
    t = turn(config)
    current = db.current_direction(t.user.id)
    if not current or not current["confirmed"]:
        return as_json({"direction": None, "status": "no confirmed direction rules"})
    start = t.day - timedelta(days=28)
    sig = service.signals_for(t.user, t.use_fixtures, start, t.day, t.now)
    return as_json({"direction": current["text"],
                    "milestones": direction.milestones(current["rules"], sig, t.day, None),
                    "not_measured": "lift weights, muscle, body composition (no lift log or measurements)"})


TOOLS = [create_goal, answer_goal, confirm_goal, goal_today, goal_status, save_limit, end_limit, choose_plan_option,
         set_direction_goal, confirm_direction_rules, direction_check]
