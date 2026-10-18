"""What the person reads about training: the plan message and the full "why". Plain words only
(labels.py), no internal terms. Code builds these; the agent sends them exactly as they are.
"""
from core.labels import PLAIN_METRIC, clock, clock_range, emoji, workout_label
from modules.nutrition.messages import kcal_protein

DONE_PROMPT = 'Reply "done" when you finish, or I\'ll mark it from Oura if it records it.'


def _num(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.0f}{unit}"


def readiness_line(readiness) -> str:
    return f"🔋 Readiness {readiness}" if readiness is not None else "🔋 Readiness: no data yet"


def _slot(time_slot: str) -> str:
    start, end = time_slot.split("-")
    return clock_range(start, end)



def format_activity(items: list[dict]) -> str:
    if not items:
        return "none"
    parts = []
    for i in items:
        when = clock_range(i["start"], i["end"]) if i["start"] and i["end"] else "time unknown"
        parts.append(f"{i['label']} {when} ({_num(i['minutes'], ' min')})"
                     + (", hard" if i["hard"] else "") + (f", felt {i['feeling']}" if i["feeling"] else ""))
    return "; ".join(parts)


def format_plan(state: dict) -> str:
    """The short plan message (workout or rest). Full detail is behind /why."""
    plan, c = state["final_plan"], state["context"]
    lines = [readiness_line(state["oura"]["today"]["readiness_score"])]
    rest = plan["workout_type"] == "rest"
    if rest:
        lines.append(f"{emoji('rest')} Rest")
    else:
        lines.append(f"{emoji(plan['workout_type'])} {workout_label(plan['workout_type'], plan.get('split_day'))} · "
                     f"{plan['duration_min']} min · {_slot(plan['time_slot'])}")
        lines += [f"{n}. {e['name']} · {e['sets_reps']}" for n, e in enumerate(plan.get("exercises") or [], 1)]
    targets = c["targets"]["targets"]
    if c["first_plan_today"] and targets:
        lines.append(f"Today: {kcal_protein(targets)}")
    lines += ["", f"Why: {plan['why']}", ""]
    if rest:
        if c["remaining"]:
            lines.append(f"🍽 Left today: {kcal_protein(c['remaining'])}")
            lines.append(plan["food_after"])
        else:
            lines.append(f"🍽 {plan['food_after']}")
        lines += ["", plan["follow_up"]]
    else:
        if plan["food_before"]:
            lines += [f"🍽 Before: {plan['food_before']}", f"After: {plan['food_after']}"]
        else:
            lines.append(f"🍽 After: {plan['food_after']}")
        lines += ["", DONE_PROMPT]
    return "\n".join(lines)


def _override_lines(overrides: list) -> list[str]:
    """Plain override sentences, once each (older sessions stored plain strings)."""
    seen = []
    for o in overrides or []:
        text = o["plain"] if isinstance(o, dict) else o
        if text not in seen:
            seen.append(text)
    return [f"- {t}" for t in seen]


def format_why(session: dict | None) -> str:
    """Everything behind the latest plan, in plain words."""
    if session is None:
        return "No plan yet today. Say \"want to work out\" to get one."
    plan, d = session["plan_json"] or {}, session["details_json"] or {}
    when = clock(session["checkin_at"][11:16]) if session["checkin_at"] else "earlier"
    if plan.get("workout_type") == "rest":
        headline = "Rest"
    else:
        headline = (f"{workout_label(plan['workout_type'], plan.get('split_day'))} · {plan['duration_min']} min · "
                    f"{_slot(plan['time_slot'])}")
    lines = [f"Your {when} plan: {headline}"]

    checkin = d.get("checkin") or {}
    if checkin.get("energy") is None and checkin.get("soreness") is None:
        lines.append("Check-in: skipped")
    else:
        note = f", \"{checkin['note']}\"" if checkin.get("note") else ""
        lines.append(f"Check-in ({checkin.get('label', 'just now')}): energy {_num(checkin.get('energy'))}, "
                     f"soreness {_num(checkin.get('soreness'))}{note}")
    if d.get("comparison"):
        parts = []
        for key, unit in (("readiness_score", ""), ("hrv_ms", " ms"), ("resting_hr", ""), ("sleep_score", "")):
            m = d["comparison"][key]
            if m["today"] is not None:
                usual = f" (usual {m['avg_7d']})" if m["avg_7d"] is not None else ""
                parts.append(f"{PLAIN_METRIC[key]} {m['today']}{unit}{usual}")
        lines.append("Oura: " + " · ".join(parts))
        lines.append(f"Recovery: {d['oura_band']['plain']}")
        if d["band"]["changed_by_checkin"]:
            lines.append(f"Check-in: {d['band']['plain']}")
    if "window" in d:
        lines.append(f"Free: {_slot('-'.join(d['window']))}" if d["window"] else "Free: no time left today")
    if d.get("reasoning"):
        lines.append(f"Coach's reasoning: {d['reasoning']}")
    changes = _override_lines(session["override_reasons"])
    lines.append("What code changed:" if changes else "Code changed nothing.")
    lines += changes
    if d.get("food_totals_today") is not None:
        eaten = d["food_totals_today"]
        food = f"Food: {kcal_protein(eaten)} so far" if eaten["entries"] else "Food: nothing logged yet"
        if d["targets"]["targets"]:
            food += f" · target {kcal_protein(d['targets']['targets'])} · left {kcal_protein(d['remaining'])}"
        lines.append(food)
        source = "the coach" if plan.get("food_source") == "coach" else "fixed options (the plan changed)"
        lines.append(f"Food ideas from {source}.")
    if plan.get("exercises_note"):
        lines.append(plan["exercises_note"])
    return "\n".join(lines)
