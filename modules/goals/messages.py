"""What the person reads about their goal, built by code and sent as built. Plain words only: no snake_case, no
"null", no "window" (tests/test_goal_scenarios.py)."""
from datetime import date, timedelta

import yaml

from programs.schema import Template
from programs.validate import CONFIG, minutes_of
from modules.goals.profile import questions

MOVEMENT_LABELS = yaml.safe_load((CONFIG / "movements.yaml").read_text())["labels"]
EFFORT = {1: "very easy", 2: "easy", 3: "moderate", 4: "hard"}


def day_label(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def amount(t: Template, x: dict) -> str:
    measure = t.disciplines[x["discipline"]].measure
    value = x.get("amount")
    if value is None:
        return ""
    return {"km": f"{value:.1f} km", "min": f"{value:.0f} min", "elevation_m": f"{value:.0f} m of climbing"}[measure]


def session(t: Template, x: dict, profile=None) -> str:
    st = t.session_type(x["discipline"], x["type"])
    size = amount(t, x)
    text = f"{st.label}, {size}" if size else st.label
    minutes = minutes_of(x, t, profile) if profile and t.disciplines[x["discipline"]].measure == "km" else None
    return f"{text} (about {minutes:.0f} min)" if minutes else text


def sessions_line(t: Template, sessions: list[dict], profile=None) -> str:
    return ", then ".join(session(t, x, profile) for x in sessions) if sessions else "rest"


def ask(intro: str, keys: list[str]) -> str:
    return "\n".join([intro] + questions(keys))


def no_template(goal_text: str) -> str:
    return (f"I don't have a program for “{goal_text}” yet, so I can't build a week-by-week plan for it. "
            "I can still plan each workout as you go and track how it's going.")


def preview(t: Template, variant: str, a: dict, event: date | None, days: list[dict], profile,
            finish: dict | None, short_note: str | None = None) -> str:
    """The plan before they say yes."""
    longs = [x for d in days for x in d["sessions"] if t.session_type(x["discipline"], x["type"]).long]
    strength = sum(1 for d in days if d["phase"] == "build" for x in d["sessions"] if x["discipline"] == "strength")
    build_weeks = len({d["week"] for d in days if d["phase"] == "build"})
    lines = [f"{t.title}" + (f", {day_label(event)} · {a['weeks_left']} weeks from now." if event else ".")]
    start = f"starting at week {a['entry_week']}" if a["entry_week"] > 1 else "from week 1"
    hold = f"; the first {a['extra_weeks']} weeks hold steady before it builds" if a["extra_weeks"] else ""
    lines.append(f"Plan: {variant} ({t.variants[variant].source.split(',')[0]}), {start}{hold}.")
    if longs:
        anchor = next(d["date"] for d in days if d["anchor"])
        lines.append(f"Long sessions on {anchor:%A}s, {amount(t, longs[0])} → {amount(t, max(longs, key=lambda x: x['amount'] or 0))}"
                     + (f"; strength about {round(strength / build_weeks)} times a week" if build_weeks and strength else "")
                     + ".")
    if finish and finish["predicted_minutes"]:
        predicted = _hm(finish["predicted_minutes"])
        line = f"From your {finish['from']['distance_km']:.1f} km run, about {predicted} looks likely (approximate)"
        if finish["target_minutes"]:
            line += f", so {_hm(finish['target_minutes'])} looks " + ("within reach." if finish["within"] else "like a stretch.")
        else:
            line += "."
        lines.append(line)
    elif finish is not None:
        lines.append("Not enough run data to predict a time yet. A run of 5 km or more with your watch or ring "
                     "on would let me.")
    if short_note:
        lines.append(short_note)
    lines.append("Set it up?")
    return "\n".join(lines)


def strength_short(short: list[dict], rules: dict, days_per_week: int) -> str | None:
    """When the plan has fewer lifts than a confirmed rule asks (in the weeks the rule covers), why and the two
    ways to fix it. None when every week has them."""
    wanted = rules.get("strength_sessions_per_week")
    misses = [s for s in short if wanted and s["phase"] in ("base", "build", "peak") and s["placed"] < wanted]
    if not misses:
        return None
    fewest = min(misses, key=lambda s: s["placed"])
    head = f"Your rules ask for {int(wanted)} lifts a week; {fewest['placed']} fits"
    timed = next((s["time"] for s in misses if s["time"]), None)
    if timed:
        return (f"{head}, because a run plus a lift needs about {timed['needed_min']} min and you have "
                f"{timed['limit_min']}. More time on those days, or another training day, would fit both.")
    return (f"{head}, because your {days_per_week} training days a week are full. Another training day, or more "
            "time to add a lift after a run, would fit them.")


def not_feasible(t: Template, a: dict, event: date | None) -> str:
    if a["entry_week"] is None:
        need = ", ".join(f"about {v:.1f} {_unit(t, k)}" for k, v in (a["build_to"] or {}).items())
        return (f"Week 1 of this plan asks for more than you've done lately. Build up to a longest session of "
                f"{need} first, then we can start.")
    return (f"{day_label(event)} is {a['weeks_left']} weeks away, and this plan needs {a['weeks_needed']}. The "
            f"earliest date that fits is {day_label(a['earliest_event'])}. Want that date, or a shorter goal?")


def _unit(t: Template, discipline: str) -> str:
    return {"km": "km", "min": "min", "elevation_m": "m of climbing"}[t.disciplines[discipline].measure]


def _hm(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}:{m:02d}"


def set_up(t: Template, days: list[dict], event: date | None) -> str:
    first = next((d for d in days if d["sessions"]), None)
    when = f" First session: {day_label(first['date'])}, {sessions_line(t, first['sessions'])}." if first else ""
    return f"Done: your {t.title.lower()} plan is set up" + (f" for {day_label(event)}." if event else ".") + when


def today_text(t: Template, day: dict | None, total_weeks: int, profile, done: list[bool] | None = None) -> str:
    if day is None:
        return "Nothing from your plan today."
    head = f"Today (week {day['week']} of {total_weeks}, {day['phase']}): "
    planned = [x for x in day["sessions"] if x.get("status", "planned") in ("planned", "done")]
    if not planned:
        return head + "rest."
    text = head + sessions_line(t, planned, profile) + "."
    if done and all(done):
        text += " Done already."
    return text


def status_text(t: Template, days: list[dict], today: date, event: date | None, adjustments: list[dict]) -> str:
    past = [x for d in days if d["date"] < today for x in d["sessions"] if x.get("status") not in ("skipped",)]
    done = sum(1 for x in past if x.get("status") == "done")
    week = next((d["week"] for d in days if d["date"] >= today), days[-1]["week"])
    longs = [(d, x) for d in days if d["date"] < today for x in d["sessions"]
             if t.session_type(x["discipline"], x["type"]).long and x.get("status") == "done"]
    lines = [f"Week {week} of {days[-1]['week']}: {done} of {len(past)} sessions done so far."]
    if longs:
        lines.append(f"Longest session done: {amount(t, max((x for _, x in longs), key=lambda x: x['amount'] or 0))}.")
    if event:
        lines.append(f"{(event - today).days // 7} weeks to {day_label(event)}.")
    for a in [a for a in adjustments if a["kind"] in CHANGE_WORDS][-1:]:
        lines.append(f"Last change: {a['reason']}, so {CHANGE_WORDS[a['kind']]}.")
    return "\n".join(lines)


CHANGE_WORDS = {"moved": "it moved to another day", "held": "the next long one stays the same length",
                "repeat": "the next week repeats it"}


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def limit_saved(body_area: str, avoid: list[str], cap: int | None, end: date | None) -> str:
    what = _listed([MOVEMENT_LABELS.get(m, m) for m in avoid]) if avoid else ""
    parts = [f"no {what}" if what else "", f"keep it {EFFORT[cap]}" if cap and cap in EFFORT else ""]
    rule = ", and ".join(p for p in parts if p) if len(avoid) > 1 else " and ".join(p for p in parts if p)
    rule = rule or "go easy"
    until = f"until {day_label(end)}" if end else "until you tell me it's better"
    return f"Saved: {rule} for your {body_area} {until}."


def limit_ended(body_area: str) -> str:
    return f"Good to hear. I've taken the {body_area} limit off your plan."


def options_text(options: list[dict]) -> str:
    lines = ["After the break, the rest of the plan no longer fits safely before the date."]
    for o in options:
        if o["kind"] == "later_date" and o.get("event"):
            lines.append(f"- Move the date to {day_label(o['event'])} and pick up from week {o['resume_week']}.")
        elif o["kind"] == "skip_lighter":
            lines.append(f"- Keep the date and skip the lighter weeks ({', '.join(str(w) for w in o['skip_weeks'])}).")
    lines.append("Which would you like?")
    return "\n".join(lines)


def direction_proposed(rules: list[dict], catalog: dict) -> str:
    if not rules:
        return ("I can't turn that into rules I can check yet. I can plan strength sessions, run days and "
                "protein for you; want any of those?")
    lines = ["Here's how I'd shape your plan around that:"]
    lines += [f"- {_rule_value(r['key'], r['value'])} {catalog[r['key']]['label']} ({r['why']})" for r in rules]
    lines.append("Keep these, or change any?")
    return "\n".join(lines)


def _rule_value(key: str, value: float) -> str:
    return f"{value:g}" if key == "protein_g_per_kg" else f"{int(value)}"


def direction_confirmed(rules: dict, catalog: dict) -> str:
    return "Saved: " + "; ".join(f"{_rule_value(k, v)} {catalog[k]['label']}" for k, v in rules.items()) + "."


def next_week_start(d: date) -> date:
    return d + timedelta(days=7 - d.weekday())
