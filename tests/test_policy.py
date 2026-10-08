from datetime import date

from policy import deciding_override, food_cap, is_hard, policy_check, why_sentence

WEDNESDAY = date(2026, 10, 7)
SATURDAY = date(2026, 10, 10)
CAPS = [
    {"within_min": 60, "min_calories": 400, "max_intensity": 2},
    {"within_min": 120, "min_calories": 400, "max_intensity": 3},
    {"within_min": 30, "min_calories": 150, "max_intensity": 3},
]


def band(level="push", oura=None, plain="Your readiness and HRV look good."):
    """An effective band; `oura` set = the check-in lowered it from that level."""
    return {"band": level, "oura_band": oura or level, "changed_by_checkin": oura is not None and oura != level,
            "reason": "test", "plain": plain}


def ctx(**changes):
    """Neutral context: good readiness, nothing done yet, easy week, free 06:00-09:00."""
    base = {
        "day": WEDNESDAY,
        "readiness": 82,
        "readiness_floor": 70,
        "band": band(),
        "worked_out_today": None,
        "yesterday_hard": None,
        "hard_days_prev6": 0,
        "max_hard_days_per_week": 2,
        "recent_food": [],
        "recent_food_caps": CAPS,
        "window": ["06:00", "09:00"],
    }
    base.update(changes)
    if isinstance(base["band"], str):
        base["band"] = band(base["band"])
    return base


def plan(workout_type="easy_run", intensity=2, time_slot="06:30-07:15", duration_min=45):
    return {"workout_type": workout_type, "intensity": intensity, "time_slot": time_slot,
            "duration_min": duration_min, "why": "coach why", "reasoning": "r", "food_before": "b",
            "food_after": "a", "follow_up": "", "exercises": [], "rules_applied": []}


def test_valid_plan_passes_unchanged():
    proposed = plan()
    final, overrides = policy_check(proposed, ctx())
    assert final == proposed
    assert overrides == []


# --- M2 rules ----------------------------------------------------------------

def test_low_readiness_overrides_llm_hiit_to_walk():
    final, overrides = policy_check(plan("HIIT", 5), ctx(readiness=65))
    assert final["workout_type"] == "walk"
    assert final["intensity"] <= 2
    assert any("Readiness 65 < 70" in o["text"] for o in overrides)


def test_readiness_floor_comes_from_context():
    final, _ = policy_check(plan("HIIT", 5), ctx(readiness=72, readiness_floor=75))
    assert final["workout_type"] == "walk"


def test_rest_and_mobility_are_allowed_at_low_readiness():
    final, overrides = policy_check(plan("rest", 1, "none", 0), ctx(readiness=50))
    assert final["workout_type"] == "rest"
    assert overrides == []
    final, overrides = policy_check(plan("mobility", 1), ctx(readiness=50))
    assert final["workout_type"] == "mobility"
    assert overrides == []


def test_no_hard_day_after_hard_day():
    final, overrides = policy_check(plan("HIIT", 4), ctx(yesterday_hard="75 min running at 07:00"))
    assert final["workout_type"] == "easy_run"
    assert final["intensity"] <= 3
    assert any("back-to-back" in o["text"] and "75 min running" in o["text"] for o in overrides)


def test_easy_day_after_hard_day_is_fine():
    final, overrides = policy_check(plan("easy_run", 2), ctx(yesterday_hard="40 min HIIT"))
    assert final["workout_type"] == "easy_run"
    assert overrides == []


def test_weekly_hard_day_cap_from_merged_days():
    final, overrides = policy_check(plan("HIIT", 4), ctx(hard_days_prev6=2))
    assert final["workout_type"] == "easy_run"
    assert any("hard days in the last 7" in o["text"] for o in overrides)
    final, overrides = policy_check(plan("HIIT", 4), ctx(hard_days_prev6=1))
    assert final["workout_type"] == "HIIT"


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


def test_recover_band_allows_only_light():
    final, overrides = policy_check(plan("easy_run", 3), ctx(band="recover"))
    assert final["workout_type"] == "walk"
    assert final["intensity"] <= 2
    assert any("recover" in o["text"] for o in overrides)

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


# --- rule a: already worked out today ---------------------------------------

def test_already_worked_out_allows_only_walk_or_mobility():
    final, overrides = policy_check(plan("HIIT", 5), ctx(worked_out_today="35 min running at 07:00"))
    assert final["workout_type"] == "walk"
    assert final["intensity"] == 2
    assert any("Already worked out today (35 min running" in o["text"] for o in overrides)


def test_already_worked_out_keeps_mobility_but_caps_intensity():
    final, overrides = policy_check(plan("mobility", 3), ctx(worked_out_today="yoga"))
    assert final["workout_type"] == "mobility"
    assert final["intensity"] == 2
    assert len(overrides) == 1


# --- rule c: recent food ----------------------------------------------------

def test_big_meal_in_last_hour_caps_at_2():
    final, overrides = policy_check(plan("easy_run", 3), ctx(recent_food=[{"minutes_ago": 45, "calories": 600}]))
    assert final["workout_type"] == "walk"
    assert final["intensity"] == 2
    assert any("Recent food" in o["text"] for o in overrides)


def test_big_meal_in_last_two_hours_caps_at_3():
    final, overrides = policy_check(plan("HIIT", 5), ctx(recent_food=[{"minutes_ago": 90, "calories": 600}]))
    assert final["workout_type"] == "easy_run"
    assert final["intensity"] == 3


def test_snack_in_last_30_min_caps_at_3():
    final, _ = policy_check(plan("easy_run", 4), ctx(recent_food=[{"minutes_ago": 20, "calories": 200}]))
    assert final["intensity"] == 3


def test_coffee_and_old_meals_do_not_cap():
    food = [{"minutes_ago": 10, "calories": 5}, {"minutes_ago": 180, "calories": 800}]
    final, overrides = policy_check(plan("HIIT", 5), ctx(recent_food=food))
    assert final["workout_type"] == "HIIT"
    assert overrides == []


def test_strictest_food_cap_wins():
    food = [{"minutes_ago": 20, "calories": 200}, {"minutes_ago": 50, "calories": 500}]
    assert food_cap(food, CAPS)[0] == 2


# --- plan window ------------------------------------------------------------

def test_time_outside_window_moves_to_window_start_and_duration_is_clamped():
    final, overrides = policy_check(plan(time_slot="10:00-14:00", duration_min=240), ctx())
    assert final["time_slot"] == "06:00-09:00"
    assert final["duration_min"] == 180
    assert len(overrides) == 2


def test_start_inside_window_is_kept():
    final, overrides = policy_check(plan(time_slot="07:30-08:15", duration_min=45), ctx())
    assert final["time_slot"] == "07:30-08:15"
    assert overrides == []


def test_no_window_means_rest():
    final, overrides = policy_check(plan("HIIT", 5), ctx(window=None))
    assert final["workout_type"] == "rest"
    assert final["time_slot"] == "none"
    assert any("No free time left" in o["text"] for o in overrides)


def test_rest_has_no_slot():
    final, _ = policy_check(plan("rest", 1, "06:00-07:00", 60), ctx())
    assert final["time_slot"] == "none"
    assert final["duration_min"] == 0


# --- check-in lowers the band (M3.1) ------------------------------------------

def test_checkin_lowered_to_maintain_caps_hiit_with_checkin_reason():
    lowered = band("maintain", oura="push", plain="You said energy 3, so no hard session today.")
    final, overrides = policy_check(plan("HIIT", 5), ctx(band=lowered))
    assert (final["workout_type"], final["intensity"]) == ("easy_run", 3)
    assert {o["rule"] for o in overrides} == {"checkin"}
    assert overrides[0]["plain"] == "You said energy 3, so no hard session today."


def test_checkin_lowered_to_recover_allows_only_light():
    lowered = band("recover", oura="push", plain="You said energy 2, so today is a recovery day.")
    final, overrides = policy_check(plan("easy_run", 3), ctx(band=lowered))
    assert (final["workout_type"], final["intensity"]) == ("walk", 2)
    assert all(o["rule"] == "checkin" for o in overrides)


def test_every_override_has_plain_text():
    _, overrides = policy_check(plan("long_run", 5, "10:00-14:00", 240),
                                ctx(band="maintain", yesterday_hard="x", hard_days_prev6=3))
    assert overrides and all(o["plain"] and "_" not in o["plain"] for o in overrides)


# --- strength --------------------------------------------------------------

def test_strength_becomes_walk_on_recover_day():
    final, _ = policy_check(plan("strength", 3), ctx(band="recover"))
    assert final["workout_type"] == "walk"


def test_strength_is_fine_on_maintain_day_and_keeps_type_when_capped():
    final, overrides = policy_check(plan("strength", 3), ctx(band="maintain"))
    assert final["workout_type"] == "strength" and overrides == []
    final, _ = policy_check(plan("strength", 4), ctx(yesterday_hard="40-min HIIT"))
    assert (final["workout_type"], final["intensity"]) == ("strength", 3)


# --- the why sentence ------------------------------------------------------

def test_why_is_the_coachs_when_code_changed_nothing():
    final, overrides = policy_check(plan("HIIT", 5), ctx())
    assert why_sentence(final, overrides, body_ok=True) == "coach why"


def test_why_uses_the_deciding_override():
    final, overrides = policy_check(plan("HIIT", 5), ctx(readiness=65))
    assert deciding_override(overrides)["rule"] == "readiness_floor"
    assert why_sentence(final, overrides, body_ok=False).startswith("Your readiness is 65")


def test_why_says_the_body_could_do_more_when_something_else_decided():
    final, overrides = policy_check(plan("HIIT", 5), ctx(worked_out_today="35-min run at 7:00am"))
    assert why_sentence(final, overrides, body_ok=True) == (
        "Your body could handle more, but you already trained today (35-min run at 7:00am), "
        "so just something light.")


def test_no_time_left_decides_even_for_a_rest_plan():
    final, overrides = policy_check(plan("rest", 1, "none", 0), ctx(window=None))
    assert why_sentence(final, overrides, body_ok=True) == (
        "Your body could handle more, but there's no free time left today.")
