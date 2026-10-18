"""Training tools for the agent: check-in, plan, consult answer, done, manual workouts, explain.

The check-in is database state (`plans.status = awaiting_checkin`), not a paused graph: start_checkin
asks the question, submit_checkin runs the planner once the answer is known. A request the rules would
change gets one question first (`awaiting_confirm`, answered with confirm_override; unanswered for
2 hours it expires and nothing is planned). Plan messages and questions are built by code and sent as
they are (artifact {"verbatim": text}).
"""
from datetime import timedelta
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from connectors.health import Health
from connectors.manual import ManualSource
from core import db
from core.config import CONSULT_TTL_MIN
from core.labels import clock, clock_range, workout_label
from core.timeparse import parse_clock_time
from core.tools import Messages, as_json, turn
from langchain_core.messages import HumanMessage, ToolMessage
from modules.training import checkin, planner
from modules.training.blocks import blocks_of, default_minutes
from modules.training.messages import format_plan, format_why, readiness_line
from modules.training.schemas import DoneType
import today

PLANNER = planner.build_graph()   # no checkpointer: a planner run never pauses
CONSULT_TTL = timedelta(minutes=CONSULT_TTL_MIN)
SKIPPED = {"energy": None, "soreness": None, "note": None, "constraints": [], "label": "skipped"}
NO_OURA_MATCH = "Oura doesn't have a matching workout. Want me to add it with a time?"


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
        return ("Question sent to them as is: the rules would change what they asked for. "
                "Wait for yes or no (confirm_override)."), {"verbatim": state["consult"]}
    final = state["final_plan"]
    summary = (f"Plan sent to them as is: {final['workout_type']}, {final['duration_min']} min, {final['time_slot']}. "
               "Do not repeat it.")
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


def _asked_this_turn(messages: list) -> bool:
    """The question was sent in reply to their latest message: they haven't answered it yet."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return any(isinstance(m, ToolMessage) and m.name in ("submit_checkin", "plan_workout")
               for m in messages[last_human + 1:])


@tool(response_format="content_and_artifact")
def confirm_override(choice: Literal["yes", "no"], messages: Messages, config: RunnableConfig) -> tuple[str, dict]:
    """Their answer to "…I'd suggest X instead. Still want Y?": "yes" = plan what they asked for anyway,
    "no" = plan the suggestion (also when they pick the suggestion, e.g. "ok, the walk")."""
    t = turn(config)
    if _asked_this_turn(messages):
        return as_json({"planned": False, "reason": "They haven't answered the question yet. Stop and wait."}), {}
    expire_consults(t)
    pending = db.pending_confirm(t.user.id)
    if pending is None:
        if db.expired_consult(t.user.id, t.day):
            return ("Told them the question expired. Do not repeat it.",
                    {"verbatim": 'That question expired, so nothing was planned. Say "want to work out" '
                                 "to plan one now."})
        return as_json({"planned": False, "reason": "No question is waiting. Ask what they want."}), {}
    return _plan_reply(planner.answer_consult(PLANNER, t.user, t.now, t.use_fixtures, pending, choice))


@tool(response_format="content_and_artifact")
def mark_done(feeling: str | None, config: RunnableConfig) -> tuple[str, dict]:
    """They finished today's planned workout ("done", "did the run"). feeling: how it felt, or null.
    Only Oura's record counts here: fetches Oura again and marks what it recorded, with Oura's times.
    The confirmation (or "Oura doesn't have a matching workout…") is sent to them as is."""
    t = turn(config)
    _sync(t, refresh=True)
    plan = next((p for p in reversed(db.plans_between(t.user.id, t.day, t.day))
                 if (p["plan_json"] or {}).get("workout_type") != "rest"), None)
    if plan is None:
        return as_json({"done": False, "reason": "No workout planned today. Ask if they want it added as a workout "
                                                 "(type, minutes, when) with add_manual_workout."}), {}
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
        said.append(NO_OURA_MATCH if len(labels) == 1 else
                    f"Oura doesn't have a matching workout for the {' and '.join(missing).lower()}. "
                    "Want me to add it with a time?")
    if result["done"] and not missing and not feeling:
        said.append("How did it feel?")
    result["say"] = " ".join(said)
    result["note"] = ("The say text was sent to them as is: do not repeat it. Reply only if they asked something "
                      "else in the same message (answer that alone), otherwise reply with nothing.")
    return as_json(result), {"verbatim": result["say"]}


@tool
def add_manual_workout(workout_type: DoneType, minutes: int | None, start_time_text: str | None,
                       feeling: str | None, config: RunnableConfig) -> str:
    """Add exercise Oura did not record ("did a 30 min walk at 6", or the time they give after
    "Oura doesn't have a matching workout"). Exercise only. If today's plan has a part of this type that
    isn't done, it marks that part done. minutes: as they said, or null (a usual length for the type is
    assumed). start_time_text: when it started, exactly as written, or null (it just ended)."""
    t = turn(config)
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
    return "Explanation sent to them as is. Do not repeat it.", {"verbatim": format_why(db.latest_planned(t.user.id, t.day))}


TOOLS = [start_checkin, submit_checkin, plan_workout, confirm_override, mark_done, add_manual_workout, explain_plan]
