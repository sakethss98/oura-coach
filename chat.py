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

Commands:
/checkin - start a check-in
/plan - plan now without asking (reuses today's latest check-in)
/food <text> - log food at the message time
/note <text> - add a context note
/undo - remove your last food or note entry
/today - sessions, activity, food so far, active notes
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


def _num(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.0f}{unit}"


def format_targets(targets: dict) -> str:
    if targets["targets"] is None:
        return f"Targets: unavailable ({targets['reason']})"
    t, i = targets["targets"], targets["inputs"]
    return (f"Targets today: {t['calories']} kcal, protein {t['protein_g']} g, carbs {t['carbs_g']} g, "
            f"fat {t['fat_g']} g, fiber {t['fiber_g']} g\n"
            f"  (from {i['age']} y, {i['weight_kg']} kg [{i['weight_source']}], {i['height_cm']} cm, {i['sex']}; "
            f"BMR {i['bmr']} kcal)")


def format_remaining(remaining: dict | None) -> str:
    if remaining is None:
        return "Remaining: n/a"
    return (f"Remaining: {remaining['calories']} kcal, protein {remaining['protein_g']} g, "
            f"carbs {remaining['carbs_g']} g, fiber {remaining['fiber_g']} g")


def format_food_today(totals: dict) -> str:
    if not totals["entries"]:
        return "Food so far: nothing logged"
    return (f"Food so far: {totals['entries']} entries, ~{totals['calories']:.0f} kcal, "
            f"protein {totals['protein_g']:.0f} g, carbs {totals['carbs_g']:.0f} g, fiber {totals['fiber_g']:.0f} g")


def format_activity(items: list[dict]) -> str:
    if not items:
        return "none"
    return "; ".join(
        f"{i['activity']} {i['start'] or '?'}-{i['end'] or '?'} ({_num(i['minutes'], ' min')})"
        + (" HARD" if i["hard"] else "") + (f", felt {i['feeling']}" if i["feeling"] else "")
        for i in items
    )


def format_plan(state: dict) -> str:
    plan, c, band = state["final_plan"], state["context"], state["baseline"]["band"]
    checkin = state["checkin"]
    window = f"{c['window'][0]}-{c['window'][1]}" if c["window"] else "none left today"
    lines = [f"Plan at {c['now']} (window {window}) | band {band['band'].upper()}: {band['reason']}"]
    if plan["workout_type"] == "rest":
        lines.append("Workout: rest")
    else:
        lines.append(f"Workout: {plan['workout_type']}, intensity {plan['intensity']}/5, "
                     f"{plan['time_slot']} ({plan['duration_min']} min)")
    lines.append(f"Why: {plan['reasoning']}")
    if checkin.get("energy") is None and checkin.get("soreness") is None:
        lines.append(f"Check-in: none ({checkin.get('label', 'skipped')})")
    else:
        note = f", note: {checkin['note']}" if checkin.get("note") else ""
        lines.append(f"Check-in: energy {_num(checkin.get('energy'))}, soreness {_num(checkin.get('soreness'))}"
                     f" ({checkin.get('label', 'answered now')}){note}")
    lines.append(f"Done today: {format_activity(c['activity_today'])}")
    if c["workout_records_fetched"] == 0:
        lines.append("(Oura returned no workout records for the last 7 days.)")
    lines.append(format_food_today(c["food_totals"]["today"]))
    if c["first_plan_today"]:
        lines.append(format_targets(c["targets"]))
        if plan.get("meal_ideas"):
            lines.append("Meal ideas:\n" + "\n".join(f"- {m}" for m in plan["meal_ideas"]))
    else:
        lines.append(format_remaining(c["remaining"]))
    lines.append(f"Food now: {plan['nutrition_note']}")
    if state["overrides"]:
        llm = state["llm_plan"]
        lines.append(f"Code changed the LLM plan ({llm['workout_type']}, intensity {llm['intensity']}, "
                     f"{llm['time_slot']}):\n" + "\n".join(f"- {o}" for o in state["overrides"]))
    return "\n".join(lines)


def format_today(snap: dict) -> str:
    lines = [f"Today {snap['day']}, {snap['now']} | band {snap['baseline']['band']['band'].upper()}"]
    sessions = snap["sessions_today"]
    if sessions:
        lines.append("Sessions:")
        for s in sessions:
            plan = s["plan_json"] or {}
            status = "done" if s["completed"] == 1 else "not marked done"
            when = plan.get("time_slot") or (s["actual_start"] or "")[11:16]
            lines.append(f"- {s['source']}: {plan.get('workout_type', '?')} {when} ({status})"
                         + (f", felt {s['feeling']}" if s["feeling"] else ""))
    else:
        lines.append("Sessions: none")
    lines.append(f"Activity: {format_activity(snap['activity_today'])}")
    if snap["workout_records_fetched"] == 0:
        lines.append("(Oura returned no workout records for the last 7 days.)")
    a = snap["oura_activity_today"]
    lines.append(f"Oura today: {_num(a['steps'])} steps, {_num(a['active_calories'])} active kcal, "
                 f"{_num(a['medium_activity_min'])} min medium / {_num(a['high_activity_min'])} min high activity")
    lines.append(format_food_today(snap["food_totals"]["today"]))
    for f in snap["food_today"]:
        lines.append(f"  {f['time']} {f['text']} (~{_num(f['calories'])} kcal, {_num(f['protein_g'])} g protein)")
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

        actions = route(text, now, pending is not None, db.latest_open_session(now.date()))
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
            return format_today(snapshot(now, self.use_fixtures))
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
            return (f"Marked done: {plan.get('workout_type')} (planned {plan.get('time_slot')}){feeling}. "
                    f"Counted as {start:%H:%M}-{done_at:%H:%M}.")

        workout_type = action.workout_type or "other"
        minutes = action.duration_min
        start = done_at - (timedelta(minutes=minutes) if minutes else UNKNOWN_DURATION)
        db.add_unplanned(day, workout_type, minutes, start, done_at, action.feeling, now)
        why = " (no open plan today, so logged as unplanned)" if action.refers_to_plan else ""
        length = f"{minutes} min" if minutes else "length not given"
        return f"Logged unplanned {workout_type}, {length}, {start:%H:%M}-{done_at:%H:%M}{feeling}{why}."

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
                       "label": f"from your {latest['checkin_at'][11:16]} check-in"}
        else:
            checkin = {"energy": None, "soreness": None, "note": None, "label": "check-in skipped"}
        return format_plan(start_run(self.app, now, self.use_fixtures, checkin=checkin))

    def _undo(self) -> str:
        removed = db.undo_last()
        if removed is None:
            return "Nothing to undo."
        if removed["kind"] == "food":
            return f"Removed food #{removed['id']}: {removed['raw_text']} ({removed['timestamp'][11:16]})."
        return f"Removed note: {removed['note']} ({removed['start_date']} - {removed['end_date']})."

    def _answer(self, question: str, now: datetime) -> str:
        snap = snapshot(now, self.use_fixtures)
        context = {
            "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
            "band": snap["baseline"]["band"],
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
