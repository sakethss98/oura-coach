from datetime import date

from policy import is_hard, policy_check

WEDNESDAY = date(2026, 10, 7)
SATURDAY = date(2026, 10, 10)


def ctx(**changes):
    """Neutral context: good readiness, easy week, mornings and evenings free."""
    base = {
        "day": WEDNESDAY,
        "readiness": 82,
        "readiness_floor": 70,
        "band": "push",
        "yesterday_plan": None,
        "recent_plans": [],
        "max_hard_days_per_week": 2,
        "free_slots": [["06:00", "09:00"], ["17:00", "21:00"]],
        "recommended_slot": ["06:00", "09:00"],
    }
    base.update(changes)
    return base


def plan(workout_type="easy_run", intensity=2, time_slot="06:30-07:15", duration_min=45):
    return {"workout_type": workout_type, "intensity": intensity, "time_slot": time_slot,
            "duration_min": duration_min, "reasoning": "r", "nutrition_note": "n", "rules_applied": []}


def test_valid_plan_passes_unchanged():
    proposed = plan()
    final, overrides = policy_check(proposed, ctx())
    assert final == proposed
    assert overrides == []


def test_low_readiness_overrides_llm_hiit_to_walk():
    final, overrides = policy_check(plan("HIIT", 5), ctx(readiness=65))
    assert final["workout_type"] == "walk"
    assert final["intensity"] <= 2
    assert any("Readiness 65 < 70" in o for o in overrides)


def test_readiness_floor_comes_from_context():
    final, _ = policy_check(plan("HIIT", 5), ctx(readiness=72, readiness_floor=75))
    assert final["workout_type"] == "walk"


def test_rest_is_allowed_at_low_readiness():
    final, overrides = policy_check(plan("rest", 1, "none", 0), ctx(readiness=50))
    assert final["workout_type"] == "rest"
    assert overrides == []


def test_no_hard_day_after_hard_day():
    final, overrides = policy_check(plan("HIIT", 4), ctx(yesterday_plan=plan("long_run", 3)))
    assert final["workout_type"] == "easy_run"
    assert final["intensity"] <= 3
    assert any("back-to-back" in o for o in overrides)


def test_easy_day_after_hard_day_is_fine():
    final, overrides = policy_check(plan("easy_run", 2), ctx(yesterday_plan=plan("HIIT", 5)))
    assert final["workout_type"] == "easy_run"
    assert overrides == []


def test_weekly_hard_day_cap():
    recent = [plan("HIIT", 4), plan("easy_run", 2), plan("easy_run", 5)]  # 2 hard days
    final, overrides = policy_check(plan("HIIT", 4), ctx(recent_plans=recent))
    assert final["workout_type"] == "easy_run"
    assert any("hard days in the last 7" in o for o in overrides)


def test_hard_definition():
    assert is_hard(plan("HIIT", 2))
    assert is_hard(plan("long_run", 2))
    assert is_hard(plan("easy_run", 4))
    assert not is_hard(plan("easy_run", 3))
    assert not is_hard(None)


def test_long_run_only_on_weekends():
    weekday, overrides = policy_check(plan("long_run", 3, "06:00-07:30", 90), ctx())
    assert weekday["workout_type"] == "easy_run"
    assert overrides

    weekend, overrides = policy_check(plan("long_run", 3, "06:00-07:30", 90), ctx(day=SATURDAY))
    assert weekend["workout_type"] == "long_run"
    assert overrides == []


def test_slot_outside_free_time_is_moved_and_duration_clamped():
    final, overrides = policy_check(plan(time_slot="10:00-12:00", duration_min=240), ctx())
    assert final["time_slot"] == "06:00-09:00"
    assert final["duration_min"] == 180
    assert len(overrides) == 2


def test_rest_has_no_slot():
    final, _ = policy_check(plan("rest", 1, "06:00-07:00", 60), ctx())
    assert final["time_slot"] == "none"
    assert final["duration_min"] == 0


def test_recover_band_allows_only_walk_or_rest():
    final, overrides = policy_check(plan("easy_run", 3), ctx(band="recover"))
    assert final["workout_type"] == "walk"
    assert final["intensity"] <= 2
    assert any("recover" in o for o in overrides)

    final, overrides = policy_check(plan("rest", 1, "none", 0), ctx(band="recover"))
    assert final["workout_type"] == "rest"
    assert overrides == []


def test_maintain_band_blocks_hiit_and_caps_intensity():
    final, overrides = policy_check(plan("HIIT", 5), ctx(band="maintain"))
    assert final["workout_type"] == "easy_run"
    assert final["intensity"] == 3
    assert len(overrides) == 2

    final, overrides = policy_check(plan("easy_run", 3), ctx(band="maintain"))
    assert overrides == []


def test_push_band_allows_hiit():
    proposed = plan("HIIT", 5)
    final, overrides = policy_check(proposed, ctx(band="push"))
    assert final == proposed
    assert overrides == []
