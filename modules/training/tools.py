"""Training tools for the agent: check-in, plan, done, manual workouts, explain.

The check-in is database state (`plans.status = awaiting_checkin`), not a paused graph: start_checkin
asks the question, submit_checkin runs the planner once the answer is known. Plan messages and the
check-in question are built by code and sent as they are (artifact {"verbatim": text}).
"""
from datetime import timedelta

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from connectors.health import Health
from connectors.manual import ManualSource
from core import db
from core.labels import clock, clock_range, workout_label
from core.timeparse import parse_clock_time
from core.tools import as_json, turn
from modules.training import planner
from modules.training.activity import UNKNOWN_DURATION
from modules.training.messages import format_plan, format_why, readiness_line
from modules.training.schemas import DoneType
import today

PLANNER = planner.build_graph()   # no checkpointer: a planner run never pauses


def _readiness(t) -> int | None:
    try:
        return Health(t.use_fixtures).readiness(t.user, t.day)["score"]
    except Exception:     # no Oura data must never block a check-in
        return None


def _plan_reply(state: dict) -> tuple[str, dict]:
    final = state["final_plan"]
    summary = (f"Plan sent to them as is: {final['workout_type']}, {final['duration_min']} min, {final['time_slot']}. "
               "Do not repeat it.")
    return summary, {"verbatim": format_plan(state)}


@tool(response_format="content_and_artifact")
def start_checkin(config: RunnableConfig) -> tuple[str, dict]:
    """Start a workout check-in: asks energy (1-5) and soreness (0-5). Use when they want a workout now."""
    t = turn(config)
    db.start_plan(t.user.id, t.day, planner.new_thread_id(t.now), t.now, t.session_id)
    text = f"{readiness_line(_readiness(t))}\n{planner.CHECKIN_QUESTION}"
    return "Check-in question sent to them as is. Wait for their answer.", {"verbatim": text}


@tool(response_format="content_and_artifact")
def submit_checkin(energy: int | None, soreness: int | None, comment: str | None,
                   config: RunnableConfig) -> tuple[str, dict]:
    """Their answer to the check-in: energy 1-5, soreness 0-5 (null when skipped or not given), and
    anything else they said (comment). Plans a workout for right now."""
    t = turn(config)
    if energy is not None and not 1 <= energy <= 5 or soreness is not None and not 0 <= soreness <= 5:
        return as_json({"planned": False, "reason": "Energy must be 1-5 and soreness 0-5. Ask again."}), {}
    pending = db.pending_plan(t.user.id)
    plan_id = pending["id"] if pending and pending["date"] == t.day.isoformat() else None
    checkin = {"energy": energy, "soreness": soreness, "note": comment, "label": "just now"}
    return _plan_reply(planner.run_plan(PLANNER, t.user, t.now, t.use_fixtures, checkin, plan_id))


@tool(response_format="content_and_artifact")
def plan_workout(request: str | None, config: RunnableConfig) -> tuple[str, dict]:
    """Plan a workout for right now without asking the check-in questions again (reuses today's latest
    check-in, if any). request: what they asked for, e.g. "strength", or null."""
    t = turn(config)
    latest = db.latest_checkin(t.user.id, t.day)
    if latest:
        checkin = {"energy": latest["energy"], "soreness": latest["soreness"], "note": request or latest["note"],
                   "label": f"from your {clock(latest['checkin_at'][11:16])} check-in"}
    else:
        checkin = {"energy": None, "soreness": None, "note": request, "label": "skipped"}
    return _plan_reply(planner.run_plan(PLANNER, t.user, t.now, t.use_fixtures, checkin))


@tool
def mark_done(feeling: str | None, time_text: str | None, config: RunnableConfig) -> str:
    """They finished today's planned workout ("done", "did the run"). feeling: how it felt, or null.
    time_text: when they finished, exactly as written, or null (now)."""
    t = turn(config)
    goals = t.user.goals()
    oura = today.fetch_oura(t.user, t.day, t.use_fixtures, t.now)
    today.sync_oura_completions(t.user, t.day, oura["workouts_by_day"], goals["activity"])
    latest = db.latest_planned(t.user.id, t.day)
    if latest and latest["completed"] == 1 and latest["done_by"] == "oura":
        start, end = latest["actual_start"], latest["actual_end"]
        return as_json({"done": True, "from_oura": True, "workout": workout_label(latest["plan_json"]["workout_type"]),
                        "time": clock_range(start[11:16], end[11:16])})
    open_plan = db.latest_open_plan(t.user.id, t.day)
    if open_plan is None:
        return as_json({"done": False, "reason": "No open plan today. Ask if they want it added as a workout "
                                                 "(type, minutes, when) with add_manual_workout."})
    plan = open_plan["plan_json"] or {}
    end = parse_clock_time(time_text, t.now) or t.now
    minutes = plan.get("duration_min")
    start = end - (timedelta(minutes=minutes) if minutes else UNKNOWN_DURATION)
    db.mark_done(open_plan["id"], feeling, t.now, start, end)
    return as_json({"done": True, "from_oura": False, "workout": workout_label(plan.get("workout_type", "other"),
                                                                              plan.get("split_day")),
                    "time": clock_range(start, end), "feeling": feeling})


@tool
def add_manual_workout(workout_type: DoneType, minutes: int | None, start_time_text: str | None,
                       feeling: str | None, config: RunnableConfig) -> str:
    """Add exercise Oura did not record ("did a 30 min walk at 6"). Exercise only.
    start_time_text: when it started, exactly as written, or null (it just ended)."""
    t = turn(config)
    length = timedelta(minutes=minutes) if minutes else UNKNOWN_DURATION
    start = parse_clock_time(start_time_text, t.now)
    if start is None:
        end = t.now
        start = end - length
    else:
        end = start + length
    w = ManualSource().add_workout(t.user, workout_type, start, end, minutes, feeling)
    return as_json({"added": True, "id": w["id"], "workout": workout_label(workout_type),
                    "minutes": minutes, "time": clock_range(start, end)})


@tool(response_format="content_and_artifact")
def explain_plan(config: RunnableConfig) -> tuple[str, dict]:
    """The full reasoning, numbers and rule changes behind today's latest plan ("why?")."""
    t = turn(config)
    return "Explanation sent to them as is. Do not repeat it.", {"verbatim": format_why(db.latest_planned(t.user.id, t.day))}


TOOLS = [start_checkin, submit_checkin, plan_workout, mark_done, add_manual_workout, explain_plan]
