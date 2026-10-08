"""The M2 agent: fetch_data -> compute_baseline -> plan -> policy_check -> log.

Each node returns a partial state update. Math and time are deterministic code;
the LLM is used only in `plan`.
"""
import json
from datetime import date, datetime, timedelta
from typing import TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

import db
from baseline import compute_baseline
from calendar_client import fetch_calendar, fixture_calendar, schedule_for
from config import GOALS_PATH, TZ
from llm import load_prompt, structured_call
from oura_client import OuraClient, daily_metrics, latest_day, load_fixtures
from policy import policy_check
from schemas import Plan


class CoachState(TypedDict, total=False):
    # input
    use_fixtures: bool
    checkin: dict                 # {"energy": int | None, "soreness": int | None}
    hide_history_from_llm: bool   # demo: LLM does not see earlier plans, so policy_check must catch them
    # fetch_data
    day: str                      # YYYY-MM-DD
    today: dict                   # daily_metrics for today
    history: list[dict]           # daily_metrics for the 7 days before today
    schedule: dict                # busy / all_day / free (times as "HH:MM") / source
    goals: dict
    context_notes: list[str]
    food_today: list[dict]
    food_yesterday: list[dict]
    yesterday_plan: dict | None
    recent_plans: list[dict]      # plans from the 6 days before today
    # compute_baseline
    baseline: dict
    # plan
    llm_plan: dict
    # policy_check
    final_plan: dict
    overrides: list[str]


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _stored_plan(row) -> dict | None:
    """The final plan saved in a daily_log row, if there is one."""
    if row is None or not row["plan"]:
        return None
    try:
        plan = json.loads(row["plan"])
    except json.JSONDecodeError:
        return None
    return plan if isinstance(plan, dict) and "workout_type" in plan else None


# --- nodes ------------------------------------------------------------------

def fetch_data(state: CoachState) -> dict:
    if state["use_fixtures"]:
        raw = load_fixtures()
        day = latest_day(raw)
        calendar, calendar_source = fixture_calendar()
    else:
        day = datetime.now(TZ).date()
        raw = OuraClient().fetch_range(day - timedelta(days=7), day + timedelta(days=1))
        calendar, calendar_source = fetch_calendar(), "live"

    sched = schedule_for(calendar, day)
    schedule = {
        "busy": [[_hm(s), _hm(e), title] for s, e, title in sched["busy"]],
        "all_day": sched["all_day"],
        "free": [[_hm(s), _hm(e)] for s, e in sched["free"]],
        "source": calendar_source,
    }

    logs = {row["date"]: row for row in db.daily_logs_between(day - timedelta(days=6), day - timedelta(days=1))}
    yesterday = day - timedelta(days=1)
    recent = [_stored_plan(row) for row in logs.values()]

    return {
        "day": day.isoformat(),
        "today": daily_metrics(raw, day),
        "history": [daily_metrics(raw, day - timedelta(days=i)) for i in range(7, 0, -1)],
        "schedule": schedule,
        "goals": yaml.safe_load(GOALS_PATH.read_text()),
        "context_notes": db.active_context_notes(day),
        "food_today": db.food_entries(day),
        "food_yesterday": db.food_entries(yesterday),
        "yesterday_plan": _stored_plan(logs.get(yesterday.isoformat())),
        "recent_plans": [p for p in recent if p],
    }


def compute_baseline_node(state: CoachState) -> dict:
    baseline = compute_baseline(
        today=state["today"],
        history=state["history"],
        free_slots=state["schedule"]["free"],
        preferred_time=state["goals"]["schedule"]["preferred_time"],
        readiness_floor=state["goals"]["schedule"]["readiness_floor"],
        food_today=state["food_today"],
        food_yesterday=state["food_yesterday"],
    )
    return {"baseline": baseline}


def plan(state: CoachState) -> dict:
    day = date.fromisoformat(state["day"])
    context = {
        "date": f"{state['day']} ({day.strftime('%A')})",
        "baseline": state["baseline"]["comparison"],
        "band": state["baseline"]["band"],
        "today": {k: v for k, v in state["today"].items() if k != "day"},
        "schedule": {**state["schedule"], "recommended_slot": state["baseline"]["recommended_slot"]},
        "goals": state["goals"],
        "context_notes": state["context_notes"],
        "checkin": state["checkin"],
        "food": state["baseline"]["food"],
    }
    if not state.get("hide_history_from_llm"):
        context["yesterday_plan"] = state["yesterday_plan"]
        context["recent_plans"] = state["recent_plans"]
    user = json.dumps(context, indent=2, default=str)  # default=str: goals.yaml dates
    result = structured_call("plan", Plan, load_prompt("plan"), user)
    return {"llm_plan": result.model_dump()}


def policy_check_node(state: CoachState) -> dict:
    schedule_rules = state["goals"]["schedule"]
    final, overrides = policy_check(state["llm_plan"], {
        "day": date.fromisoformat(state["day"]),
        "readiness": state["today"]["readiness_score"],
        "readiness_floor": schedule_rules["readiness_floor"],
        "band": state["baseline"]["band"]["band"],
        "yesterday_plan": state["yesterday_plan"],
        "recent_plans": state["recent_plans"],
        "max_hard_days_per_week": schedule_rules["max_hard_days_per_week"],
        "free_slots": state["schedule"]["free"],
        "recommended_slot": state["baseline"]["recommended_slot"],
    })
    return {"final_plan": final, "overrides": overrides}


def log(state: CoachState) -> dict:
    db.upsert_daily_log(
        date.fromisoformat(state["day"]),
        metrics_json={"today": state["today"], "baseline": state["baseline"]},
        checkin_json=state["checkin"],
        plan=state["final_plan"],
        policy_json={
            "llm_plan": state["llm_plan"],
            "overrides": state["overrides"],
            "overridden": bool(state["overrides"]),
        },
    )
    return {}


def build_graph(checkpointer):
    graph = StateGraph(CoachState)
    graph.add_node("fetch_data", fetch_data)
    graph.add_node("compute_baseline", compute_baseline_node)
    graph.add_node("plan", plan)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("log", log)
    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "compute_baseline")
    graph.add_edge("compute_baseline", "plan")
    graph.add_edge("plan", "policy_check")
    graph.add_edge("policy_check", "log")
    graph.add_edge("log", END)
    return graph.compile(checkpointer=checkpointer)
