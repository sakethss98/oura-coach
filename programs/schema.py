"""The program template: what any goal's plan is made of, for any discipline (pydantic).

A template is data, never code: disciplines (each with a measure: km, minutes or elevation), their session
types, one or more level variants with their weeks, the rules for placing sessions, and the safety numbers
every plan must keep (programs/validate.py checks them). Nothing here is specific to one sport: a
half marathon, a triathlon and a day hike are all disciplines + session types + weeks.

Where a session lands: each week's sessions sit at an offset from that week's anchor day (the long session's
day, 0 = the anchor, -1 = the day before). The person's own anchor day (asked: "which day for the long one?")
moves the whole week. Sessions the template doesn't place (strength, from `strength_per_week`) are placed by
the generator by the `placement` rules.

Source and status: a library template (hand-written) or a drafted one (M5: drafted by the model). Only an
approved template generates a plan (programs/library.py); every template, either kind, must pass
validate_template first.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Measure = Literal["km", "min", "elevation_m"]
MI_TO_KM = 1.609344


class SessionType(BaseModel):
    label: str                    # what the person reads ("easy run", "upper-body strength")
    plan_as: str | None = None    # the workout type a day's plan uses for it (modules/training), None = not planned
    intensity: int = Field(ge=1, le=5)
    hard: bool = False            # counts toward the week's hard sessions
    long: bool = False            # the week's long session: the anchor, and the long-session growth check
    event: bool = False           # the dated event itself (exempt from the growth checks)
    recovery: bool = False        # an easy recovery session: not part of the weekly load
    movements: list[str] = []     # from config/movements.yaml
    pace: Literal["easy", "race", "none"] = "none"   # km sessions: minutes from their easy or race pace
    default_min: int | None = None  # the length of a session the generator adds (strength), in minutes


class Discipline(BaseModel):
    measure: Measure
    kinds: list[str]              # the neutral workout kinds that count as this discipline (modules/goals/signals.py)
    session_types: dict[str, SessionType]
    alternatives: dict[str, str] = {}   # a session type -> "discipline.type" to use when a limit rules it out


class SessionRef(BaseModel):
    discipline: str
    type: str
    amount: float | None = None   # in the discipline's measure (km sessions in the variant's unit)
    minutes: int | None = None    # stated by the template when the measure isn't time (a hike's hours)
    at: int = 0                   # days from the week's anchor (0 = the anchor day, -5 = five days before)


class Week(BaseModel):
    phase: str
    lighter: bool = False         # a planned lighter week (a test race, a cut-back)
    sessions: list[SessionRef]


class Variant(BaseModel):
    source: str                   # where the numbers come from, in words
    unit: Literal["km", "mi"] = "km"   # the unit of km-measured amounts, as published (converted on load)
    choose_when: dict[str, float] = {}  # all must hold (data first, else their answers); {} = the default variant
    weeks: list[Week]


class Placement(BaseModel):
    """One rule for where sessions go, by kind. `session` names a "discipline.type" (or "discipline" for any type)."""
    model_config = ConfigDict(populate_by_name=True)
    kind: Literal["rest_before_long", "light_after_long", "keep_away_from_long", "keep_away_from_event",
                  "pair_only_after", "prefer_days_before_long", "not_paired_with_longest"]
    variants: list[str] = []             # the variants it applies to ([] = all)
    session: str | None = None
    days: int | None = None              # rest_before_long / light_after_long / keep_away_from_event: how many days
    before: int | None = None            # keep_away_from_long: no such session this many days before the long one
    after: int | None = None             # ... nor this many days after it
    with_: list[str] = Field(default=[], alias="with")   # pair_only_after: it may follow only these on a day
    offsets: list[int] = []              # prefer_days_before_long: days before the long one, in order of preference
    discipline: str | None = None        # not_paired_with_longest: the longest non-long session of this discipline


class Shared(BaseModel):
    max_sessions_per_week: int = Field(ge=1, le=7)
    max_hard_per_week: int = Field(ge=0, le=7)
    min_rest_days: int = Field(default=1, ge=1, le=6)
    drop_order: list[str] = []           # "discipline.type" dropped first when they have fewer days a week
    strength_per_week: dict[str, int] = {}   # phase -> strength sessions the generator adds (a direction goal overrides)
    strength_types: list[str] = []       # the strength session types, the first preferred where the rules allow it


class Event(BaseModel):
    dated: bool = True
    measure: Measure
    amount: float                        # 21.1 (km), 1200 (elevation_m)
    taper_weeks: int = Field(default=1, ge=0)


class Template(BaseModel):
    name: str
    title: str
    source: Literal["library", "drafted"]
    status: Literal["approved", "pending_review"]
    note: str | None = None              # "Example only: illustrative numbers" and the like
    matches: list[str] = []              # words in their goal that pick this template ([] = never picked: an example)
    event: Event | None = None
    asks_user: list[str] = []            # questions beyond the goal-agnostic core (modules/goals/profile.py)
    disciplines: dict[str, Discipline]
    variants: dict[str, Variant]
    shared: Shared
    placement: list[Placement] = []
    progress_signals: list[Literal["workout", "manual_workout", "checkin", "milestone_test", "class_attended",
                                   "coach_verdict"]] = ["workout", "manual_workout", "checkin"]

    @model_validator(mode="after")
    def _in_km(self):
        """Amounts published in miles become km here, once, exactly (5 mi is 1.25 x 4 mi in km too)."""
        for variant in self.variants.values():
            if variant.unit == "mi":
                for week in variant.weeks:
                    for s in week.sessions:
                        d = self.disciplines.get(s.discipline)
                        if d and d.measure == "km" and s.amount is not None:
                            s.amount = s.amount * MI_TO_KM
                variant.unit = "km"
        return self

    def rules(self, variant: str) -> list[Placement]:
        """The placement rules for one variant: those naming it, and those naming none."""
        return [r for r in self.placement if not r.variants or variant in r.variants]

    def session_type(self, discipline: str, type_: str) -> SessionType | None:
        d = self.disciplines.get(discipline)
        return d.session_types.get(type_) if d else None
