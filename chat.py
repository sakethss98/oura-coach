"""Message in, reply text out. No Telegram imports, so all of this is testable offline.

bot.py calls `Coach.handle_text` / `Coach.handle_command` from a worker thread.
"""
import json
import re
from datetime import date, datetime, timedelta

import db
from activity import UNKNOWN_DURATION
from config import TZ
from food import format_entry, log_food
from graph import resume_run, start_run
from labels import PLAIN_METRIC, clock, clock_range, emoji, workout_label
from llm import load_prompt, structured_call
from oura_client import latest_day, load_fixtures
from router import route
from schemas import Answer
from timeparse import parse_clock_time, resolve_dates
from today import snapshot

HELP = """I'm your coach. Just message me:
- "want to work out now" or /checkin: I ask energy/soreness, then plan a workout for right now
- "had poha", "lunch at 1: rajma chawal": I log food (message time unless you give one)
- "traveling Thu-Sat, keep it light": a context note with dates
- "did the run, felt heavy": marks your latest plan done; "went for a 30 min walk" logs an unplanned one
- "what should I eat for dinner?": answered from today's data
- "why?" or /why: the full reasoning behind your latest plan

Commands:
/checkin - start a check-in
/plan - plan now without asking (reuses today's latest check-in)
/food <text> - log food at the message time
/note <text> - add a context note
/undo - remove your last food or note entry
/today - sessions, activity, food so far, active notes
/why - the full reasoning behind your latest plan
/help - this message"""

UNCLEAR = ("Sorry, I'm not sure what you mean. Try: \"had dal and rice\", \"want to work out now\", "
           "\"did the run, felt good\", or /help.")
CHECKIN_REMINDER = "(Your check-in is still waiting: reply like 4 2, or 'skip'.)"

_LABELED_ENERGY = re.compile(r"\benergy\s*[:=]?\s*([1-5])\b", re.I)
_LABELED_SORENESS = re.compile(r"\bsore(?:ness)?\s*[:=]?\s*([1-5])\b", re.I)
_LEADING_PAIR = re.compile(r"^\s*([1-5])(?:\s*/\s*5)?\s*[,/ ]\s*([1-5])(?:\s*/\s*5)?\b(.*)$", re.S)


def parse_checkin_reply(text: str) -> dict | None:
    """Energy/soreness from a check-in answer, or None if this is not one.

    Accepts "skip", labeled values ("energy 4, soreness 2, legs heavy") or a reply that
    starts with two numbers ("4 2 slept badly", "4/5, 2/5"). Numbers later in a sentence
    ("had 3 eggs and 2 toast") are not a check-in.
    """
    stripped = text.strip()
    if stripped.lower() in {"skip", "/skip"}:
        return {"energy": None, "soreness": None, "note": None}
    energy, soreness = _LABELED_ENERGY.search(stripped), _LABELED_SORENESS.search(stripped)
    if energy or soreness:
        rest = _LABELED_SORENESS.sub("", _LABELED_ENERGY.sub("", stripped))
        return {"energy": int(energy[1]) if energy else None,
                "soreness": int(soreness[1]) if soreness else None,
                "note": rest.strip(" ,.;:-") or None}
    pair = _LEADING_PAIR.match(stripped)
    if pair:
        return {"energy": int(pair[1]), "soreness": int(pair[2]), "note": pair[3].strip(" ,.;:-") or None}
    return None


DONE_PROMPT = 'Reply "done" when you finish, or I\'ll mark it from Oura if it records it.'


def _num(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.0f}{unit}"


def readiness_line(readiness) -> str:
    return f"🔋 Readiness {readiness}" if readiness is not None else "🔋 Readiness: no data yet"


def _slot(time_slot: str) -> str:
    start, end = time_slot.split("-")
    return clock_range(start, end)


def kcal_protein(numbers: dict) -> str:
    return f"{numbers['calories']:.0f} kcal · {numbers['protein_g']:.0f}g protein"


def format_targets(targets: dict) -> str:
    if targets["targets"] is None:
        return f"Targets: unavailable ({targets['reason']})"
    t, i = targets["targets"], targets["inputs"]
    return (f"Targets today: {t['calories']} kcal, protein {t['protein_g']}g, carbs {t['carbs_g']}g, "
            f"fat {t['fat_g']}g, fiber {t['fiber_g']}g\n"
            f"  (from {i['age']} y, {i['weight_kg']} kg [{i['weight_source']}], {i['height_cm']} cm, {i['sex']}; "
            f"BMR {i['bmr']} kcal)")


def format_remaining(remaining: dict | None) -> str:
    if remaining is None:
        return "Left today: n/a"
    return (f"Left today: {remaining['calories']} kcal, protein {remaining['protein_g']}g, "
            f"carbs {remaining['carbs_g']}g, fiber {remaining['fiber_g']}g")


def format_food_today(totals: dict) -> str:
    if not totals["entries"]:
        return "Food so far: nothing logged"
    return (f"Food so far: {totals['entries']} entries, ~{totals['calories']:.0f} kcal, "
            f"protein {totals['protein_g']:.0f}g, carbs {totals['carbs_g']:.0f}g, fiber {totals['fiber_g']:.0f}g")


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
        return "No plan yet today. Send /checkin to get one."
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


def format_today(snap: dict) -> str:
    lines = [f"Today, {snap['now_label']}", f"Recovery: {snap['band']['plain']}"]
    sessions = snap["sessions_today"]
    if sessions:
        lines.append("Sessions:")
        for s in sessions:
            plan = s["plan_json"] or {}
            label = workout_label(plan.get("workout_type", "other"), plan.get("split_day"))
            if s["source"] == "unplanned":
                when = clock_range(s["actual_start"][11:16], s["actual_end"][11:16]) if s["actual_start"] else ""
                lines.append(f"- Logged: {label} {when}" + (f", felt {s['feeling']}" if s["feeling"] else ""))
                continue
            slot = _slot(plan["time_slot"]) if plan.get("time_slot", "none") != "none" else ""
            status = ("done, from Oura" if s["done_by"] == "oura" else "done") if s["completed"] == 1 \
                else "not done yet"
            lines.append(f"- Planned: {label} {slot} ({status})".replace("  ", " ")
                         + (f", felt {s['feeling']}" if s["feeling"] else ""))
    else:
        lines.append("Sessions: none")
    lines.append(f"Activity: {format_activity(snap['activity_today'])}")
    if snap["workout_records_fetched"] == 0:
        lines.append("(Oura returned no workouts for the last 7 days.)")
    a = snap["oura_activity_today"]
    lines.append(f"Oura today: {_num(a['steps'])} steps, {_num(a['active_calories'])} active kcal, "
                 f"{_num(a['medium_activity_min'])} min medium / {_num(a['high_activity_min'])} min high activity")
    lines.append(format_food_today(snap["food_totals"]["today"]))
    for f in snap["food_today"]:
        lines.append(f"  {clock(f['time'])} {f['text']} (~{_num(f['calories'])} kcal, {_num(f['protein_g'])}g protein)")
    lines.append(format_targets(snap["targets"]))
    lines.append(format_remaining(snap["remaining"]))
    notes = snap["context_notes"]
    lines.append("Notes: " + ("; ".join(notes) if notes else "none"))
    return "\n".join(lines)


class Coach:
    def __init__(self, app, use_fixtures: bool = False):
        self.app = app
        self.use_fixtures = use_fixtures
        self._fixture_day = latest_day(load_fixtures()) if use_fixtures else None

    def clock(self, msg_time: datetime) -> datetime:
        """Local message time. Fixtures mode keeps the clock time but moves it to the fixtures' today."""
        local = msg_time.astimezone(TZ).replace(second=0, microsecond=0)
        if self._fixture_day:
            return datetime.combine(self._fixture_day, local.time(), TZ)
        return local

    # --- entry points -------------------------------------------------------

    def handle_text(self, text: str, msg_time: datetime) -> str:
        now = self.clock(msg_time)
        pending = self._pending(now.date())
        if pending:
            answer = parse_checkin_reply(text)
            if answer is not None:
                return self._resume(pending, answer, now)

        latest = db.latest_planned_session(now.date())
        last_question = ((latest["plan_json"] or {}).get("follow_up") or None) if latest else None
        actions = route(text, now, pending is not None, db.latest_open_session(now.date()), last_question)
        if not actions:
            return UNCLEAR
        replies = [self._do(action, text, now) for action in actions]
        if pending and not any(a.type == "start_checkin" for a in actions):
            replies.append(CHECKIN_REMINDER)
        return "\n\n".join(replies)

    def handle_command(self, command: str, args: str, msg_time: datetime) -> str:
        now = self.clock(msg_time)
        if command == "checkin":
            return self._start_checkin(now)
        if command == "plan":
            return self._plan_now(now)
        if command == "food":
            if not args.strip():
                return "Usage: /food what you had"
            return format_entry(log_food(args.strip(), now))
        if command == "note":
            return self._note_command(args.strip(), now)
        if command == "undo":
            return self._undo()
        if command == "today":
            return format_today({**snapshot(now, self.use_fixtures), "now_label": now.strftime("%a %b %d · ")
                                 + clock(now)})
        if command == "why":
            return format_why(db.latest_planned_session(now.date()))
        return HELP

    # --- actions ------------------------------------------------------------

    def _do(self, action, text: str, now: datetime) -> str:
        if action.type == "food":
            return self._food(action.text or text, action.time_text, now)
        if action.type == "context_note":
            return self._note(action.text or text, action.start_text, action.end_text, now.date())
        if action.type == "workout_done":
            return self._workout_done(action, now)
        if action.type == "start_checkin":
            return self._start_checkin(now)
        if action.type == "question":
            return self._answer(action.text or text, now)
        if action.type == "why":
            return format_why(db.latest_planned_session(now.date()))
        return UNCLEAR

    def _food(self, text: str, time_text: str | None, now: datetime) -> str:
        eaten_at = parse_clock_time(time_text, now)
        reply = format_entry(log_food(text, eaten_at or now), time_stated=eaten_at is not None)
        if time_text and eaten_at is None:
            reply += f"\n  (Couldn't read the time \"{time_text}\", so I used the message time.)"
        return reply

    def _note(self, text: str, start_text: str | None, end_text: str | None, today: date) -> str:
        dates = resolve_dates(start_text, end_text, today)
        if dates is None:
            return (f"I couldn't work out the dates for \"{text}\" (\"{start_text}\" - \"{end_text}\"). "
                    "Please send it again with days like Thu-Sat or Oct 12.")
        start, end = dates
        db.add_context_note(text, start, end)
        span = f"{start:%a %b %d}" if start == end else f"{start:%a %b %d} - {end:%a %b %d}"
        default = " (no dates given, so today only)" if not start_text and not end_text else ""
        return f"Noted for {span}{default}: {text}"

    def _note_command(self, text: str, now: datetime) -> str:
        if not text:
            return "Usage: /note traveling Thu-Sat, keep it light"
        actions = route(f"Context note: {text}", now, False, None)
        note = next((a for a in actions if a.type == "context_note"), None)
        if note is None:
            return self._note(text, None, None, now.date())
        return self._note(text, note.start_text, note.end_text, now.date())

    def _workout_done(self, action, now: datetime) -> str:
        day = now.date()
        done_at = parse_clock_time(action.time_text, now) or now
        open_session = db.latest_open_session(day)
        feeling = f", felt {action.feeling}" if action.feeling else ""

        if action.refers_to_plan and open_session:
            plan = open_session["plan_json"] or {}
            minutes = action.duration_min or plan.get("duration_min")
            start = done_at - (timedelta(minutes=minutes) if minutes else UNKNOWN_DURATION)
            db.mark_done(open_session["id"], action.feeling, now, start, done_at)
            label = workout_label(plan.get("workout_type", "other"), plan.get("split_day"))
            return f"Nice! Marked done: {label}{feeling}. Counted as {clock_range(start, done_at)}."

        workout_type = action.workout_type or "other"
        minutes = action.duration_min
        start = done_at - (timedelta(minutes=minutes) if minutes else UNKNOWN_DURATION)
        db.add_unplanned(day, workout_type, minutes, start, done_at, action.feeling, now)
        why = " (no open plan today, so I logged it as extra)" if action.refers_to_plan else ""
        length = f"{minutes} min" if minutes else "length not given"
        return (f"Logged: {workout_label(workout_type).lower()}, {length}, "
                f"{clock_range(start, done_at)}{feeling}{why}.")

    def _start_checkin(self, now: datetime) -> str:
        result = start_run(self.app, now, self.use_fixtures)
        if "question" in result:
            return f"{result['summary']}\n{result['question']}"
        return format_plan(result)

    def _pending(self, today: date) -> dict | None:
        """The check-in waiting for an answer. One from an earlier day is abandoned."""
        pending = db.pending_session()
        if pending and pending["date"] != today.isoformat():
            db.abandon_pending()
            return None
        return pending

    def _resume(self, pending: dict, answer: dict, now: datetime) -> str:
        config = {"configurable": {"thread_id": pending["thread_id"]}}
        if not self.app.get_state(config).next:
            db.abandon_pending()
            return "That check-in can't be resumed any more. Send /checkin to start a new one."
        return format_plan(resume_run(self.app, pending["thread_id"], answer, now))

    def _plan_now(self, now: datetime) -> str:
        latest = db.latest_checkin(now.date())
        if latest:
            checkin = {"energy": latest["energy"], "soreness": latest["soreness"], "note": latest["note"],
                       "label": f"from your {clock(latest['checkin_at'][11:16])} check-in"}
        else:
            checkin = {"energy": None, "soreness": None, "note": None, "label": "skipped"}
        return format_plan(start_run(self.app, now, self.use_fixtures, checkin=checkin))

    def _undo(self) -> str:
        removed = db.undo_last()
        if removed is None:
            return "Nothing to undo."
        if removed["kind"] == "food":
            return f"Removed food #{removed['id']}: {removed['raw_text']} ({clock(removed['timestamp'][11:16])})."
        return f"Removed note: {removed['note']} ({removed['start_date']} - {removed['end_date']})."

    def _answer(self, question: str, now: datetime) -> str:
        snap = snapshot(now, self.use_fixtures)
        context = {
            "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
            "how_hard_today": {"level": snap["band"]["band"], "why": snap["band"]["plain"]},
            "oura_today": {k: v for k, v in snap["oura"]["today"].items() if k not in ("day", "workouts")},
            "body": snap["oura"]["body"],
            "activity_today": snap["activity_today"],
            "plans_today": [s["plan_json"] for s in snap["sessions_today"]],
            "food_eaten_today": snap["food_today"],
            "food_totals_today": snap["food_totals"]["today"],
            "targets": snap["targets"],
            "remaining_today": snap["remaining"],
            "context_notes": snap["context_notes"],
            "goals": snap["goals"],
        }
        user = f"Question: {question}\n\nToday's data:\n{json.dumps(context, indent=2, default=str)}"
        return structured_call("answer", Answer, load_prompt("answer"), user).reply
