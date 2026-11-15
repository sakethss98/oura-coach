"""Safety checks every template and every generated plan must pass. Each returns a list of plain errors ([] = ok).

validate_template(t): the template on its own. Its structure (disciplines, types, movements and placements that
exist; phases in one run each), then per variant the safety numbers in config/program_safety.yaml: weekly load
growth, long-session growth, rest days, training days and hard sessions per week, a taper before a dated event,
and its own placement rules.

validate_plan(days, profile, t): a generated plan, before anything is written. The same safety checks on real
dates, now with what they did before the plan (profile.history), plus what only the person decides: their days
a week, their time per session, and their limits (no movement they must avoid, no effort over a cap).

Both work on one shape, a list of weeks: {"n", "phase", "anchor" (day number), "days": {day number: [session]}};
a session is {"discipline", "type", "amount", "minutes", "intensity"}. Template days are numbered from 0; plan
days are date ordinals.
"""
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from programs.profile import Profile
from programs.schema import Template

CONFIG = Path(__file__).resolve().parents[1] / "config"
EPS = 1e-9


@lru_cache(maxsize=1)
def safety() -> dict:
    return yaml.safe_load((CONFIG / "program_safety.yaml").read_text())


@lru_cache(maxsize=1)
def movements() -> set[str]:
    return set(yaml.safe_load((CONFIG / "movements.yaml").read_text())["movements"])


def _ref(name: str) -> tuple[str, str | None]:
    discipline, _, type_ = name.partition(".")
    return discipline, type_ or None


def _type(t: Template, s: dict):
    return t.session_type(s["discipline"], s["type"])


def is_recovery(st) -> bool:
    """An easy recovery session (not load): marked recovery AND easy. A "recovery" session above easy is load."""
    return bool(st and st.recovery and st.intensity <= safety()["recovery_max_intensity"])


def _load(t: Template, sessions: list[dict], discipline: str) -> float:
    """The week's load in one discipline: easy recovery sessions and the event itself don't count."""
    total = 0.0
    for s in sessions:
        st = _type(t, s)
        if s["discipline"] == discipline and st and not is_recovery(st) and not st.event:
            total += s.get("amount") or 0
    return total


def _all(week: dict) -> list[dict]:
    return [s for sessions in week["days"].values() for s in sessions]


def _unit(t: Template, discipline: str) -> str:
    return {"km": "km", "min": "min", "elevation_m": "m"}[t.disciplines[discipline].measure]


def _label(day: int, dated: bool) -> str:
    return date.fromordinal(day).strftime("%a %b %-d") if dated else f"day {day + 1}"


# --- the checks both share --------------------------------------------------------------------------------

def _growth(t: Template, weeks: list[dict], history: dict, where: str) -> list[str]:
    s = safety()
    errors = []
    for discipline in t.disciplines:
        loads = list(history.get(discipline, {}).get("weekly", [])) + [_load(t, _all(w), discipline) for w in weeks]
        offset = len(loads) - len(weeks)
        for i, w in enumerate(weeks):
            before = loads[max(0, offset + i - s["growth_lookback_weeks"]):offset + i]
            base = max(before, default=0)
            load = loads[offset + i]
            if base > 0 and load > base * (1 + s["weekly_growth_max"]) + EPS:
                errors.append(f"{where} week {w['n']}: {discipline} load {load:.1f} {_unit(t, discipline)} is "
                              f"{(load / base - 1) * 100:.0f}% over the highest of the 3 weeks before "
                              f"({base:.1f}); the limit is {s['weekly_growth_max'] * 100:.0f}%")
    return errors


def _long_growth(t: Template, weeks: list[dict], history: dict, start: int, dated: bool, where: str) -> list[str]:
    s = safety()
    days = s["long_session_lookback_days"]
    sessions = [(day, x) for w in weeks for day, xs in w["days"].items() for x in xs]
    errors = []
    for day, x in sessions:
        st = _type(t, x)
        if not (st and st.long) or not x.get("amount"):
            continue
        earlier = [y.get("amount") or 0 for d, y in sessions
                   if y["discipline"] == x["discipline"] and day - days <= d < day]
        if day - start < days:                         # their own sessions before the plan are still in range
            known = history.get(x["discipline"], {}).get("longest_30d")
            if known is None and day - start < 7:
                continue                               # the first week of a template: nothing before it to compare
            earlier.append(known or 0)
        base = max(earlier, default=0)
        if base > 0 and x["amount"] > base * (1 + s["long_session_growth_max"]) + EPS:
            errors.append(f"{where} {_label(day, dated)}: the long {x['discipline']} session "
                          f"({x['amount']:.1f} {_unit(t, x['discipline'])}) is "
                          f"{(x['amount'] / base - 1) * 100:.0f}% over the longest in the {days} days before "
                          f"({base:.1f}); the limit is {s['long_session_growth_max'] * 100:.0f}%")
    return errors


def _per_week(t: Template, weeks: list[dict], max_days: int, max_days_why: str, where: str) -> list[str]:
    errors = []
    rest_needed = max(t.shared.min_rest_days, safety()["min_rest_days_per_week"])
    for w in weeks:
        training = [d for d, xs in w["days"].items() if xs]
        hard = sum(1 for x in _all(w) if (_type(t, x) and _type(t, x).hard))
        if len(training) > 7 - rest_needed:
            errors.append(f"{where} week {w['n']}: {len(training)} training days leave fewer than "
                          f"{rest_needed} rest day(s)")
        if len(training) > max_days:
            errors.append(f"{where} week {w['n']}: {len(training)} training days, more than {max_days_why} "
                          f"({max_days})")
        if hard > t.shared.max_hard_per_week:
            errors.append(f"{where} week {w['n']}: {hard} hard sessions, more than {t.shared.max_hard_per_week}")
    return errors


def _taper(t: Template, weeks: list[dict], dated: bool, where: str) -> list[str]:
    if not (t.event and t.event.dated):
        return []
    events = [(day, x) for w in weeks for day, xs in w["days"].items() for x in xs
              if _type(t, x) and _type(t, x).event]
    if not events:
        return [f"{where}: the dated event is missing from the last week"]
    if t.event.taper_weeks < 1:
        return [f"{where}: a dated event needs a taper (taper_weeks is {t.event.taper_weeks})"]
    event_day = events[-1][0]
    errors = []
    for discipline in t.disciplines:
        peak = max((_load(t, _all(w), discipline) for w in weeks), default=0)
        before = _load(t, [x for w in weeks for d, xs in w["days"].items() if event_day - 6 <= d < event_day
                           for x in xs], discipline)
        if peak > 0 and before > peak * safety()["taper_max_of_peak"] + EPS:
            errors.append(f"{where}: no taper in {discipline}: the 6 days before the event hold {before:.1f} "
                          f"{_unit(t, discipline)}, over {safety()['taper_max_of_peak'] * 100:.0f}% of the peak "
                          f"week ({peak:.1f})")
    return errors


def _matches(t: Template, x: dict, name: str) -> bool:
    discipline, type_ = _ref(name)
    return x["discipline"] == discipline and (type_ is None or x["type"] == type_)


def _event_day(t: Template, weeks: list[dict]) -> int | None:
    return next((d for w in reversed(weeks) for d, xs in w["days"].items() for x in xs
                 if _type(t, x) and _type(t, x).event), None)


def _placement(t: Template, weeks: list[dict], dated: bool, where: str, variant: str) -> list[str]:
    """The variant's placement rules, on the days as placed."""
    anchors = [w["anchor"] for w in weeks]
    day_sessions = {d: xs for w in weeks for d, xs in w["days"].items()}
    event_day = _event_day(t, weeks)
    errors = []
    for rule in t.rules(variant):
        if rule.kind == "keep_away_from_event" and event_day is not None:
            for d in range(event_day - rule.days, event_day):
                if any(_matches(t, x, rule.session) for x in day_sessions.get(d, [])):
                    errors.append(f"{where} {_label(d, dated)}: {rule.session} within {rule.days} days of the event")
        for a in anchors:
            if rule.kind == "rest_before_long":
                for d in range(a - rule.days, a):
                    if day_sessions.get(d):
                        errors.append(f"{where} {_label(d, dated)}: a session the day before the long one "
                                      f"(rest_before_long)")
            elif rule.kind == "light_after_long":
                for d in range(a + 1, a + 1 + rule.days):
                    if any(not is_recovery(_type(t, x)) for x in day_sessions.get(d, [])):
                        errors.append(f"{where} {_label(d, dated)}: more than a light session the day after the "
                                      f"long one (light_after_long)")
            elif rule.kind == "keep_away_from_long":
                near = list(range(a - (rule.before or 0), a)) + list(range(a + 1, a + 1 + (rule.after or 0)))
                for d in near:
                    if any(_matches(t, x, rule.session) for x in day_sessions.get(d, [])):
                        errors.append(f"{where} {_label(d, dated)}: {rule.session} too close to the long session")
        if rule.kind == "pair_only_after":
            for d, xs in day_sessions.items():
                for i, x in enumerate(xs):
                    if _matches(t, x, rule.session) and any(not any(_matches(t, y, w) for w in rule.with_)
                                                            for y in xs[:i] + xs[i + 1:]
                                                            if not _matches(t, y, rule.session)):
                        errors.append(f"{where} {_label(d, dated)}: {rule.session} shares a day with a session "
                                      f"it may not follow")
    return list(dict.fromkeys(errors))


# --- templates --------------------------------------------------------------------------------------------

def _structure(t: Template) -> list[str]:
    errors = []
    known = movements()
    for name, d in t.disciplines.items():
        for type_name, st in d.session_types.items():
            for m in st.movements:
                if m not in known:
                    errors.append(f"{name}.{type_name}: unknown movement {m!r} (config/movements.yaml)")
        for from_type, to in d.alternatives.items():
            to_d, to_t = _ref(to)
            if from_type not in d.session_types or not t.session_type(to_d, to_t or ""):
                errors.append(f"{name}: alternative {from_type} -> {to} names a session type that doesn't exist")
    names = [n for n in t.shared.drop_order + t.shared.strength_types]
    names += [r.session for r in t.placement if r.session]
    for r in t.placement:
        names += r.with_
        if r.discipline and r.discipline not in t.disciplines:
            errors.append(f"placement {r.kind}: unknown discipline {r.discipline!r}")
        for v in r.variants:
            if v not in t.variants:
                errors.append(f"placement {r.kind}: unknown variant {v!r}")
    for n in names:
        discipline, type_ = _ref(n)
        if discipline not in t.disciplines or (type_ and type_ not in t.disciplines[discipline].session_types):
            errors.append(f"{n!r} names a session type that doesn't exist")
    for vname, v in t.variants.items():
        phases = [w.phase for w in v.weeks]
        runs = [p for i, p in enumerate(phases) if i == 0 or p != phases[i - 1]]
        if len(runs) != len(set(runs)):
            errors.append(f"{vname}: phases are not in order ({' → '.join(runs)})")
        for i, w in enumerate(v.weeks, 1):
            for s in w.sessions:
                if not t.session_type(s.discipline, s.type):
                    errors.append(f"{vname} week {i}: unknown session {s.discipline}.{s.type}")
                if not -6 <= s.at <= 0:
                    errors.append(f"{vname} week {i}: {s.discipline}.{s.type} at {s.at} is outside the week (-6..0)")
    return errors


def template_weeks(t: Template, variant: str) -> list[dict]:
    """The variant's weeks on day numbers: week 1 is days 0-6, its anchor day 6."""
    weeks = []
    for i, w in enumerate(t.variants[variant].weeks):
        anchor = 7 * i + 6
        days: dict[int, list[dict]] = {}
        for s in w.sessions:
            st = t.session_type(s.discipline, s.type)
            days.setdefault(anchor + s.at, []).append(
                {"discipline": s.discipline, "type": s.type, "amount": s.amount, "minutes": s.minutes,
                 "intensity": st.intensity if st else None})
        weeks.append({"n": i + 1, "phase": w.phase, "anchor": anchor, "days": days})
    return weeks


def validate_template(t: Template) -> list[str]:
    errors = _structure(t)
    if errors:
        return errors                       # the safety checks need a sound structure
    for name in t.variants:
        weeks = template_weeks(t, name)
        errors += _growth(t, weeks, {}, name)
        errors += _long_growth(t, weeks, {}, 0, False, name)
        errors += _per_week(t, weeks, t.shared.max_sessions_per_week, "the template's maximum", name)
        errors += _taper(t, weeks, False, name)
        errors += _placement(t, weeks, False, name, name)
    return errors


# --- plans ------------------------------------------------------------------------------------------------

# Paused sessions (a trip, an injury: modules/goals/adjust.py) are not load and not training days: a pause really
# means no training. A missed session still counts as the plan's design; the miss rules (drop, move, hold, repeat
# the week) deal with it; a missed session moved to another day counts once, on its new day ("rescheduled").
NOT_DONE = {"skipped", "rescheduled"}


def plan_weeks(days: list[dict]) -> list[dict]:
    """The generator's days ({"date", "week", "phase", "anchor", "sessions"}) as weeks on date ordinals, without
    paused (skipped) sessions."""
    weeks: dict[int, dict] = {}
    for d in days:
        w = weeks.setdefault(d["week"], {"n": d["week"], "phase": d["phase"], "anchor": None, "days": {}})
        sessions = [x for x in d["sessions"] if x.get("status") not in NOT_DONE]
        if sessions:
            w["days"][d["date"].toordinal()] = sessions
        if d.get("anchor"):
            w["anchor"] = d["date"].toordinal()
    return [weeks[n] for n in sorted(weeks)]


def _calendar_rest(days: list[dict], t: Template) -> list[str]:
    """Every full Monday-Sunday week inside the plan has its rest days."""
    rest_needed = max(t.shared.min_rest_days, safety()["min_rest_days_per_week"])
    by_date = {d["date"]: [x for x in d["sessions"] if x.get("status") not in NOT_DONE] for d in days}
    errors = []
    for d in days:
        if d["date"].weekday() == 0 and all(d["date"].toordinal() + i in {x.toordinal() for x in by_date}
                                           for i in range(7)):
            week = [date.fromordinal(d["date"].toordinal() + i) for i in range(7)]
            if sum(1 for x in week if not by_date[x]) < rest_needed:
                errors.append(f"the week of {d['date']:%a %b %-d} has no rest day")
    return errors


def minutes_of(x: dict, t: Template, profile: Profile) -> float | None:
    """A session's minutes: stated, or the amount in minutes, or km at their easy or race pace."""
    if x.get("minutes"):
        return x["minutes"]
    d = t.disciplines[x["discipline"]]
    if d.measure == "min":
        return x.get("amount")
    st = t.session_type(x["discipline"], x["type"])
    pace = profile.race_pace if st and st.pace == "race" else profile.easy_pace
    if d.measure == "km" and x.get("amount") and pace:
        return x["amount"] * pace
    return None


def _time_and_limits(days: list[dict], t: Template, profile: Profile) -> list[str]:
    errors = []
    for d in days:
        sessions = [x for x in d["sessions"] if x.get("status") not in NOT_DONE]
        if not sessions:
            continue
        label = f"{d['date']:%a %b %-d}"
        event = any(_type(t, x) and _type(t, x).event for x in sessions)
        limit = profile.longest_session_min if d.get("anchor") else profile.minutes_per_session
        times = [minutes_of(x, t, profile) for x in sessions]
        if None in times:
            missing = sessions[times.index(None)]
            errors.append(f"{label}: no way to time {missing['discipline']}.{missing['type']} (no pace known)")
        elif limit and not event and sum(times) > limit + EPS:
            errors.append(f"{label}: {sum(times):.0f} min of training, more than the {limit} min they have")
        for limit_ in (lim for lim in profile.limits if lim.active(d["date"])):
            for x in sessions:
                st = _type(t, x)
                hit = set(st.movements if st else []) & set(limit_.avoid)
                if hit:
                    errors.append(f"{label}: {x['discipline']}.{x['type']} uses {', '.join(sorted(hit))}, "
                                  f"which their {limit_.body_area} must avoid")
                if limit_.intensity_cap and (x.get("intensity") or 0) > limit_.intensity_cap:
                    errors.append(f"{label}: {x['discipline']}.{x['type']} at effort {x['intensity']}, over the "
                                  f"cap of {limit_.intensity_cap} for their {limit_.body_area}")
    return errors


def validate_plan(days: list[dict], profile: Profile, t: Template, variant: str) -> list[str]:
    weeks = plan_weeks(days)
    start = weeks[0]["anchor"] - 6 if weeks else 0           # the first program week's first day
    max_days = min(profile.days_per_week, t.shared.max_sessions_per_week)
    why = "their days a week" if profile.days_per_week <= t.shared.max_sessions_per_week else "the template's maximum"
    errors = (_growth(t, weeks, profile.history, "plan") + _long_growth(t, weeks, profile.history, start, True, "plan")
              + _per_week(t, weeks, max_days, why, "plan") + _calendar_rest(days, t) + _taper(t, weeks, True, "plan")
              + _placement(t, weeks, True, "plan", variant) + _time_and_limits(days, t, profile))
    return list(dict.fromkeys(errors))
