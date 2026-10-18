"""What the person reads about training: the plan message and the full "why". Plain words only
(labels.py), no internal terms. Code builds these; the agent sends them exactly as they are.
"""
from core.labels import PLAIN_METRIC, blocks_phrase, clock, clock_range, emoji, workout_label
from modules.nutrition.messages import kcal_protein
from modules.training.blocks import blocks_of

DONE_PROMPT = 'Reply "done" when you finish, or I\'ll mark it from Oura if it records it.'


def _num(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.0f}{unit}"


def readiness_line(readiness) -> str:
    return f"🔋 Readiness {readiness}" if readiness is not None else "🔋 Readiness: no data yet"


def _slot(time_slot: str) -> str:
    start, end = time_slot.split("-")
    return clock_range(start, end)


def _workout_line(plan: dict, with_emoji: bool = True) -> str:
    """"🏃 HIIT · 30 min · 6:30–7:00pm", or for several blocks "🏃 Easy run 20 min + 🏋️ Strength (Legs) 30 min · …"."""
    def icon(workout_type):
        return f"{emoji(workout_type)} " if with_emoji else ""
    blocks = plan.get("blocks") or []
    if len(blocks) > 1:
        parts = [f"{icon(b['workout_type'])}{workout_label(b['workout_type'], plan.get('split_day'))} "
                 f"{b['duration_min']} min" for b in blocks]
        return f"{' + '.join(parts)} · {_slot(plan['time_slot'])}"
    return (f"{icon(plan['workout_type'])}{workout_label(plan['workout_type'], plan.get('split_day'))} · "
            f"{plan['duration_min']} min · {_slot(plan['time_slot'])}")


def format_consult(request: list[dict], suggestion: list[dict], conflicts: list[dict]) -> str:
    """The one question before planning a request the rules would change, built from the actual request,
    the rules' suggestion and their reasons (at most two). Low readiness already says why the band
    is low, so the band's sentence is left out then."""
    rules = {o["rule"] for o in conflicts}
    reasons = []
    for o in conflicts:
        if o["rule"] == "band" and "readiness_floor" in rules:
            continue
        if o["plain"] not in reasons:
            reasons.append(o["plain"])
    asked = blocks_phrase(request)
    return (f"{' '.join(reasons[:2])}\n"
            f"I'd suggest {blocks_phrase(suggestion)} instead. Still want {asked}?")


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
        lines.append(_workout_line(plan))
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
        if plan["follow_up"]:
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


def _done_line(session: dict) -> str | None:
    """Which blocks are done, with their times and who recorded them."""
    plan = session["plan_json"] or {}
    if plan.get("workout_type") == "rest":
        return None
    done = session.get("blocks_done") or {}
    if not done and session.get("completed") != 1:
        return "Done: not yet"
    if not done:
        return "Done: yes"
    parts = []
    for i, b in enumerate(blocks_of(plan)):
        label = workout_label(b["workout_type"], plan.get("split_day"))
        d = done.get(i)
        by = "Oura" if d and d["done_by"] == "oura" else "you"
        parts.append(f"{label} {clock_range(d['start'][11:16], d['end'][11:16])} (from {by})" if d
                     else f"{label} not yet")
    return "Done: " + "; ".join(parts)


def format_why(session: dict | None) -> str:
    """Everything behind the latest plan, in plain words."""
    if session is None:
        return "No plan yet today. Say \"want to work out\" to get one."
    plan, d = session["plan_json"] or {}, session["details_json"] or {}
    when = clock(session["checkin_at"][11:16]) if session["checkin_at"] else "earlier"
    headline = "Rest" if plan.get("workout_type") == "rest" else _workout_line(plan, with_emoji=False)
    lines = [f"Your {when} plan: {headline}"]
    done = _done_line(session)
    if done:
        lines.append(done)

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
    overrides = session["override_reasons"] or []
    skipped = [o for o in overrides if isinstance(o, dict) and o["rule"] == "user_choice"]
    changes = _override_lines([o for o in overrides if o not in skipped])
    lines.append("What code changed:" if changes else "Code changed nothing.")
    lines += changes
    if skipped:
        lines.append("Rules you chose to skip:")
        lines += _override_lines(skipped)

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
