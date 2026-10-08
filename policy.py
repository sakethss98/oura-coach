"""Hard rules from goals.yaml, enforced in code. The LLM proposes, this module decides.

`policy_check` is a pure function: it takes the LLM's plan and the facts it needs,
and returns the final plan plus a list of human-readable overrides.
"""

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4          # intensity >= this also counts as a hard day
WEEKEND = {5, 6}            # Saturday, Sunday


def is_hard(plan: dict | None) -> bool:
    if not plan:
        return False
    return plan["workout_type"] in HARD_TYPES or plan["intensity"] >= HARD_INTENSITY


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _downgrade_hard(plan: dict) -> None:
    plan["workout_type"] = "easy_run"
    plan["intensity"] = min(plan["intensity"], 3)


def _fit_slot(plan: dict, free_slots: list[list[str]], recommended: list[str] | None, overrides: list[str]) -> None:
    """Keep the time inside a free slot and the duration inside that slot."""
    if plan["workout_type"] == "rest":
        plan["time_slot"], plan["duration_min"] = "none", 0
        return
    containing = None
    try:
        start, end = (to_minutes(t) for t in plan["time_slot"].split("-"))
        containing = next(
            (s for s in free_slots if to_minutes(s[0]) <= start < end <= to_minutes(s[1])), None
        )
    except ValueError:
        pass
    if containing is None:
        if recommended is None:
            overrides.append(f"No free slot today for {plan['time_slot']}; changed to rest.")
            plan.update(workout_type="rest", intensity=1, time_slot="none", duration_min=0)
            return
        overrides.append(
            f"Time {plan['time_slot']} is not inside a free slot; moved to {recommended[0]}-{recommended[1]}."
        )
        start, end = to_minutes(recommended[0]), to_minutes(recommended[1])
    if plan["duration_min"] > end - start:
        overrides.append(f"Duration {plan['duration_min']} min does not fit the slot; cut to {end - start} min.")
        plan["duration_min"] = end - start
    end = start + plan["duration_min"]
    plan["time_slot"] = f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def policy_check(plan: dict, ctx: dict) -> tuple[dict, list[str]]:
    """Apply the hard rules.

    ctx keys:
      day (date), readiness (int | None), readiness_floor (int),
      band ("push" | "maintain" | "recover", from baseline.readiness_band),
      yesterday_plan (dict | None), recent_plans (plans from the previous 6 days, so today makes a 7-day window),
      max_hard_days_per_week (int), free_slots (list of ["HH:MM","HH:MM"]),
      recommended_slot (["HH:MM","HH:MM"] | None)
    """
    plan, overrides = dict(plan), []
    original = plan["workout_type"]

    readiness, floor = ctx["readiness"], ctx["readiness_floor"]
    if readiness is not None and readiness < floor and plan["workout_type"] not in {"walk", "rest"}:
        overrides.append(f"Readiness {readiness} < {floor}: {original} changed to walk.")
        plan["workout_type"] = "walk"
        plan["intensity"] = min(plan["intensity"], 2)

    band = ctx["band"]
    if band == "recover" and plan["workout_type"] not in {"walk", "rest"}:
        overrides.append(f"Band is recover: {plan['workout_type']} changed to walk.")
        plan["workout_type"] = "walk"
        plan["intensity"] = min(plan["intensity"], 2)
    if band == "maintain" and plan["workout_type"] == "HIIT":
        overrides.append("Band is maintain: no HIIT, changed to easy_run.")
        plan["workout_type"] = "easy_run"
    if band == "maintain" and plan["intensity"] > 3:
        overrides.append(f"Band is maintain: intensity {plan['intensity']} capped at 3.")
        plan["intensity"] = 3

    if is_hard(plan) and is_hard(ctx["yesterday_plan"]):
        overrides.append(
            f"Yesterday was a hard day ({ctx['yesterday_plan']['workout_type']}); "
            f"no back-to-back hard days, so {plan['workout_type']} changed to easy_run."
        )
        _downgrade_hard(plan)

    hard_days = sum(is_hard(p) for p in ctx["recent_plans"])
    if is_hard(plan) and hard_days >= ctx["max_hard_days_per_week"]:
        overrides.append(
            f"Already {hard_days} hard days in the last 7 (max {ctx['max_hard_days_per_week']}); "
            f"{plan['workout_type']} changed to easy_run."
        )
        _downgrade_hard(plan)

    if plan["workout_type"] == "long_run" and ctx["day"].weekday() not in WEEKEND:
        overrides.append("Long runs are for weekends; long_run changed to easy_run.")
        _downgrade_hard(plan)

    _fit_slot(plan, ctx["free_slots"], ctx["recommended_slot"], overrides)
    return plan, overrides

