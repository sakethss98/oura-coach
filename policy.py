"""Hard rules from goals.yaml, enforced in code. The LLM proposes, this module decides.

`policy_check` is a pure function: it takes the LLM's plan and the facts it needs,
and returns the final plan plus a list of human-readable overrides.
"""

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4          # intensity >= this also counts as a hard day
LIGHT_TYPES = {"walk", "mobility", "rest"}
WEEKEND = {5, 6}            # Saturday, Sunday


def is_hard(plan: dict | None) -> bool:
    if not plan:
        return False
    return plan["workout_type"] in HARD_TYPES or plan["intensity"] >= HARD_INTENSITY


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _downgrade_hard(plan: dict) -> None:
    plan["workout_type"] = "easy_run"
    plan["intensity"] = min(plan["intensity"], 3)


def _light_only(plan: dict, overrides: list[str], reason: str) -> None:
    """Walk, mobility or rest at intensity <= 2."""
    if plan["workout_type"] not in LIGHT_TYPES:
        overrides.append(f"{reason}: {plan['workout_type']} changed to walk.")
        plan["workout_type"] = "walk"
    if plan["intensity"] > 2:
        overrides.append(f"{reason}: intensity {plan['intensity']} capped at 2.")
        plan["intensity"] = 2


def food_cap(recent_food: list[dict], caps: list[dict]) -> tuple[int, str] | None:
    """The strictest intensity cap triggered by recent food, with the reason, or None."""
    hits = []
    for cap in caps:
        for f in recent_food:
            if f["minutes_ago"] <= cap["within_min"] and f["calories"] >= cap["min_calories"]:
                hits.append((cap["max_intensity"],
                             f"ate ~{f['calories']:.0f} kcal {f['minutes_ago']} min ago "
                             f"(>= {cap['min_calories']} kcal within {cap['within_min']} min)"))
                break
    return min(hits) if hits else None


def _fit_window(plan: dict, window: list[str] | None, overrides: list[str]) -> None:
    """Start inside the plan window (default: its start) and end before it closes."""
    if plan["workout_type"] == "rest":
        plan["time_slot"], plan["duration_min"] = "none", 0
        return
    if window is None:
        overrides.append("No free time left today; changed to rest.")
        plan.update(workout_type="rest", intensity=1, time_slot="none", duration_min=0)
        return
    window_start, window_end = to_minutes(window[0]), to_minutes(window[1])
    try:
        start = to_minutes(plan["time_slot"].split("-")[0])
    except ValueError:
        start = None
    if start is None or not window_start <= start < window_end:
        overrides.append(f"Time {plan['time_slot']} is outside the plan window {window[0]}-{window[1]}; "
                         f"starts at {window[0]}.")
        start = window_start
    if plan["duration_min"] > window_end - start:
        overrides.append(f"Duration {plan['duration_min']} min does not fit before {window[1]}; "
                         f"cut to {window_end - start} min.")
        plan["duration_min"] = window_end - start
    plan["time_slot"] = f"{_hhmm(start)}-{_hhmm(start + plan['duration_min'])}"


def policy_check(plan: dict, ctx: dict) -> tuple[dict, list[str]]:
    """Apply the hard rules, in this order.

    ctx keys:
      day (date), readiness (int | None), readiness_floor (int),
      band ("push" | "maintain" | "recover", from baseline.readiness_band),
      worked_out_today (str | None: what already counts as today's workout),
      yesterday_hard (str | None: why yesterday was a hard day),
      hard_days_prev6 (int: hard days among the 6 days before today), max_hard_days_per_week (int),
      recent_food ([{minutes_ago, calories}]), recent_food_caps (goals.yaml list),
      window (["HH:MM", "HH:MM"] | None: from now until the next event)
    """
    plan, overrides = dict(plan), []

    readiness, floor = ctx["readiness"], ctx["readiness_floor"]
    if readiness is not None and readiness < floor:
        _light_only(plan, overrides, f"Readiness {readiness} < {floor}")

    band = ctx["band"]
    if band == "recover":
        _light_only(plan, overrides, "Band is recover")
    if band == "maintain" and plan["workout_type"] == "HIIT":
        overrides.append("Band is maintain: no HIIT, changed to easy_run.")
        plan["workout_type"] = "easy_run"
    if band == "maintain" and plan["intensity"] > 3:
        overrides.append(f"Band is maintain: intensity {plan['intensity']} capped at 3.")
        plan["intensity"] = 3

    if ctx["worked_out_today"]:
        _light_only(plan, overrides, f"Already worked out today ({ctx['worked_out_today']})")

    if is_hard(plan) and ctx["yesterday_hard"]:
        overrides.append(
            f"Yesterday was a hard day ({ctx['yesterday_hard']}); "
            f"no back-to-back hard days, so {plan['workout_type']} changed to easy_run."
        )
        _downgrade_hard(plan)

    hard_days = ctx["hard_days_prev6"]
    if is_hard(plan) and hard_days >= ctx["max_hard_days_per_week"]:
        overrides.append(
            f"Already {hard_days} hard days in the last 7 (max {ctx['max_hard_days_per_week']}); "
            f"{plan['workout_type']} changed to easy_run."
        )
        _downgrade_hard(plan)

    if plan["workout_type"] == "long_run" and ctx["day"].weekday() not in WEEKEND:
        overrides.append("Long runs are for weekends; long_run changed to easy_run.")
        _downgrade_hard(plan)

    cap = food_cap(ctx["recent_food"], ctx["recent_food_caps"])
    if cap:
        max_intensity, reason = cap
        if max_intensity <= 2:
            _light_only(plan, overrides, f"Recent food: {reason}")
        else:
            if plan["workout_type"] == "HIIT":
                overrides.append(f"Recent food: {reason}; HIIT changed to easy_run.")
                plan["workout_type"] = "easy_run"
            if plan["intensity"] > max_intensity:
                overrides.append(f"Recent food: {reason}; intensity {plan['intensity']} capped at {max_intensity}.")
                plan["intensity"] = max_intensity

    _fit_window(plan, ctx["window"], overrides)
    return plan, overrides
