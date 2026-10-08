"""The coach graph, one thread per check-in:

    fetch_data -> compute_baseline -> checkin (interrupt) -> gather_today -> plan -> policy_check -> log

Each node returns a partial state update. Math, times and rules are deterministic code;
the LLM is used only in `plan`. `now` is always passed in (input or resume value), never
read inside a node, so a run is reproducible.
"""
import json
from datetime import date, datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import db
from baseline import compute_baseline
from config import TZ
from llm import load_prompt, structured_call
from policy import policy_check
from schemas import Plan
from today import day_context, fetch_oura, fetch_schedule, fixtures_now, load_goals

CHECKIN_QUESTION = (
    "Check-in: how's your energy (1-5) and soreness (1-5)? Anything else I should know?\n"
    "Reply like: 4 2 slept badly   (or 'skip')"
)


class CoachState(TypedDict, total=False):
    # input
    use_fixtures: bool
    thread_id: str
    now: str                      # ISO local time the plan is for (updated by the check-in answer)
    checkin: dict | None          # {"energy", "soreness", "note", "label"}; preset = no interrupt
    hide_history_from_llm: bool   # demo: LLM does not see earlier activity, so policy_check must catch it
    # fetch_data
    session_id: int
    day: str                      # YYYY-MM-DD
    oura: dict                    # today / history / body / workouts_by_day (today.fetch_oura)
    schedule: dict                # busy / all_day / free (times as "HH:MM") / source
    goals: dict
    # compute_baseline
    baseline: dict
    # gather_today
    context: dict                 # today.day_context: window, activity, food, targets, notes
    # plan
    llm_plan: dict
    # policy_check
    final_plan: dict
    overrides: list[str]


def new_thread_id(now: datetime) -> str:
    """checkin-YYYY-MM-DD-HHMM, with -2, -3... if that minute already has a check-in."""
    base = f"checkin-{now:%Y-%m-%d-%H%M}"
    thread_id, n = base, 1
    while db.thread_exists(thread_id):
        n += 1
        thread_id = f"{base}-{n}"
    return thread_id


# --- nodes ------------------------------------------------------------------

def fetch_data(state: CoachState) -> dict:
    fixtures = state.get("use_fixtures", False)
    now = datetime.fromisoformat(state["now"]) if state.get("now") else (
        fixtures_now() if fixtures else datetime.now(TZ))
    day = now.date()
    thread_id = state.get("thread_id") or new_thread_id(now)
    session_id = db.start_session(day, thread_id, now)  # abandons any older pending check-in
    return {
        "now": now.isoformat(timespec="minutes"),
        "thread_id": thread_id,
        "session_id": session_id,
        "day": day.isoformat(),
        "oura": fetch_oura(day, fixtures),
        "schedule": fetch_schedule(day, fixtures),
        "goals": load_goals(),
    }


def compute_baseline_node(state: CoachState) -> dict:
    oura = state["oura"]
    return {"baseline": compute_baseline(oura["today"], oura["history"],
                                         state["goals"]["schedule"]["readiness_floor"])}


def checkin(state: CoachState) -> dict:
    """Ask energy/soreness unless the caller already supplied them. No side effects (re-runs on resume)."""
    if state.get("checkin") is not None:
        return {}
    band = state["baseline"]["band"]
    readiness = state["oura"]["today"]["readiness_score"]
    answer = interrupt({
        "question": CHECKIN_QUESTION,
        "summary": f"Readiness {readiness if readiness is not None else 'n/a'}, band {band['band'].upper()}.",
    })
    return {
        "checkin": {"energy": answer.get("energy"), "soreness": answer.get("soreness"),
                    "note": answer.get("note"), "label": answer.get("label", "answered now")},
        "now": answer.get("now") or state["now"],
    }


def gather_today(state: CoachState) -> dict:
    now = datetime.fromisoformat(state["now"])
    context = day_context(date.fromisoformat(state["day"]), now, state["oura"], state["schedule"],
                          state["baseline"]["band"]["band"], state["goals"])
    return {"context": context}


def plan(state: CoachState) -> dict:
    day, c = date.fromisoformat(state["day"]), state["context"]
    context = {
        "date": f"{state['day']} ({day.strftime('%A')})",
        "now": c["now"],
        "window": c["window"],
        "band": state["baseline"]["band"],
        "baseline": state["baseline"]["comparison"],
        "today": {k: v for k, v in state["oura"]["today"].items() if k not in ("day", "workouts")},
        "body": state["oura"]["body"],
        "schedule": {"busy": state["schedule"]["busy"], "all_day": state["schedule"]["all_day"]},
        "goals": state["goals"],
        "context_notes": c["context_notes"],
        "checkin": state["checkin"],
        "activity_today": c["activity_today"],
        "worked_out_today": c["worked_out_today"],
        "oura_daily_activity": c["oura_activity_today"],
        "food": {
            "first_plan_today": c["first_plan_today"],
            "eaten_today": c["food_today"],
            "totals": c["food_totals"],
            "targets": c["targets"],
            "remaining_today": c["remaining"],
        },
    }
    if not state.get("hide_history_from_llm"):
        context["activity_yesterday"] = c["activity_yesterday"]
        context["yesterday_hard"] = c["yesterday_hard"]
        context["hard_days_prev6"] = c["hard_days_prev6"]
    user = json.dumps(context, indent=2, default=str)  # default=str: goals.yaml dates
    result = structured_call("plan", Plan, load_prompt("plan"), user)
    return {"llm_plan": result.model_dump()}


def policy_check_node(state: CoachState) -> dict:
    c, rules = state["context"], state["goals"]["schedule"]
    final, overrides = policy_check(state["llm_plan"], {
        "day": date.fromisoformat(state["day"]),
        "readiness": state["oura"]["today"]["readiness_score"],
        "readiness_floor": rules["readiness_floor"],
        "band": state["baseline"]["band"]["band"],
        "worked_out_today": c["worked_out_today"],
        "yesterday_hard": c["yesterday_hard"],
        "hard_days_prev6": c["hard_days_prev6"],
        "max_hard_days_per_week": rules["max_hard_days_per_week"],
        "recent_food": c["recent_food"],
        "recent_food_caps": state["goals"]["recent_food_caps"],
        "window": c["window"],
    })
    if not c["first_plan_today"]:
        final["meal_ideas"] = []
    return {"final_plan": final, "overrides": overrides}


def log(state: CoachState) -> dict:
    db.finish_session(state["session_id"], state["checkin"], state["final_plan"], state["llm_plan"],
                      state["overrides"])
    db.upsert_daily_log(
        date.fromisoformat(state["day"]),
        metrics_json={"today": state["oura"]["today"], "baseline": state["baseline"]},
        food_targets_json=state["context"]["targets"],
    )
    return {}


def build_graph(checkpointer):
    graph = StateGraph(CoachState)
    graph.add_node("fetch_data", fetch_data)
    graph.add_node("compute_baseline", compute_baseline_node)
    graph.add_node("checkin", checkin)
    graph.add_node("gather_today", gather_today)
    graph.add_node("plan", plan)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("log", log)
    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "compute_baseline")
    graph.add_edge("compute_baseline", "checkin")
    graph.add_edge("checkin", "gather_today")
    graph.add_edge("gather_today", "plan")
    graph.add_edge("plan", "policy_check")
    graph.add_edge("policy_check", "log")
    graph.add_edge("log", END)
    return graph.compile(checkpointer=checkpointer)


# --- helpers for callers (chat.py, run.py) -----------------------------------

def start_run(app, now: datetime, use_fixtures: bool, checkin: dict | None = None,
              hide_history_from_llm: bool = False) -> dict:
    """Start a check-in. Returns {"thread_id", "question"} if it waits for an answer, else the final state."""
    thread_id = new_thread_id(now)
    state = app.invoke(
        {"use_fixtures": use_fixtures, "thread_id": thread_id, "now": now.isoformat(timespec="minutes"),
         "checkin": checkin, "hide_history_from_llm": hide_history_from_llm},
        config={"configurable": {"thread_id": thread_id}},
    )
    if "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        return {"thread_id": thread_id, "question": payload["question"], "summary": payload["summary"]}
    return state


def resume_run(app, thread_id: str, answer: dict, now: datetime) -> dict:
    """Answer a waiting check-in; returns the final state."""
    return app.invoke(Command(resume={**answer, "now": now.isoformat(timespec="minutes")}),
                      config={"configurable": {"thread_id": thread_id}})
