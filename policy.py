"""Hard rules from goals.yaml, enforced in code. The LLM proposes, this module decides.

`policy_check` is a pure function: it takes the LLM's plan and the facts it needs,
and returns the final plan plus a list of overrides. Each override is
{"rule", "text", "plain", "changes_type"}: `text` is technical (run.py, logs),
`plain` is a coach sentence for chat.
"""
from labels import workout_label

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4          # intensity >= this also counts as a hard day
LIGHT_TYPES = {"walk", "mobility", "rest"}
WEEKEND = {5, 6}            # Saturday, Sunday
# Rules that decide for reasons other than how recovered the body is.
NON_BODY_RULES = {"checkin", "worked_out", "back_to_back", "weekly_cap", "long_run_weekday", "recent_food",
                  "no_time"}


def is_hard(plan: dict | None) -> bool:
    if not plan:
        return False
    return plan["workout_type"] in HARD_TYPES or plan["intensity"] >= HARD_INTENSITY


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _add(overrides: list[dict], rule: str, text: str, plain: str, changes_type: bool = False) -> None:
    overrides.append({"rule": rule, "text": text, "plain": plain, "changes_type": changes_type})


def _downgrade_hard(plan: dict) -> str:
    """A hard day becomes moderate: strength keeps its type, everything else becomes an easy run."""
    before = plan["workout_type"]
    if before != "strength":
        plan["workout_type"] = "easy_run"
    plan["intensity"] = min(plan["intensity"], 3)
    return before


def _light_only(plan: dict, overrides: list[dict], rule: str, reason: str, plain: str) -> None:
    """Walk, mobility or rest at intensity <= 2."""
    if plan["workout_type"] not in LIGHT_TYPES:
        _add(overrides, rule, f"{reason}: {plan['workout_type']} changed to walk.", plain, changes_type=True)
        plan["workout_type"] = "walk"
    if plan["intensity"] > 2:
        _add(overrides, rule, f"{reason}: intensity {plan['intensity']} capped at 2.", plain)
        plan["intensity"] = 2


def food_cap(recent_food: list[dict], caps: list[dict]) -> tuple[int, dict] | None:
    """The strictest intensity cap triggered by recent food, with the entry that triggered it."""
    hits = []
    for i, cap in enumerate(caps):
        for f in recent_food:
            if f["minutes_ago"] <= cap["within_min"] and f["calories"] >= cap["min_calories"]:
                hits.append((cap["max_intensity"], i, {**f, **cap}))
                break
    if not hits:
        return None
    max_intensity, _, hit = min(hits, key=lambda h: (h[0], h[1]))
    return max_intensity, hit


def _fit_window(plan: dict, window: list[str] | None, overrides: list[dict]) -> None:
    """Start inside the plan window (default: its start) and end before it closes."""
    if window is None:
        _add(overrides, "no_time", "No free time left today; rest.", "There's no free time left today.",
             changes_type=plan["workout_type"] != "rest")
        plan.update(workout_type="rest", intensity=1, time_slot="none", duration_min=0)
        return
    if plan["workout_type"] == "rest":
        plan["time_slot"], plan["duration_min"] = "none", 0
        return
    window_start, window_end = to_minutes(window[0]), to_minutes(window[1])
    try:
        start = to_minutes(plan["time_slot"].split("-")[0])
    except ValueError:
        start = None
    if start is None or not window_start <= start < window_end:
        _add(overrides, "window", f"Time {plan['time_slot']} is outside the plan window {window[0]}-{window[1]}; "
                                  f"starts at {window[0]}.", "Moved to start when you're free.")
        start = window_start
    if plan["duration_min"] > window_end - start:
        _add(overrides, "window", f"Duration {plan['duration_min']} min does not fit before {window[1]}; "
                                  f"cut to {window_end - start} min.", "Shortened to fit your free time.")
        plan["duration_min"] = window_end - start
    plan["time_slot"] = f"{_hhmm(start)}-{_hhmm(start + plan['duration_min'])}"


def policy_check(plan: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """Apply the hard rules, in this order.

    ctx keys:
      day (date), readiness (int | None), readiness_floor (int),
      band (baseline.effective_band: {"band", "oura_band", "changed_by_checkin", "reason", "plain"}),
      worked_out_today (str | None: what already counts as today's workout),
      yesterday_hard (str | None: why yesterday was a hard day),
      hard_days_prev6 (int: hard days among the 6 days before today), max_hard_days_per_week (int),
      recent_food ([{minutes_ago, calories}]), recent_food_caps (goals.yaml list),
      window (["HH:MM", "HH:MM"] | None: from now until the next event)
    """
    plan, overrides = dict(plan), []

    readiness, floor = ctx["readiness"], ctx["readiness_floor"]
    if readiness is not None and readiness < floor:
        _light_only(plan, overrides, "readiness_floor", f"Readiness {readiness} < {floor}",
                    f"Your readiness is {readiness}, below your floor of {floor}, so keep it light.")

    band = ctx["band"]
    by_checkin = band["changed_by_checkin"]
    rule = "checkin" if by_checkin else "band"
    who = f"Check-in lowers today to {band['band']}" if by_checkin else f"Band is {band['band']}"
    if band["band"] == "recover":
        plain = band["plain"] if by_checkin else f"{band['plain']} So keep it light today."
        _light_only(plan, overrides, rule, who, plain)
    if band["band"] == "maintain":
        plain = band["plain"] if by_checkin else f"{band['plain']} So no hard session today."
        if plan["workout_type"] == "HIIT":
            _add(overrides, rule, f"{who}: no HIIT, changed to easy_run.", plain, changes_type=True)
            plan["workout_type"] = "easy_run"
        if plan["intensity"] > 3:
            _add(overrides, rule, f"{who}: intensity {plan['intensity']} capped at 3.", plain)
            plan["intensity"] = 3

    if ctx["worked_out_today"]:
        _light_only(plan, overrides, "worked_out", f"Already worked out today ({ctx['worked_out_today']})",
                    f"You already trained today ({ctx['worked_out_today']}), so just something light.")

    if is_hard(plan) and ctx["yesterday_hard"]:
        before = _downgrade_hard(plan)
        _add(overrides, "back_to_back",
             f"Yesterday was a hard day ({ctx['yesterday_hard']}); no back-to-back hard days, "
             f"so {before} changed to {plan['workout_type']}, intensity <= 3.",
             f"Yesterday was a hard day ({ctx['yesterday_hard']}), so today stays easier.",
             changes_type=before != plan["workout_type"])

    hard_days = ctx["hard_days_prev6"]
    if is_hard(plan) and hard_days >= ctx["max_hard_days_per_week"]:
        before = _downgrade_hard(plan)
        _add(overrides, "weekly_cap",
             f"Already {hard_days} hard days in the last 7 (max {ctx['max_hard_days_per_week']}); "
             f"{before} changed to {plan['workout_type']}, intensity <= 3.",
             f"You've already had {hard_days} hard days this week, so today stays easier.",
             changes_type=before != plan["workout_type"])

    if plan["workout_type"] == "long_run" and ctx["day"].weekday() not in WEEKEND:
        _downgrade_hard(plan)
        _add(overrides, "long_run_weekday", "Long runs are for weekends; long_run changed to easy_run.",
             "Long runs are for the weekend, so it's an easy run today.", changes_type=True)

    cap = food_cap(ctx["recent_food"], ctx["recent_food_caps"])
    if cap:
        max_intensity, hit = cap
        reason = (f"Recent food: ate ~{hit['calories']:.0f} kcal {hit['minutes_ago']} min ago "
                  f"(>= {hit['min_calories']} kcal within {hit['within_min']} min)")
        plain = f"You ate about {hit['calories']:.0f} kcal {hit['minutes_ago']} minutes ago, so keep it light."
        if max_intensity <= 2:
            _light_only(plan, overrides, "recent_food", reason, plain)
        else:
            if plan["workout_type"] == "HIIT":
                _add(overrides, "recent_food", f"{reason}; HIIT changed to easy_run.", plain, changes_type=True)
                plan["workout_type"] = "easy_run"
            if plan["intensity"] > max_intensity:
                _add(overrides, "recent_food", f"{reason}; intensity {plan['intensity']} capped at {max_intensity}.",
                     plain)
                plan["intensity"] = max_intensity

    _fit_window(plan, ctx["window"], overrides)
    return plan, overrides


# --- turning the result into words ------------------------------------------

def deciding_override(overrides: list[dict]) -> dict | None:
    """The override that decided the final workout: the last one that changed its type
    (or "no free time", which always decides)."""
    deciding = [o for o in overrides if o["changes_type"] or o["rule"] == "no_time"]
    return deciding[-1] if deciding else None


def body_could_do_more(oura_band: str, readiness: int | None, readiness_floor: int) -> bool:
    return oura_band != "recover" and (readiness is None or readiness >= readiness_floor)


def why_sentence(plan: dict, overrides: list[dict], body_ok: bool) -> str:
    """The one-line "Why" for chat: the coach's sentence, or code's when code decided."""
    decider = deciding_override(overrides)
    if decider is None:
        return plan["why"]
    if body_ok and decider["rule"] in NON_BODY_RULES:
        plain = decider["plain"]
        return "Your body could handle more, but " + plain[0].lower() + plain[1:]
    return decider["plain"]


def label_of(plan: dict) -> str:
    return workout_label(plan["workout_type"], plan.get("split_day"))
