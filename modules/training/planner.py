"""The training planner graph: one run turns a known check-in into a workout for right now.

    fetch_data -> compute_baseline -> gather_today -> rest_plan | consult | plan
    consult -> END (a question instead of a plan) | plan
    plan | rest_plan -> policy_check -> log

Conditional edges:
- `rest_plan` (code, no LLM call) when it's late (after the day's score cutoff, or before the morning)
  or there is no free time left today.
- `consult` (code, no LLM call) when they asked for a specific workout: if the rules would change it,
  the run stops with one question ("…I'd suggest a 30-min walk instead. Still want HIIT?") and the plan row
  waits as `awaiting_confirm`. Their answer reruns the planner with `decision`: "yes" plans the request
  with only the clock enforced (each skipped rule recorded as `user_choice`); "no" takes the suggestion
  exactly as offered (`suggested_plan`, code, no LLM call), moved to start now.
  There is never a second question for the same request.
There is no interrupt: the check-in question and its answer live in the `plans` table and the
agent's chat session (core/agent.py), and the planner only runs once the answer is known.

Each node returns a partial state update. Math, times and rules are deterministic code;
the LLM is used only in `plan`. `now` is always passed in, never read inside a node,
so a run is reproducible.
"""
import json
from datetime import date, datetime, time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from core import db, users
from core.config import DAY_START_HOUR
from core.day import day_of
from core.labels import blocks_phrase
from core.llm import load_prompt, structured_call
from modules.nutrition.targets import fallback_before, fallback_meal
from modules.training.baseline import compute_baseline, effective_band
from modules.training.blocks import BLOCK_KEYS, blocks_of, pin_to_request, requested_blocks, types_of, with_blocks
from modules.training.messages import format_consult
from modules.training.policy import body_could_do_more, conflicts, policy_check, why_sentence
from modules.training.schemas import Plan
from today import day_context, fetch_oura, fetch_schedule, fixtures_now, sync_oura_completions

CHECKIN_QUESTION = (
    "How's your energy (1-5) and soreness (0-5)? Anything else I should know?\n"
    "Reply like: 4 2 slept badly   (or skip)"
)
REST_QUESTION = "Want a 10-minute stretch you can do anytime?"
LATE_WHY = "It's late, so no workout now."
LATE_FOLLOW_UP = "Get some sleep and check in again in the morning."
EXERCISE_COUNT = range(5, 7)   # a strength plan lists 5-6 exercises
# The fields of a plan built by code (a request, a rest day) before the coach or the rules fill them in.
CODE_PLAN = {"why": "", "reasoning": "", "food_before": "", "food_after": "", "follow_up": "", "exercises": [],
             "rules_applied": []}


class CoachState(TypedDict, total=False):
    # input
    user_id: int                  # whose plan (default: user 1, e.g. from Studio)
    use_fixtures: bool
    thread_id: str
    now: str                      # ISO local time the plan is for
    checkin: dict                 # {"energy", "soreness", "note", "constraints", "label"} (None = not answered)
    plan_id: int                  # the plans row to fill (an answered check-in); new row if missing
    request: list[dict] | None    # the workout they asked for: [{workout_type, duration_min | None}], in order
    decision: str | None          # their answer to the consult question: "yes" (the request) / "no" (the suggestion)
    suggestion: dict | None       # with decision "no": the code-built plan the question offered
    hide_history_from_llm: bool   # demo: LLM does not see earlier activity, so policy_check must catch it
    # fetch_data
    day: str                      # YYYY-MM-DD
    oura: dict                    # today / history / body / workouts_by_day (today.fetch_oura)
    schedule: dict                # busy / all_day / free (times as "HH:MM") / source
    goals: dict
    late: bool                    # after the day's cutoff or before its morning: rest, no plan
    # compute_baseline
    baseline: dict
    # gather_today
    band: dict                    # baseline.effective_band: the Oura band lowered by the check-in
    context: dict                 # today.day_context: window, activity, food, targets, notes, split day
    # consult
    consult: str                  # the question sent instead of a plan (the run stops there)
    # plan / rest_plan
    llm_plan: dict
    llm_types: list[str]          # the workout types the coach proposed (before pinning to the request)
    plan_source: str              # "coach" (LLM) or "code" (rest_plan, suggested_plan)
    # policy_check
    final_plan: dict
    overrides: list[dict]


def new_thread_id(now: datetime) -> str:
    """checkin-YYYY-MM-DD-HHMM, with -2, -3... if that minute already has a check-in."""
    base = f"checkin-{now:%Y-%m-%d-%H%M}"
    thread_id, n = base, 1
    while db.thread_exists(thread_id):
        n += 1
        thread_id = f"{base}-{n}"
    return thread_id


def is_late(user, day: date, now: datetime) -> bool:
    """After the day's score cutoff (21:00) or before its morning (06:00). `day` is day_of(user, now), the
    same day logic as food: a 00:30 check-in before that night's main sleep still belongs to yesterday,
    which is past its cutoff; after it, it is before today's morning."""
    return (now >= datetime.combine(day, user.score_cutoff, user.tz)
            or now < datetime.combine(day, time(DAY_START_HOUR), user.tz))


# --- nodes ------------------------------------------------------------------

def fetch_data(state: CoachState) -> dict:
    fixtures = state.get("use_fixtures", False)
    user = users.get(state.get("user_id") or users.DEFAULT_USER_ID)
    goals = user.goals()
    now = datetime.fromisoformat(state["now"]) if state.get("now") else (
        fixtures_now(tz=user.tz) if fixtures else datetime.now(user.tz))
    # Fetch first: it fills the Oura cache that day_of reads to find where today starts.
    oura = fetch_oura(user, now.date(), fixtures, now)
    day = day_of(user, now)
    if day != now.date():
        oura = fetch_oura(user, day, fixtures, now)
    # Before a new plan row replaces today's open plans: what Oura already recorded is done, not abandoned.
    sync_oura_completions(user, day, oura["workouts_by_day"], goals["activity"])
    late = is_late(user, day, now)
    thread_id = state.get("thread_id") or new_thread_id(now)
    # A late check-in only gets a rest note: the day's plans stay as they were (done or missed).
    plan_id = state.get("plan_id") or db.start_plan(user.id, day, thread_id, now, replace_open=not late)
    return {
        "user_id": user.id,
        "now": now.isoformat(timespec="minutes"),
        "thread_id": thread_id,
        "plan_id": plan_id,
        "day": day.isoformat(),
        "oura": oura,
        "schedule": fetch_schedule(user, day, fixtures),
        "goals": goals,
        "late": late,
    }


def compute_baseline_node(state: CoachState) -> dict:
    oura = state["oura"]
    return {"baseline": compute_baseline(oura["today"], oura["history"],
                                         state["goals"]["schedule"]["readiness_floor"])}


def gather_today(state: CoachState) -> dict:
    user, day = users.get(state["user_id"]), date.fromisoformat(state["day"])
    band = effective_band(state["baseline"]["band"], state["checkin"], state["goals"]["checkin"])
    now = datetime.fromisoformat(state["now"])
    context = day_context(user, day, now, state["oura"], state["schedule"], band["band"], state["goals"])
    return {"band": band, "context": context}


def _policy_ctx(state: CoachState) -> dict:
    c, rules = state["context"], state["goals"]["schedule"]
    return {
        "day": date.fromisoformat(state["day"]),
        "readiness": state["oura"]["today"]["readiness_score"],
        "readiness_floor": rules["readiness_floor"],
        "readiness_usual": state["baseline"]["comparison"]["readiness_score"]["avg_7d"],
        "band": state["band"],
        "worked_out_today": c["worked_out_today"],
        "yesterday_hard": c["yesterday_hard"],
        "hard_days_prev6": c["hard_days_prev6"],
        "max_hard_days_per_week": rules["max_hard_days_per_week"],
        "recent_food": c["recent_food"],
        "recent_food_caps": state["goals"]["recent_food_caps"],
        "window": c["window"],
    }


def consult(state: CoachState) -> dict:
    """Would the rules change what they asked for? Then ask once instead of planning (code only)."""
    window = state["context"]["window"]
    requested = with_blocks({**CODE_PLAN, "time_slot": f"{window[0]}-{window[0]}"},
                            requested_blocks(state["request"]))
    final, overrides = policy_check(requested, _policy_ctx(state))
    found = conflicts(requested, final, overrides)
    if not found:
        return {}
    suggestion = finalize(final, requested, overrides, {**state, "plan_source": "code"})
    suggestion["why"] = suggestion["why"] or found[0]["plain"]
    suggested_blocks = [{"workout_type": b["workout_type"], "duration_min": b["duration_min"]}
                        for b in blocks_of(final)]
    question = format_consult(state["request"], suggested_blocks, found)
    db.ask_confirm(state["plan_id"], state["checkin"], {
        "asked_at": state["now"],
        "question": question,
        "request": state["request"],
        "checkin": state["checkin"],
        "suggestion_blocks": suggested_blocks,
        "suggestion_plan": suggestion,
        "suggestion_overrides": overrides,
    })
    return {"consult": question}


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
        "saved_memory": c["saved_memory"],
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
            "recent_meal_ideas": c["recent_meal_ideas"],
        },
    }
    if state.get("request"):
        context["requested"] = {"blocks": state["request"],
                                "they_chose_it_over_the_rules": state.get("decision") == "yes"}
    if not state.get("hide_history_from_llm"):
        context["activity_yesterday"] = c["activity_yesterday"]
        context["yesterday_hard"] = c["yesterday_hard"]
        context["hard_days_prev6"] = c["hard_days_prev6"]
    user = json.dumps(context, indent=2, default=str)  # default=str: goals.yaml dates
    result = structured_call("plan", Plan, load_prompt("plan"), user, state["user_id"]).model_dump()
    if state.get("request"):
        llm_plan = pin_to_request(result, state["request"])     # what they asked for, in their order
    else:
        llm_plan = {**result, "blocks": []}                     # parts come only from what they asked for
    return {"llm_plan": llm_plan, "llm_types": types_of(result), "plan_source": "coach"}


def _meal(state: CoachState) -> str:
    """A code-picked meal for the diet and what's left today, not one of the recent ideas."""
    c = state["context"]
    return fallback_meal(c["remaining"], state["goals"]["nutrition"]["diet"], date.fromisoformat(state["day"]),
                         c["recent_meal_ideas"])


def suggested_plan(state: CoachState) -> dict:
    """They said no to the request: the suggestion the question offered, as it was (code, no LLM call).
    policy_check moves it to start now."""
    window = state["context"]["window"]
    suggestion = {**state["suggestion"], "time_slot": f"{window[0]}-{window[0]}"}
    return {"llm_plan": suggestion, "plan_source": "code"}


def rest_plan(state: CoachState) -> dict:
    """Late, or no free time left today: a rest plan built in code, without an LLM call."""
    c, request = state["context"], state.get("request")
    follow_up = REST_QUESTION
    if state.get("late"):
        cutoff = users.get(state["user_id"]).score_cutoff
        why, follow_up = LATE_WHY, LATE_FOLLOW_UP
        reasoning = (f"Check-in at {c['now']}: after the {cutoff:%H:%M} cutoff or before the morning, "
                     "so no plan was generated.")
    elif request:
        why = f"There's no free time left today for {blocks_phrase(request)}."
        reasoning = (f"They asked for {blocks_phrase(request)}, but there's no free time left today after "
                     f"{c['now']} (calendar and the 21:00 cutoff), so no plan was generated.")
    else:
        why = "There's no free time left today."
        reasoning = (f"No free time left today after {c['now']} (calendar and the 21:00 cutoff), "
                     "so no plan was generated.")
    return {
        "llm_plan": {**CODE_PLAN, "workout_type": "rest", "intensity": 1, "time_slot": "none", "duration_min": 0,
                     "why": why, "reasoning": reasoning, "food_after": _meal(state),
                     "follow_up": follow_up},
        "plan_source": "code",
    }


def policy_check_node(state: CoachState) -> dict:
    llm_plan = state["llm_plan"]
    final, overrides = policy_check(llm_plan, _policy_ctx(state), user_choice=state.get("decision") == "yes")
    return {"final_plan": finalize(final, llm_plan, overrides, state), "overrides": overrides}


def finalize(final: dict, llm_plan: dict, overrides: list[dict], state: CoachState) -> dict:
    """Make every chat-facing field match the final workout (code decides; the LLM's text is kept
    only where it still fits)."""
    c, final = state["context"], dict(final)
    body_ok = body_could_do_more(state["baseline"]["band"]["band"], state["oura"]["today"]["readiness_score"],
                                 state["goals"]["schedule"]["readiness_floor"])
    final["why"] = why_sentence(final, overrides, body_ok)
    if state.get("late") or (state.get("request") and not c["window"]):
        final["why"] = llm_plan["why"]          # rest_plan's own sentence says why better than "no free time"

    types = types_of(final)
    # Pinned to the request, but the coach proposed something else: its words were for that workout.
    coach_ignored_request = bool(state.get("llm_types")) and state["llm_types"] != types_of(llm_plan)
    if coach_ignored_request and final["why"] == llm_plan["why"]:
        verb = "chose" if state.get("decision") == "yes" else "asked for"
        final["why"] = f"You {verb} {blocks_phrase(state['request'])}."
    type_changed = types != types_of(llm_plan) or coach_ignored_request
    final["food_source"] = "code" if type_changed or state.get("plan_source") == "code" else "coach"
    if type_changed:
        final["food_before"] = fallback_before(final["workout_type"])
        final["food_after"] = _meal(state)
    if final["workout_type"] == "rest":
        final["food_before"] = ""
        if type_changed or not final["follow_up"]:
            final["follow_up"] = REST_QUESTION
    else:
        final["follow_up"] = ""

    has_strength = "strength" in types
    final["split_day"] = c["next_split_day"] if has_strength else None
    exercises = final["exercises"] if has_strength else []
    final["exercises_note"] = None
    if has_strength and len(exercises) not in EXERCISE_COUNT:
        final["exercises_note"] = f"The coach listed {len(exercises)} exercises (expected 5-6), so none are shown."
        exercises = []
    final["exercises"] = exercises
    return final


def _details(state: CoachState, llm_plan: dict, overrides: list[dict], plan_source: str) -> dict:
    """The numbers and reasons behind a plan, saved with it (explain_plan shows them)."""
    c = state["context"]
    return {
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
        "reasoning": llm_plan["reasoning"],
        "llm_plan": {k: llm_plan[k] for k in (*BLOCK_KEYS, "time_slot")} | {"blocks": blocks_of(llm_plan)},
        "plan_source": plan_source,
        "request": state.get("request"),
        "decision": state.get("decision"),
        "late": state.get("late", False),
        "overrides": overrides,
    }


def log(state: CoachState) -> dict:
    c = state["context"]
    details = _details(state, state["llm_plan"], state["overrides"], state.get("plan_source", "coach"))
    db.finish_plan(state["plan_id"], state["checkin"], state["final_plan"], state["llm_plan"],
                   state["overrides"], details)
    db.upsert_daily_log(
        state["user_id"],
        date.fromisoformat(state["day"]),
        metrics_json={"today": state["oura"]["today"], "baseline": state["baseline"]},
        food_targets_json=c["targets"],
    )
    return {}


def after_gather(state: CoachState) -> str:
    if state["late"] or not state["context"]["window"]:
        return "late or no time left"
    if state.get("suggestion"):
        return "said no"
    if state.get("request") and not state.get("decision"):
        return "asked for a workout"
    return "time left"


def after_consult(state: CoachState) -> str:
    return "ask first" if state.get("consult") else "no conflict"


def build_graph(checkpointer=None):
    graph = StateGraph(CoachState)
    graph.add_node("fetch_data", fetch_data)
    graph.add_node("compute_baseline", compute_baseline_node)
    graph.add_node("gather_today", gather_today)
    graph.add_node("consult", consult)
    graph.add_node("plan", plan)
    graph.add_node("rest_plan", rest_plan)
    graph.add_node("suggested_plan", suggested_plan)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("log", log)
    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "compute_baseline")
    graph.add_edge("compute_baseline", "gather_today")
    graph.add_conditional_edges("gather_today", after_gather, {
        "late or no time left": "rest_plan", "said no": "suggested_plan",
        "asked for a workout": "consult", "time left": "plan"})
    graph.add_conditional_edges("consult", after_consult, {"ask first": END, "no conflict": "plan"})
    graph.add_edge("plan", "policy_check")
    graph.add_edge("rest_plan", "policy_check")
    graph.add_edge("suggested_plan", "policy_check")
    graph.add_edge("policy_check", "log")
    graph.add_edge("log", END)
    return graph.compile(checkpointer=checkpointer)


# --- helper for callers (core/agent.py, run.py) ------------------------------

def run_plan(app, user, now: datetime, use_fixtures: bool, checkin: dict, plan_id: int | None = None,
             hide_history_from_llm: bool = False, request: list[dict] | None = None,
             decision: str | None = None, suggestion: dict | None = None) -> dict:
    """Plan a workout for `now` from a known check-in; returns the final state. If they asked for a
    workout the rules would change, the state has `consult` (the question) and no plan yet."""
    return app.invoke({"user_id": user.id, "use_fixtures": use_fixtures, "thread_id": new_thread_id(now),
                       "now": now.isoformat(timespec="minutes"), "checkin": checkin, "plan_id": plan_id,
                       "request": request, "decision": decision, "suggestion": suggestion,
                       "hide_history_from_llm": hide_history_from_llm})


def answer_consult(app, user, now: datetime, use_fixtures: bool, pending: dict, choice: str,
                   hide_history_from_llm: bool = False) -> dict:
    """Plan from their yes or no to the waiting question (`pending` = its plans row)."""
    r = pending["requested_json"]
    if choice == "yes":
        return run_plan(app, user, now, use_fixtures, r["checkin"], pending["id"], hide_history_from_llm,
                        request=r["request"], decision="yes")
    return run_plan(app, user, now, use_fixtures, r["checkin"], pending["id"], hide_history_from_llm,
                    request=r["suggestion_blocks"], decision="no", suggestion=r["suggestion_plan"])
