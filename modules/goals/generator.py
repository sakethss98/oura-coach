"""A template + a profile + a start (+ a dated event) -> every day of the plan. Pure, and generic: it knows sessions,
disciplines, anchors and the template's placement rules, never a sport.

1. Only an approved template that passes the safety checks makes a plan (programs/library.require_approved).
2. Weeks: the variant's weeks from the entry week on, the entry week repeated first for any extra weeks (holding
   steady). Each week ends on its anchor day: the long session's weekday they chose; the last week ends on the
   event day.
3. Template sessions land at their offsets; strength sessions are placed by the rules (after an easy session the
   rules allow, else on a free day while rest days remain; the preferred days first).
4. Fewer days a week than the template: sessions go in the template's drop order, then strength.
5. Limits: a session using a movement they must avoid becomes its alternative, else it's dropped; no effort over
   the cap. A day over their time loses its added strength session.
6. validate_plan on the result: any error is returned and nothing may be written.
"""
from datetime import date, timedelta

from programs.library import require_approved
from programs.profile import Profile
from programs.schema import Template
from programs.validate import minutes_of, validate_plan
from modules.goals.feasibility import anchors


def _ref(name: str) -> tuple[str, str | None]:
    discipline, _, type_ = name.partition(".")
    return discipline, type_ or None


def _matches(x: dict, name: str | None) -> bool:
    discipline, type_ = _ref(name or "")
    return x["discipline"] == discipline and (type_ is None or x["type"] == type_)


def _session(t: Template, discipline: str, type_: str, amount, minutes=None) -> dict:
    return {"discipline": discipline, "type": type_, "amount": amount, "minutes": minutes,
            "intensity": t.session_type(discipline, type_).intensity}


def _rule(week, kind: str, session: dict | None = None):
    """The week's variant rules of one kind (for this session, when given)."""
    return [r for r in week.rules if r.kind == kind and (session is None or _matches(session, r.session))]


class _Week:
    def __init__(self, n: int, tweek: int, phase: str, anchor: date, previous: date | None, rules: list,
                 event: date | None):
        self.n, self.tweek, self.phase, self.anchor, self.previous = n, tweek, phase, anchor, previous
        self.rules, self.event = rules, event
        self.days: dict[date, list[dict]] = {}
        self.time_short: list[dict] = []     # lifts dropped for time: what the day needed, what they have

    def window(self) -> list[date]:
        return [self.anchor - timedelta(days=6 - i) for i in range(7)]

    def training_days(self) -> int:
        return sum(1 for d in self.window() if self.days.get(d))


def _strength_ok(t: Template, week: _Week, day: date, x: dict, max_days: int) -> bool:
    """May strength session x go on this day? The template's placement rules, read generically."""
    on_day = week.days.get(day, [])
    if day == week.anchor or any(s["discipline"] == x["discipline"] for s in on_day):
        return False
    for r in _rule(week, "rest_before_long"):
        if week.anchor - timedelta(days=r.days) <= day < week.anchor:
            return False
    for r in _rule(week, "light_after_long"):
        if week.previous and week.previous < day <= week.previous + timedelta(days=r.days):
            return False
    for r in _rule(week, "keep_away_from_event", x):
        if week.event and week.event - timedelta(days=r.days) <= day < week.event:
            return False
    for r in _rule(week, "keep_away_from_long", x):
        if week.anchor - timedelta(days=r.before or 0) <= day < week.anchor:
            return False
        if week.previous and week.previous < day <= week.previous + timedelta(days=r.after or 0):
            return False
    for r in _rule(week, "pair_only_after", x):
        if any(not any(_matches(s, w) for w in r.with_) for s in on_day):
            return False
    for r in _rule(week, "not_paired_with_longest", x):
        others = [(s["amount"] or 0) for d in week.window() if d != week.anchor for s in week.days.get(d, [])
                  if s["discipline"] == r.discipline]
        longest = max(others, default=None)
        if longest and any(s["discipline"] == r.discipline and (s["amount"] or 0) == longest for s in on_day):
            return False
    if not on_day:                                     # a free day becomes a training day
        rest_needed = max(t.shared.min_rest_days, 1)
        if week.training_days() + 1 > min(max_days, 7 - rest_needed):
            return False
    return True


def _place_strength(t: Template, week: _Week, count: int, max_days: int) -> list[str]:
    """Strength sessions for the week, in the template's type order; returns what couldn't be placed."""
    types = t.shared.strength_types
    missed = []
    for k in range(count):
        placed = False
        for name in types[k % len(types):] + types[:k % len(types)]:
            discipline, type_ = _ref(name)
            st = t.session_type(discipline, type_)
            x = _session(t, discipline, type_, st.default_min)
            prefer = [week.anchor + timedelta(days=o) for r in _rule(week, "prefer_days_before_long", x)
                      for o in r.offsets]
            paired = [d for d in week.window() if week.days.get(d) and d not in prefer]
            free = [d for d in week.window() if not week.days.get(d) and d not in prefer]
            for day in prefer + paired + free:
                if week.anchor - timedelta(days=6) <= day <= week.anchor and _strength_ok(t, week, day, x, max_days):
                    week.days.setdefault(day, []).append(x)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            missed.append(f"week {week.n}: no day for strength session {k + 1}")
    return missed


def _drop_to_days(t: Template, week: _Week, max_days: int) -> list[str]:
    dropped = []
    order = t.shared.drop_order + [f"{_ref(n)[0]}" for n in t.shared.strength_types]
    for name in order:
        for day in sorted(week.days, reverse=True):
            if week.training_days() <= max_days:
                return dropped
            if len(week.days[day]) == 1 and _matches(week.days[day][0], name):
                dropped.append(f"week {week.n} {day:%a %b %-d}: {week.days[day][0]['discipline']}."
                               f"{week.days[day][0]['type']} (fewer days a week)")
                week.days[day] = []
    return dropped


def _cap_days(t: Template, week: _Week, discipline: str, most: int) -> list[str]:
    """At most `most` days a week with this discipline (a direction rule): the drop order first, then the shortest;
    never the long session or the event."""
    def days_with():
        return [d for d in week.window() if any(x["discipline"] == discipline for x in week.days.get(d, []))]
    dropped = []
    order = [n for n in t.shared.drop_order if _ref(n)[0] == discipline]
    while len(days_with()) > most:
        candidates = [(d, x) for d in days_with() for x in week.days[d] if x["discipline"] == discipline
                      and not (t.session_type(discipline, x["type"]).long or t.session_type(discipline, x["type"]).event)]
        if not candidates:
            break
        ranked = sorted(candidates, key=lambda c: (next((i for i, n in enumerate(order) if _matches(c[1], n)),
                                                         len(order)), c[1]["amount"] or 0))
        day, x = ranked[0]
        week.days[day] = [y for y in week.days[day] if y is not x]      # a lift after it stays, on its own
        dropped.append(f"week {week.n} {day:%a %b %-d}: {discipline}.{x['type']} (at most {most} {discipline} days)")
    return dropped


def apply_limits(t: Template, profile: Profile, week) -> list[str]:
    """Sessions a limit rules out become their alternative, else they go; no effort over the cap. `week.days` =
    {date: [session]} (changed in place); returns the changes in words."""
    changes = []
    for day, sessions in week.days.items():
        active = [lim for lim in profile.limits if lim.active(day)]
        if not active:
            continue
        avoid = {m for lim in active for m in lim.avoid}
        caps = [lim.intensity_cap for lim in active if lim.intensity_cap]
        kept = []
        for x in sessions:
            st = t.session_type(x["discipline"], x["type"])
            if set(st.movements) & avoid:
                alt = t.disciplines[x["discipline"]].alternatives.get(x["type"])
                alt_d, alt_t = _ref(alt) if alt else (None, None)
                alt_st = t.session_type(alt_d, alt_t) if alt else None
                if alt_st and not set(alt_st.movements) & avoid:
                    same = t.disciplines[alt_d].measure == t.disciplines[x["discipline"]].measure
                    amount = x["amount"] if same else (alt_st.default_min or minutes_of(x, t, profile))
                    kept.append(_session(t, alt_d, alt_t, amount, x["minutes"] if same else None))
                    changes.append(f"{day:%a %b %-d}: {x['discipline']}.{x['type']} -> {alt} (their "
                                   f"{active[0].body_area})")
                else:
                    changes.append(f"{day:%a %b %-d}: {x['discipline']}.{x['type']} dropped (their "
                                   f"{active[0].body_area})")
                continue
            kept.append(x)
        for x in kept:
            if caps:
                x["intensity"] = min(x["intensity"], min(caps))
        week.days[day] = kept
    return changes


def _fit_time(t: Template, profile: Profile, week: _Week) -> list[str]:
    """A day over their time loses its added strength session (the template's own sessions stay)."""
    dropped = []
    for day, sessions in week.days.items():
        limit = profile.longest_session_min if day == week.anchor else profile.minutes_per_session
        if not limit or len(sessions) < 2:
            continue
        total = sum(minutes_of(x, t, profile) or 0 for x in sessions)
        if total > limit:
            lifts = [x for x in sessions if any(_matches(x, n) for n in t.shared.strength_types)]
            if lifts:
                sessions.remove(lifts[-1])
                week.time_short.append({"needed_min": round(total), "limit_min": limit})
                dropped.append(f"{day:%a %b %-d}: {lifts[-1]['discipline']}.{lifts[-1]['type']} dropped "
                               f"(over their {limit} min)")
    return dropped


def generate(t: Template, variant: str, profile: Profile, start: date, event: date | None = None,
             entry_week: int = 1, extra_weeks: int = 0, strength_per_week: dict[str, int] | None = None,
             skip_weeks: tuple[int, ...] = (), max_days: dict[str, int] | None = None) -> dict:
    """{"days": [{"date", "week" (the plan's week), "tweek" (the template's week), "phase", "anchor", "sessions"}],
    "changes": [what code changed and why], "strength_short": [{week, phase, wanted, placed, time}] for weeks
    with fewer strength sessions than asked (time = {needed_min, limit_min} when a lift didn't fit their time),
    "errors": validate_plan's (non-empty = don't write it)}."""
    require_approved(t)
    template_weeks = t.variants[variant].weeks
    tweeks = [entry_week] * extra_weeks + [n for n in range(entry_week, len(template_weeks) + 1) if n not in skip_weeks]
    weeks = [template_weeks[n - 1] for n in tweeks]
    rules = t.rules(variant)
    days_of = anchors(start, event, profile.anchor_weekday, len(weeks))
    discipline_days = max_days
    max_days = min(profile.days_per_week, t.shared.max_sessions_per_week)
    per_phase = {**t.shared.strength_per_week, **(strength_per_week or {})}
    built, changes, short = [], [], []
    for i, (w, anchor) in enumerate(zip(weeks, days_of)):
        week = _Week(i + 1, tweeks[i], w.phase, anchor, days_of[i - 1] if i else None, rules, event)
        for s in w.sessions:
            week.days.setdefault(anchor + timedelta(days=s.at), []).append(
                _session(t, s.discipline, s.type, s.amount, s.minutes))
        changes += _drop_to_days(t, week, max_days)
        for discipline, most in (discipline_days or {}).items():
            changes += _cap_days(t, week, discipline, most)
        wanted = per_phase.get(w.phase, 0)
        changes += _place_strength(t, week, wanted, max_days)
        changes += apply_limits(t, profile, week)
        changes += _fit_time(t, profile, week)
        placed = sum(1 for xs in week.days.values() for x in xs
                     if any(_matches(x, n) for n in t.shared.strength_types))
        if placed < wanted:
            short.append({"week": week.n, "phase": w.phase, "wanted": wanted, "placed": placed,
                          "time": week.time_short[0] if week.time_short else None})
        built.append(week)
    days = []
    first = min(start, built[0].anchor - timedelta(days=6))
    week_of = {}
    for week in built:
        for d in week.window():
            week_of[d] = week
    for k in range((built[-1].anchor - first).days + 1):
        day = first + timedelta(days=k)
        week = week_of.get(day) or next(w for w in built if w.anchor >= day)
        days.append({"date": day, "week": week.n, "tweek": week.tweek, "phase": week.phase,
                     "anchor": day == week.anchor, "sessions": list(week.days.get(day, [])) if day in week_of else []})
    return {"days": days, "changes": changes, "strength_short": short,
            "errors": validate_plan(days, profile, t, variant)}
