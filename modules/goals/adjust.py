"""Keeping a plan going when life happens. Pure: takes the plan's days and their neutral signals, returns the
days changed and what changed, each with its reason (stored in program_adjustments by the module).

- A past session is done when they did at least 70% of it (its discipline's workout kinds, its measure), else
  missed.
- A missed session that isn't the long one is dropped (never doubled up later).
- A missed long session moves to the next free day before the next long one (keeping the rest days and the rest
  before the long one); with no free day, the next long session is held at the missed length.
- Two or more missed sessions in a week: the next week repeats that week (never the taper).
- A low-readiness day (the planner already made it easy): its sessions are marked moved.
- A pause (a saved fact with a cap, or a limit, that covers days): those sessions are skipped; after a whole week
  paused, the first week back repeats the last week before the pause.
Run on its own output again, it changes nothing.

Every adjustment ends with validate_plan. If the adjusted plan breaks a safety check (after a long pause the
first week back would jump too far), nothing is changed: the result has the errors and the options, computed
here (a later event date, or skipping the lighter weeks still ahead). Growth is never squeezed past the checks.
"""
import copy
from datetime import date, timedelta

from programs.profile import Profile
from programs.schema import Template
from programs.validate import _growth, _long_growth, template_weeks, validate_plan
from modules.goals import signals
from modules.goals.feasibility import earliest_event, weeks_until

DONE_SHARE = 0.7


def _target(x: dict) -> float:
    return x.get("amount") or x.get("minutes") or 0


def _status(x: dict) -> str:
    return x.get("status", "planned")


def _mark_done(days: list[dict], signals_by_day: dict, today: date, t: Template) -> list[dict]:
    """Past planned sessions become done or missed; returns the missed ones as (day, session)."""
    missed = []
    for d in days:
        if d["date"] >= today:
            continue
        left = {}
        for x in d["sessions"]:
            if _status(x) != "planned":
                continue
            disc = t.disciplines[x["discipline"]]
            if x["discipline"] not in left:
                left[x["discipline"]] = signals.done(signals_by_day.get(d["date"].isoformat()), disc.kinds,
                                                     disc.measure)
            if left[x["discipline"]] >= DONE_SHARE * _target(x) > 0:
                x["status"] = "done"
                left[x["discipline"]] -= _target(x)
            else:
                x["status"] = "missed"
                missed.append((d, x))
    return missed


def _is_long(t: Template, x: dict) -> bool:
    return t.session_type(x["discipline"], x["type"]).long


def _rest_ok(days: list[dict], day: date, t: Template) -> bool:
    """Adding a session on `day` keeps the Monday-Sunday week's rest days."""
    monday = day - timedelta(days=day.weekday())
    week = [d for d in days if monday <= d["date"] < monday + timedelta(days=7)]
    rest = sum(1 for d in week if not d["sessions"] and d["date"] != day)
    return rest >= max(t.shared.min_rest_days, 1)


def _free_day(days: list[dict], missed_day: date, today: date, t: Template, variant: str) -> dict | None:
    next_anchor = next((d["date"] for d in days if d["anchor"] and d["date"] > missed_day), None)
    if next_anchor is None:
        return None
    before = max((r.days for r in t.rules(variant) if r.kind == "rest_before_long"), default=0)
    after = max((r.days for r in t.rules(variant) if r.kind == "light_after_long"), default=0)
    for d in days:
        if (max(today, missed_day + timedelta(days=after + 1)) <= d["date"] < next_anchor - timedelta(days=before)
                and not d["sessions"] and _rest_ok(days, d["date"], t)):
            return d
    return None


def _repeat(days: list[dict], week_n: int, into: int, why: str) -> dict | None:
    """Week `into` becomes a copy of week `week_n` (same offsets from the anchor). None if it can't."""
    src = [d for d in days if d["week"] == week_n]
    dst = [d for d in days if d["week"] == into]
    if not src or not dst or any(x.get("repeat_of") for d in dst for x in d["sessions"]):
        return None
    src_anchor = next(d["date"] for d in src if d["anchor"])
    dst_anchor = next(d["date"] for d in dst if d["anchor"])
    by_offset = {(d["date"] - src_anchor).days: d["sessions"] for d in src}
    for d in dst:
        if any(_status(x) != "planned" for x in d["sessions"]):
            return None                                   # that week has started: leave it
    for d in dst:
        d["sessions"] = [{**copy.deepcopy(x), "status": "planned", "repeat_of": week_n}
                         for x in by_offset.get((d["date"] - dst_anchor).days, [])]
    return {"day": dst_anchor, "kind": "repeat", "reason": why}


def options(days: list[dict], t: Template, variant: str, profile: Profile, today: date) -> list[dict]:
    """What would make the rest of the plan fit again, in code: start again from the last week they completed
    and move the event later, or keep the date and skip the lighter weeks still ahead (if that passes the checks)."""
    done = [d["tweek"] for d in days if any(_status(x) == "done" for x in d["sessions"])]
    resume = max(done) if done else days[0]["tweek"]
    total = len(t.variants[variant].weeks)
    remaining = list(range(resume, total + 1))               # the last week done is repeated first
    event = days[-1]["date"] if t.event and t.event.dated else None
    out = [{"kind": "later_date", "weeks": len(remaining), "resume_week": resume,
            "event": earliest_event(today, profile.anchor_weekday, len(remaining),
                                    (event or today).weekday()) if event else None}]
    if event:
        lighter = [n for n in remaining[1:] if t.variants[variant].weeks[n - 1].lighter]
        kept = [n for n in remaining if n not in lighter]
        weeks = [w for w in template_weeks(t, variant) if w["n"] in kept]
        if lighter and len(kept) <= weeks_until(today, event, profile.anchor_weekday) and not _growth(
                t, weeks, {}, "") and not _long_growth(t, weeks, {}, weeks[0]["anchor"] - 6, False, ""):
            out.append({"kind": "skip_lighter", "skip_weeks": lighter, "weeks": len(kept), "resume_week": resume,
                        "event": event})
    return out


def adjust(days: list[dict], signals_by_day: dict, today: date, t: Template, profile: Profile, variant: str,
           band_by_day: dict[date, str] | None = None,
           paused: list[tuple[date, date, str]] = (), move_missed_long: bool = True) -> dict:
    """{"days", "changes", "errors", "options"}: errors non-empty = nothing changed (days are the input). A missed
    long session that can't move without breaking a check is held instead (the rule's own alternative)."""
    original = days
    days = copy.deepcopy(days)
    changes = []
    last_weeks = {d["week"] for d in days if d["phase"] == days[-1]["phase"]} if t.event else set()

    for start, end, why in paused:
        for d in days:
            if start <= d["date"] <= end:
                for x in d["sessions"]:
                    if _status(x) == "planned":
                        x["status"] = "skipped"
                        changes.append({"day": d["date"], "kind": "skipped", "reason": why})

    for day, band in (band_by_day or {}).items():
        for d in days:
            if d["date"] == day and band == "recover":
                for x in d["sessions"]:
                    if _status(x) == "planned":
                        x["status"] = "moved"
                        changes.append({"day": day, "kind": "moved", "reason": "low readiness: made easy"})

    missed = _mark_done(days, signals_by_day, today, t)
    for d, x in missed:
        if not _is_long(t, x):
            changes.append({"day": d["date"], "kind": "dropped",
                            "reason": f"missed {x['discipline']}.{x['type']}, not made up"})
            continue
        free = _free_day(days, d["date"], today, t, variant) if move_missed_long else None
        if free:
            free["sessions"] = [{**copy.deepcopy(x), "status": "planned", "moved_from": d["date"].isoformat()}]
            x["status"] = "rescheduled"
            changes.append({"day": free["date"], "kind": "moved",
                            "reason": f"the long {x['discipline']} session missed on {d['date']:%a %b %-d}"})
            continue
        nxt = next(((n, y) for n in days if n["date"] > d["date"] for y in n["sessions"]
                    if _is_long(t, y) and y["discipline"] == x["discipline"] and _status(y) == "planned"), None)
        if nxt and (nxt[1].get("amount") or 0) > (x.get("amount") or 0) and not nxt[1].get("held"):
            nxt[1]["amount"], nxt[1]["held"] = x["amount"], True
            changes.append({"day": nxt[0]["date"], "kind": "held",
                            "reason": f"the long {x['discipline']} session missed on {d['date']:%a %b %-d}"})

    by_week: dict[int, int] = {}
    for d, x in missed:
        by_week[d["week"]] = by_week.get(d["week"], 0) + 1
    for week_n, count in sorted(by_week.items()):
        if count >= 2 and week_n + 1 not in last_weeks:
            change = _repeat(days, week_n, week_n + 1, f"{count} sessions missed in week {week_n}")
            if change:
                changes.append(change)

    weeks = sorted({d["week"] for d in days})
    for w in weeks:
        sessions = [x for d in days if d["week"] == w for x in d["sessions"]]
        if sessions and all(_status(x) == "skipped" for x in sessions):
            later = [n for n in weeks if n > w and any(_status(x) == "planned"
                                                       for d in days if d["week"] == n for x in d["sessions"])]
            before = [n for n in weeks if n < w and any(_status(x) == "done"
                                                        for d in days if d["week"] == n for x in d["sessions"])]
            if later and before and later[0] not in last_weeks and not any(
                    _status(x) == "skipped" for d in days if d["week"] == later[0] for x in d["sessions"]):
                change = _repeat(days, before[-1], later[0], f"back after a paused week {w}")
                if change:
                    changes.append(change)
    errors = validate_plan(days, profile, t, variant)
    if errors and move_missed_long and any(c["kind"] == "moved" and "missed on" in c["reason"] for c in changes):
        return adjust(original, signals_by_day, today, t, profile, variant, band_by_day, paused, False)
    if errors:
        return {"days": original, "changes": [], "errors": errors,
                "options": options(days, t, variant, profile, today)}
    return {"days": days, "changes": changes, "errors": [], "options": []}
