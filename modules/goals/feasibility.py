"""Can they get there, and from which week? Pure; nothing is written until they say yes.

- The variant: the first whose `choose_when` all hold (their data first, else their answers), else the default.
- The entry week: a week (not a lighter one, not the taper) from which the rest of the plan passes the
  validator's growth checks against what they did before: the validator's own rule, so a plan that starts there
  passes it. Of those, the earliest that still fits before the event, so they get the whole build.
- Weeks left until a dated event vs weeks needed from the entry week. Extra weeks repeat the entry week (holding
  steady); too few = not feasible, with the earliest date that would be.
"""
from datetime import date, timedelta

from programs.schema import Template
from programs.validate import _growth, _long_growth, safety, template_weeks

AT_LEAST = "_at_least"


def choose_variant(t: Template, facts: dict[str, float]) -> str:
    for name, v in t.variants.items():
        if v.choose_when and all(facts.get(key.removesuffix(AT_LEAST), 0) >= value
                                 for key, value in v.choose_when.items()):
            return name
    return next(name for name, v in t.variants.items() if not v.choose_when)


def _long(t: Template, week: dict, discipline: str) -> float:
    return max((x.get("amount") or 0 for xs in week["days"].values() for x in xs
                if x["discipline"] == discipline and t.session_type(x["discipline"], x["type"]).long), default=0)


def valid_entries(t: Template, variant: str, history: dict) -> list[int]:
    """The weeks they could start at (1-based): from that week on, the plan passes the validator's growth checks
    against what they did before (history). Never a lighter week or the taper. No history at all: week 1 only."""
    if not history:
        return [1]
    weeks = template_weeks(t, variant)
    taper = t.event.taper_weeks if t.event else 0
    found = []
    for w in weeks[:len(weeks) - taper] if taper else weeks:
        if t.variants[variant].weeks[w["n"] - 1].lighter:
            continue
        rest = weeks[w["n"] - 1:]
        if not _growth(t, rest, history, "") and not _long_growth(t, rest, history, rest[0]["anchor"] - 6, False, ""):
            found.append(w["n"])
    return found


def entry_week(t: Template, variant: str, history: dict, weeks_left: int | None = None) -> int | None:
    """Where the plan starts: the earliest valid week that still fits before the event (so they get the whole
    build), or the latest valid week when time is short. None = no valid week yet."""
    valid = valid_entries(t, variant, history)
    if not valid:
        return None
    if weeks_left is None:
        return valid[0]
    total = len(t.variants[variant].weeks)
    fitting = [n for n in valid if total - n + 1 <= weeks_left]
    return fitting[0] if fitting else valid[-1]


def build_to(t: Template, variant: str) -> dict[str, float]:
    """What week 1 asks for before they can start: per discipline, the longest recent session it needs."""
    first = template_weeks(t, variant)[0]
    grow = 1 + safety()["long_session_growth_max"]
    return {name: _long(t, first, name) / grow for name in t.disciplines if _long(t, first, name)}


def anchors(start: date, event: date | None, anchor_weekday: int, weeks: int) -> list[date]:
    """The anchor day of each program week. With a dated event the last anchor is the event day, and the
    training weeks before it end on their own long-session weekday; without one, they run from the first
    anchor on or after start + 6 days."""
    if event is None:
        first = start + timedelta(days=6)
        first += timedelta(days=(anchor_weekday - first.weekday()) % 7)
        return [first + timedelta(days=7 * i) for i in range(weeks)]
    last = event - timedelta(days=7)
    last -= timedelta(days=(last.weekday() - anchor_weekday) % 7)
    return [last - timedelta(days=7 * i) for i in range(weeks - 2, -1, -1)] + [event]


def weeks_until(start: date, event: date, anchor_weekday: int) -> int:
    """Program weeks that fit between start and the event (the event's week included)."""
    n = 1
    while anchors(start, event, anchor_weekday, n + 1)[0] - timedelta(days=6) >= start:
        n += 1
    return n


def assess(t: Template, variant: str, history: dict, start: date, event: date | None,
           anchor_weekday: int) -> dict:
    """{"variant", "entry_week", "weeks_needed", "weeks_left", "feasible", "extra_weeks", "earliest_event",
    "build_to"}. Not feasible: no entry week yet (build_to says what week 1 needs), or too few weeks."""
    weeks = len(t.variants[variant].weeks)
    left = weeks_until(start, event, anchor_weekday) if event else None
    entry = entry_week(t, variant, history, left)
    out = {"variant": variant, "entry_week": entry, "weeks_left": None, "extra_weeks": 0, "earliest_event": None,
           "build_to": None if entry else build_to(t, variant)}
    if entry is None:
        return {**out, "weeks_needed": None, "feasible": False}
    needed = weeks - entry + 1
    out["weeks_needed"] = needed
    if event is None:
        return {**out, "feasible": True}
    out["weeks_left"] = left
    if left >= needed:
        return {**out, "feasible": True, "extra_weeks": left - needed}
    return {**out, "feasible": False, "earliest_event": earliest_event(start, anchor_weekday, needed, event.weekday())}


def earliest_event(start: date, anchor_weekday: int, weeks: int, event_weekday: int) -> date:
    """The first event day (on its weekday) that leaves `weeks` program weeks from start, the event's week included."""
    first = anchors(start, None, anchor_weekday, max(weeks, 2))
    if weeks <= 1 or event_weekday == anchor_weekday:
        return first[weeks - 1] if weeks > 1 else first[0]
    return first[-2] + timedelta(days=7 + (event_weekday - anchor_weekday) % 7)


def predict_finish(runs: list[dict], race_km: float, target_minutes: float | None) -> dict:
    """{"predicted_minutes" (or None), "target_minutes", "within": True/False/None, "from"}: approximate."""
    from modules.goals.paces import predict
    found = predict(runs, race_km)
    if not found:
        return {"predicted_minutes": None, "target_minutes": target_minutes, "within": None, "from": None}
    within = None if target_minutes is None else found["minutes"] <= target_minutes
    return {"predicted_minutes": found["minutes"], "target_minutes": target_minutes, "within": within,
            "from": found["from"]}
