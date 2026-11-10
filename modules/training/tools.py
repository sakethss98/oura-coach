"""Training tools for the agent: check-in, plan, consult answer, done, manual workouts, explain.

The check-in is database state (`plans.status = awaiting_checkin`), not a paused graph: start_checkin
asks the question, submit_checkin runs the planner once the answer is known. A request the rules would
change gets one question first (`awaiting_confirm`, answered with confirm_override; unanswered for
2 hours it expires and nothing is planned). Plan messages and questions are built by code and sent as
they are (artifact {"verbatim": text}).
"""
from datetime import datetime, timedelta
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from connectors.health import Health
from connectors.manual import ManualSource
from core import db
from core.config import CONSULT_TTL_MIN
from core.labels import clock, clock_range, oura_label, workout_label, workout_phrase
from core.timeparse import parse_clock_time
from core.tools import GroupBy, Messages, as_json, range_for, range_label, turn, with_range_args
from langchain_core.messages import HumanMessage, ToolMessage
from modules.training import checkin, history, planner
from modules.training.activity import OURA_MATCHES, OURA_MATCH_SLACK
from modules.training.blocks import blocks_of, default_minutes
from modules.training.messages import format_plan, format_why, readiness_line
from modules.training.schemas import DoneType
import today

PLANNER = planner.build_graph()   # no checkpointer: a planner run never pauses
CONSULT_TTL = timedelta(minutes=CONSULT_TTL_MIN)
SKIPPED = {"energy": None, "soreness": None, "note": None, "constraints": [], "label": "skipped"}
RING_ONLY = "Oura only shares workouts the ring recorded (Apple Watch sessions don't come through)."
NO_OURA_MATCH = f"Oura doesn't have a matching workout. {RING_ONLY} Want me to add it with a time?"


def expire_consults(t) -> list[dict]:
    """Questions left unanswered for 2 hours expire: no plan, nothing sent, nothing scored."""
    return db.expire_consults(t.user.id, t.now, CONSULT_TTL)


def _sync(t, refresh: bool = False) -> dict:
    """Today's Oura bundle (fetched again with `refresh`); marks plan blocks Oura already recorded."""
    health = Health(t.use_fixtures)
    if refresh:
        health.refresh(t.user, t.day, t.now)
    bundle = health.bundle(t.user, t.day, now=t.now)
    today.sync_oura_completions(t.user, t.day, bundle["workouts_by_day"], t.user.goals()["activity"])
    return bundle


def _plan_reply(state: dict) -> tuple[str, dict]:
    if state.get("consult"):
        return ("Question sent to them as is: the rules would change what they asked for; their yes or no "
                "comes next."), {"verbatim": state["consult"]}
    final = state["final_plan"]
    summary = f"Plan sent to them as is: {final['workout_type']}, {final['duration_min']} min, {final['time_slot']}."
    return summary, {"verbatim": format_plan(state)}


@tool(response_format="content_and_artifact")
def start_checkin(request_text: str | None, config: RunnableConfig) -> tuple[str, dict]:
    """Start a workout check-in: asks energy (1-5) and soreness (0-5). Use when they want a workout now.
    request_text: the workout they asked for, exactly as written ("HIIT", "run + strength"), or null."""
    t = turn(config)
    expire_consults(t)
    try:
        readiness = _sync(t)["today"]["readiness_score"]
    except Exception:     # no Oura data must never block a check-in
        readiness = None
    if planner.is_late(t.user, t.day, t.now):     # too late to train: no question, straight to rest
        return _plan_reply(planner.run_plan(PLANNER, t.user, t.now, t.use_fixtures, SKIPPED))
    db.start_plan(t.user.id, t.day, planner.new_thread_id(t.now), t.now, t.session_id,
                  {"text": request_text} if request_text else None)
    text = f"{readiness_line(readiness)}\n{planner.CHECKIN_QUESTION}"
    return "Check-in question sent to them as is. Wait for their answer.", {"verbatim": text}


@tool(response_format="content_and_artifact")
def submit_checkin(answer: str, config: RunnableConfig) -> tuple[str, dict]:
    """Their answer to the check-in question, exactly as they wrote it ("4 2 slept badly", "skip",
    "5 0 give me run + strength"). Plans a workout for right now."""
    t = turn(config)
    expire_consults(t)
    pending = db.pending_plan(t.user.id)
    plan_id = pending["id"] if pending and pending["date"] == t.day.isoformat() else None
    request_text = ((pending["requested_json"] or {}).get("text") if plan_id else None)
    try:
        parsed = checkin.parse(answer, request_text, t.user.id)
    except checkin.CheckinError as e:
        return as_json({"planned": False, "reason": str(e)}), {}
    answered = parsed["energy"] is not None or parsed["soreness"] is not None
    answer_ = {k: parsed[k] for k in ("energy", "soreness", "note", "constraints")}
    answer_["label"] = "just now" if answered else "skipped"
    return _plan_reply(planner.run_plan(PLANNER, t.user, t.now, t.use_fixtures, answer_, plan_id,
                                        request=parsed["request"]))


@tool(response_format="content_and_artifact")
def plan_workout(request: str | None, config: RunnableConfig) -> tuple[str, dict]:
    """Plan a workout for right now without asking the check-in questions again (reuses today's latest
    check-in, if any). request: the workout they asked for, exactly as written ("strength"), or null."""
    t = turn(config)
    expire_consults(t)
    latest = db.latest_checkin(t.user.id, t.day)
    if latest:
        answer = {"energy": latest["energy"], "soreness": latest["soreness"], "note": request or latest["note"],
                  "constraints": [], "label": f"from your {clock(latest['checkin_at'][11:16])} check-in"}
    else:
        answer = {**SKIPPED, "note": request}
    blocks = checkin.parse("", request, t.user.id)["request"] if request else None
    return _plan_reply(planner.run_plan(PLANNER, t.user, t.now, t.use_fixtures, answer, request=blocks))


def _offered_to_add_this_turn(messages: list) -> bool:
    """mark_done found nothing in Oura in reply to their latest message, so it asked whether to add the workout:
    they haven't answered yet (adding it now would invent its time)."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return any(isinstance(m, ToolMessage) and m.name == "mark_done" and isinstance(m.artifact, dict)
               and m.artifact.get("offered_add") for m in messages[last_human + 1:])


def _asked_this_turn(messages: list) -> bool:
    """The question was sent in reply to their latest message: they haven't answered it yet."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return any(isinstance(m, ToolMessage) and m.name in ("submit_checkin", "plan_workout")
               for m in messages[last_human + 1:])


@tool(response_format="content_and_artifact")
def confirm_override(choice: Literal["yes", "no"], messages: Messages, config: RunnableConfig) -> tuple[str, dict]:
    """Their yes or no to the workout question ("I'd suggest a 30-min walk instead. Still want HIIT?"):
    "yes" = plan what they asked for anyway, "no" = plan the suggestion (also when they pick the suggestion,
    e.g. "ok, the walk"). Also for a yes/no to a question that expired (it tells them)."""
    t = turn(config)
    if _asked_this_turn(messages):
        return as_json({"planned": False, "reason": "the question was asked in this same turn: no answer yet"}), {}
    expire_consults(t)
    pending = db.pending_confirm(t.user.id)
    if pending is None:
        if db.expired_consult(t.user.id, t.day):
            return ("Sent to them as is: the question expired.",
                    {"verbatim": 'That question expired, so nothing was planned. Say "want to work out" '
                                 "to plan one now."})
        return as_json({"planned": False, "reason": "no question is waiting"}), {}
    return _plan_reply(planner.answer_consult(PLANNER, t.user, t.now, t.use_fixtures, pending, choice))


def _offer(workout_type: str | None, minutes: int | None, start) -> str:
    """"Want me to add 49 min of HIIT at 10:37am?" from what they said, or a plain "with a time?"."""
    if workout_type is None:
        return "Want me to add it with a time?"
    return f"Want me to add {workout_phrase(workout_type, minutes)}{f' at {clock(start)}' if start else ''}?"


def _oura_saw(t, workout_type: str | None, start) -> dict | None:
    """Today's Oura workout that matches what they said they did (type, and time when they gave one)."""
    allowed = OURA_MATCHES.get(workout_type) if workout_type else None
    for w in Health(t.use_fixtures).oura.workouts(t.user, t.day):
        begin = datetime.fromisoformat(w["start"])
        if allowed is not None and w["activity"] not in allowed:
            continue
        if start is None or abs(begin - start) <= OURA_MATCH_SLACK:
            return w
    return None


@tool(response_format="content_and_artifact")
def mark_done(feeling: str | None, config: RunnableConfig, workout_type: DoneType | None = None,
              minutes: int | None = None, start_time_text: str | None = None) -> tuple[str, dict]:
    """They did a workout: today's planned one ("done", "did the run") or another ("I did 49 min HIIT at 10:37").
    Only Oura's record counts here: fetches Oura again and marks what it recorded, with Oura's times; when Oura
    doesn't have it, it says why and offers to add it (then add_manual_workout on their yes). feeling: how it
    felt, or null. workout_type, minutes, start_time_text: what they said they did, exactly as said, or null.
    The text is sent to them as is."""
    t = turn(config)
    _sync(t, refresh=True)
    stated_start = parse_clock_time(start_time_text, t.now)
    plan = next((p for p in reversed(db.plans_between(t.user.id, t.day, t.day))
                 if (p["plan_json"] or {}).get("workout_type") != "rest"), None)
    if plan is None:
        seen = _oura_saw(t, workout_type, stated_start)
        if seen:
            say = f"Oura has it: {oura_label(seen['activity']).lower()} {clock_range(seen['start'][11:16], seen['end'][11:16])}."
        else:
            say = f"Oura doesn't have it. {RING_ONLY} {_offer(workout_type, minutes, stated_start)}"
        return (as_json({"done": [], "no_plan_today": True, "oura_has_it": seen is not None, "say": say,
                         "sent_as_is": True}),
                {"verbatim": say, "wrote": False, "offered_add": seen is None})
    if feeling:
        db.set_feeling(plan["id"], feeling)
    p, done = plan["plan_json"], plan["blocks_done"]
    labels = [workout_label(b["workout_type"], p.get("split_day")) for b in blocks_of(p)]
    if not done and plan["completed"] == 1:      # an older plan marked done as a whole
        done = {0: {"start": plan["actual_start"], "end": plan["actual_end"], "done_by": plan["done_by"]}}
    result = {
        "done": [{"workout": labels[i], "time": clock_range(d["start"][11:16], d["end"][11:16]),
                  "from_oura": d["done_by"] == "oura"} for i, d in sorted(done.items())],
        "feeling": feeling,
    }
    said = []
    if result["done"]:
        said.append("Marked done: " + ", ".join(f"{d['workout']} {d['time']}" + (" (from Oura)" if d["from_oura"] else "")
                                                for d in result["done"]) + ".")
    missing = [labels[i] for i in range(len(labels)) if i not in done]
    if missing:
        result["not_found"] = missing
        offer = _offer(workout_type, minutes, stated_start)
        said.append(f"Oura doesn't have a matching workout. {RING_ONLY} {offer}" if len(labels) == 1 else
                    f"Oura doesn't have a matching workout for the {' and '.join(missing).lower()}. {RING_ONLY} "
                    f"{offer}")
    if result["done"] and not missing and not feeling:
        said.append("How did it feel?")
    result["say"] = " ".join(said)
    result["sent_as_is"] = True
    return as_json(result), {"verbatim": result["say"], "wrote": bool(result["done"]) or bool(feeling),
                             "offered_add": bool(missing)}


@tool
def add_manual_workout(workout_type: DoneType, minutes: int | None, start_time_text: str | None,
                       feeling: str | None, messages: Messages, config: RunnableConfig) -> str:
    """Add exercise Oura did not record ("did a 30 min walk at 6", or the time they give after
    "Oura doesn't have a matching workout"). Exercise only. If today's plan has a part of this type that
    isn't done, it marks that part done. minutes: as they said, or null (a usual length for the type is
    assumed). start_time_text: when it started, exactly as written, or null (it just ended)."""
    t = turn(config)
    if _offered_to_add_this_turn(messages):
        return as_json({"added": False, "reason": "mark_done asked them in this same turn whether to add it: no "
                                                  "answer yet"})
    length = timedelta(minutes=minutes or default_minutes(workout_type))
    start = parse_clock_time(start_time_text, t.now)
    if start is None:
        end = t.now
        start = end - length
    else:
        end = start + length
    open_plan = db.latest_open_plan(t.user.id, t.day)
    if open_plan:
        blocks = blocks_of(open_plan["plan_json"])
        index = next((i for i, b in enumerate(blocks)
                      if b["workout_type"] == workout_type and i not in open_plan["blocks_done"]), None)
        if index is not None:
            db.mark_block_done(open_plan["id"], index, start, end, round(length.total_seconds() / 60), "chat",
                               t.now, feeling)
            return as_json({"added": True, "completed_planned": True, "workout": workout_label(workout_type),
                            "minutes": minutes, "time": clock_range(start, end)})
    w = ManualSource().add_workout(t.user, workout_type, start, end, minutes, feeling)
    return as_json({"added": True, "id": w["id"], "workout": workout_label(workout_type),
                    "minutes": minutes, "time": clock_range(start, end)})


@tool(response_format="content_and_artifact")
def explain_plan(config: RunnableConfig) -> tuple[str, dict]:
    """The full reasoning, numbers and rule changes behind today's latest plan ("why?")."""
    t = turn(config)
    return "Explanation sent to them as is.", {"verbatim": format_why(db.latest_planned(t.user.id, t.day))}


@tool
@with_range_args
def workouts(config: RunnableConfig, days_back: int | None = None, start_text: str | None = None,
             end_text: str | None = None, group_by: GroupBy = None) -> str:
    """Workouts over a range of days (Oura, added in chat, and planned ones done), with totals, run distance and
    the longest run ("what has my training looked like the last 2 weeks?"). group_by "weekday" answers "which
    days do I usually skip workouts?". At most the last 90 days."""
    t = turn(config)
    found = range_for(t, days_back, start_text, end_text)
    if isinstance(found, dict):
        return as_json(found)
    start, end, note = found
    health, rules = Health(t.use_fixtures), t.user.goals()["activity"]
    plans = history.plans_by_day(t.user, start, end)
    items = history.activity_by_day(t.user, start, end, health.history(t.user, start, end, t.now),
                                    health.manual_by_day(t.user, start, end), rules, plans)
    result = history.summarize(items, plans, rules, t.day, group_by)
    return as_json({"range": range_label(start, end), **({"coverage": note} if note else {}), **result})


TOOLS = [start_checkin, submit_checkin, plan_workout, confirm_override, mark_done, add_manual_workout, explain_plan, workouts]
