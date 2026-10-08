"""The coach graph, one thread per check-in:

    fetch_data -> compute_baseline -> [checkin (interrupt)] -> gather_today -> plan | rest_plan
               -> policy_check -> log

Two conditional edges: `checkin` is skipped when the check-in is already known (/plan, run.py),
and `rest_plan` (no LLM call) replaces `plan` when there is no free time left today.

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
from baseline import compute_baseline, effective_band
from config import TZ
from llm import load_prompt, structured_call
from nutrition import fallback_before, fallback_meal
from policy import body_could_do_more, policy_check, why_sentence
from schemas import Plan
from today import day_context, fetch_oura, fetch_schedule, fixtures_now, load_goals

CHECKIN_QUESTION = (
    "How's your energy (1-5) and soreness (1-5)? Anything else I should know?\n"
    "Reply like: 4 2 slept badly   (or skip)"
)
REST_QUESTION = "Want a 10-minute stretch you can do anytime?"
EXERCISE_COUNT = range(5, 7)   # a strength plan lists 5-6 exercises


class CoachState(TypedDict, total=False):
    # input
    use_fixtures: bool
    thread_id: str
    now: str                      # ISO local time the plan is for (updated by the check-in answer)
    checkin: dict | None          # {"energy", "soreness", "note", "label"}; preset = checkin node skipped
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
    band: dict                    # baseline.effective_band: the Oura band lowered by the check-in
    context: dict                 # today.day_context: window, activity, food, targets, notes, split day
    # plan / rest_plan
    llm_plan: dict
    plan_source: str              # "coach" (LLM) or "code" (rest_plan)
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
    """Ask energy/soreness. No side effects: LangGraph re-runs this node on resume."""
    readiness = state["oura"]["today"]["readiness_score"]
    answer = interrupt({
        "question": CHECKIN_QUESTION,
        "summary": f"🔋 Readiness {readiness}" if readiness is not None else "🔋 Readiness: no data yet",
    })
    return {
        "checkin": {"energy": answer.get("energy"), "soreness": answer.get("soreness"),
                    "note": answer.get("note"), "label": answer.get("label", "just now")},
        "now": answer.get("now") or state["now"],
    }


def gather_today(state: CoachState) -> dict:
    band = effective_band(state["baseline"]["band"], state["checkin"], state["goals"]["checkin"])
    now = datetime.fromisoformat(state["now"])
    context = day_context(date.fromisoformat(state["day"]), now, state["oura"], state["schedule"],
                          band["band"], state["goals"])
    return {"band": band, "context": context}


def plan(state: CoachState) -> dict:
    day, c, band = date.fromisoformat(state["day"]), state["context"], state["band"]
    context = {
        "date": f"{state['day']} ({day.strftime('%A')})",
        "now": c["now"],
        "free_until": c["window"],
        "how_hard_today": {"level": band["band"], "why": band["plain"]},
        "baseline": state["baseline"]["comparison"],
        "today": {k: v for k, v in state["oura"]["today"].items() if k not in ("day", "workouts")},
        "body": state["oura"]["body"],
        "schedule": {"busy": state["schedule"]["busy"], "all_day": state["schedule"]["all_day"]},
        "goals": state["goals"],
        "context_notes": c["context_notes"],
        "checkin": {k: v for k, v in state["checkin"].items() if k != "label"},
        "activity_today": c["activity_today"],
        "worked_out_today": c["worked_out_today"],
        "oura_daily_activity": c["oura_activity_today"],
        "strength_split_day": c["next_split_day"],
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
    return {"llm_plan": result.model_dump(), "plan_source": "coach"}


def rest_plan(state: CoachState) -> dict:
    """No free time left today: a rest plan built in code, without an LLM call."""
    c = state["context"]
    return {
        "llm_plan": {
            "workout_type": "rest", "intensity": 1, "time_slot": "none", "duration_min": 0,
            "why": "There's no free time left today.",
            "reasoning": f"No free time left today after {c['now']} (calendar and the 21:00 cutoff), "
                         "so no plan was generated.",
            "food_before": "", "food_after": fallback_meal(c["remaining"]), "follow_up": REST_QUESTION,
            "exercises": [], "rules_applied": [],
        },
        "plan_source": "code",
    }


def policy_check_node(state: CoachState) -> dict:
    c, rules, llm_plan = state["context"], state["goals"]["schedule"], state["llm_plan"]
    readiness = state["oura"]["today"]["readiness_score"]
    final, overrides = policy_check(llm_plan, {
        "day": date.fromisoformat(state["day"]),
        "readiness": readiness,
        "readiness_floor": rules["readiness_floor"],
        "band": state["band"],
        "worked_out_today": c["worked_out_today"],
        "yesterday_hard": c["yesterday_hard"],
        "hard_days_prev6": c["hard_days_prev6"],
        "max_hard_days_per_week": rules["max_hard_days_per_week"],
        "recent_food": c["recent_food"],
        "recent_food_caps": state["goals"]["recent_food_caps"],
        "window": c["window"],
    })
    return {"final_plan": finalize(final, llm_plan, overrides, state), "overrides": overrides}


def finalize(final: dict, llm_plan: dict, overrides: list[dict], state: CoachState) -> dict:
    """Make every chat-facing field match the final workout (code decides; the LLM's text is kept
    only where it still fits)."""
    c, final = state["context"], dict(final)
    body_ok = body_could_do_more(state["baseline"]["band"]["band"], state["oura"]["today"]["readiness_score"],
                                 state["goals"]["schedule"]["readiness_floor"])
    final["why"] = why_sentence(final, overrides, body_ok)

    type_changed = final["workout_type"] != llm_plan["workout_type"]
    final["food_source"] = "code" if type_changed or state.get("plan_source") == "code" else "coach"
    if type_changed:
        final["food_before"] = fallback_before(final["workout_type"])
        final["food_after"] = fallback_meal(c["remaining"])
    if final["workout_type"] == "rest":
        final["food_before"] = ""
        if type_changed or not final["follow_up"]:
            final["follow_up"] = REST_QUESTION
    else:
        final["follow_up"] = ""

    final["split_day"] = c["next_split_day"] if final["workout_type"] == "strength" else None
    exercises = final["exercises"] if final["workout_type"] == "strength" else []
    final["exercises_note"] = None
    if final["workout_type"] == "strength" and len(exercises) not in EXERCISE_COUNT:
        final["exercises_note"] = f"The coach listed {len(exercises)} exercises (expected 5-6), so none are shown."
        exercises = []
    final["exercises"] = exercises
    return final


def log(state: CoachState) -> dict:
    c = state["context"]
    details = {
        "now": c["now"],
        "checkin": state["checkin"],
        "comparison": state["baseline"]["comparison"],
        "oura_band": state["baseline"]["band"],
        "band": state["band"],
        "window": c["window"],
        "activity_today": c["activity_today"],
        "food_totals_today": c["food_totals"]["today"],
        "targets": c["targets"],
        "remaining": c["remaining"],
        "first_plan_today": c["first_plan_today"],
        "reasoning": state["llm_plan"]["reasoning"],
        "llm_plan": {k: state["llm_plan"][k] for k in ("workout_type", "intensity", "time_slot", "duration_min")},
        "plan_source": state.get("plan_source", "coach"),
        "overrides": state["overrides"],
    }
    db.finish_session(state["session_id"], state["checkin"], state["final_plan"], state["llm_plan"],
                      state["overrides"], details)
    db.upsert_daily_log(
        date.fromisoformat(state["day"]),
        metrics_json={"today": state["oura"]["today"], "baseline": state["baseline"]},
        food_targets_json=c["targets"],
    )
    return {}


def after_baseline(state: CoachState) -> str:
    return "check-in known" if state.get("checkin") is not None else "ask check-in"


def after_gather(state: CoachState) -> str:
    return "time left" if state["context"]["window"] else "no time left"


def build_graph(checkpointer):
    graph = StateGraph(CoachState)
    graph.add_node("fetch_data", fetch_data)
    graph.add_node("compute_baseline", compute_baseline_node)
    graph.add_node("checkin", checkin)
    graph.add_node("gather_today", gather_today)
    graph.add_node("plan", plan)
    graph.add_node("rest_plan", rest_plan)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("log", log)
    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "compute_baseline")
    graph.add_conditional_edges("compute_baseline", after_baseline,
                                {"ask check-in": "checkin", "check-in known": "gather_today"})
    graph.add_edge("checkin", "gather_today")
    graph.add_conditional_edges("gather_today", after_gather, {"time left": "plan", "no time left": "rest_plan"})
    graph.add_edge("plan", "policy_check")
    graph.add_edge("rest_plan", "policy_check")
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
