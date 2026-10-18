"""Hard rules from goals.yaml, enforced in code. The LLM proposes, this module decides.

`policy_check` is a pure function: it takes a plan (the LLM's, or what the person asked for) and the
facts it needs, and returns the final plan plus a list of overrides. Each override is
{"rule", "text", "plain", "changes_type"}: `text` is technical (run.py, logs), `plain` is a coach
sentence for chat. A plan with several blocks (modules/training/blocks.py) has the type rules
applied to each block; the day is hard if any block is.
"""
from core.labels import workout_label
from modules.training.blocks import blocks_of, is_hard, with_blocks

LIGHT_TYPES = {"walk", "mobility", "rest"}
WEEKEND = {5, 6}            # Saturday, Sunday
MIN_BLOCK_MINUTES = 10      # a block cut shorter than this by the window is dropped
# Rules that decide for reasons other than how recovered the body is.
NON_BODY_RULES = {"checkin", "worked_out", "back_to_back", "weekly_cap", "long_run_weekday", "recent_food",
                  "no_time"}
# Rules that always apply, even when the person chose to skip the others.
TIME_RULES = {"window", "no_time"}


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _add(overrides: list[dict], rule: str, text: str, plain: str, changes_type: bool = False) -> None:
    overrides.append({"rule": rule, "text": text, "plain": plain, "changes_type": changes_type})


def _downgrade_hard(block: dict) -> str:
    """A hard block becomes moderate: strength keeps its type, everything else becomes an easy run."""
    before = block["workout_type"]
    if before != "strength":
        block["workout_type"] = "easy_run"
    block["intensity"] = min(block["intensity"], 3)
    return before


def _light_only(block: dict, overrides: list[dict], rule: str, reason: str, plain: str) -> None:
    """Walk, mobility or rest at intensity <= 2."""
    if block["workout_type"] not in LIGHT_TYPES:
        _add(overrides, rule, f"{reason}: {block['workout_type']} changed to walk.", plain, changes_type=True)
        block["workout_type"] = "walk"
    if block["intensity"] > 2:
        _add(overrides, rule, f"{reason}: intensity {block['intensity']} capped at 2.", plain)
        block["intensity"] = 2


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


def _fit_window(plan: dict, blocks: list[dict], window: list[str] | None, overrides: list[dict]) -> list[dict]:
    """Start inside the plan window (default: its start) and end before it closes. Too long: the last
    blocks are cut (a block left under 10 min is dropped, but never the first one)."""
    if window is None:
        _add(overrides, "no_time", "No free time left today; rest.", "There's no free time left today.",
             changes_type=any(b["workout_type"] != "rest" for b in blocks))
        plan.update(time_slot="none")
        return [{"workout_type": "rest", "intensity": 1, "duration_min": 0}]
    if all(b["workout_type"] == "rest" for b in blocks):
        plan["time_slot"] = "none"
        return [{**blocks[0], "duration_min": 0}]
    window_start, window_end = to_minutes(window[0]), to_minutes(window[1])
    try:
        start = to_minutes(plan["time_slot"].split("-")[0])
    except ValueError:
        start = None
    if start is None or not window_start <= start < window_end:
        _add(overrides, "window", f"Time {plan['time_slot']} is outside the plan window {window[0]}-{window[1]}; "
                                  f"starts at {window[0]}.", "Moved to start when you're free.")
        start = window_start
    total, available = sum(b["duration_min"] for b in blocks), window_end - start
    if total > available:
        _add(overrides, "window", f"Duration {total} min does not fit before {window[1]}; "
                                  f"cut to {available} min.", "Shortened to fit your free time.")
        kept, used = [], 0
        for b in blocks:
            room = available - used
            if kept and room < MIN_BLOCK_MINUTES:
                break
            kept.append({**b, "duration_min": min(b["duration_min"], room)})
            used += kept[-1]["duration_min"]
        blocks = kept
    plan["time_slot"] = f"{_hhmm(start)}-{_hhmm(start + sum(b['duration_min'] for b in blocks))}"
    return blocks


def _type_rules(blocks: list[dict], ctx: dict, overrides: list[dict]) -> None:
    """Every rule except the clock, in order. Changes `blocks` in place."""
    readiness, floor = ctx["readiness"], ctx["readiness_floor"]
    if readiness is not None and readiness < floor:
        usual = ctx.get("readiness_usual")
        low = "lower than usual" if usual is not None and readiness < usual else "on the low side"
        for b in blocks:
            _light_only(b, overrides, "readiness_floor", f"Readiness {readiness} < {floor}",
                        f"Readiness is {readiness} today, {low}, so I'd keep it light.")

    band = ctx["band"]
    by_checkin = band["changed_by_checkin"]
    rule = "checkin" if by_checkin else "band"
    who = f"Check-in lowers today to {band['band']}" if by_checkin else f"Band is {band['band']}"
    for b in blocks:
        if band["band"] == "recover":
            plain = band["plain"] if by_checkin else f"{band['plain']} So keep it light today."
            _light_only(b, overrides, rule, who, plain)
        if band["band"] == "maintain":
            plain = band["plain"] if by_checkin else f"{band['plain']} So no hard session today."
            if b["workout_type"] == "HIIT":
                _add(overrides, rule, f"{who}: no HIIT, changed to easy_run.", plain, changes_type=True)
                b["workout_type"] = "easy_run"
            if b["intensity"] > 3:
                _add(overrides, rule, f"{who}: intensity {b['intensity']} capped at 3.", plain)
                b["intensity"] = 3

    if ctx["worked_out_today"]:
        for b in blocks:
            _light_only(b, overrides, "worked_out", f"Already worked out today ({ctx['worked_out_today']})",
                        f"You already trained today ({ctx['worked_out_today']}), so just something light.")

    if ctx["yesterday_hard"]:
        for b in (b for b in blocks if is_hard(b)):
            before = _downgrade_hard(b)
            _add(overrides, "back_to_back",
                 f"Yesterday was a hard day ({ctx['yesterday_hard']}); no back-to-back hard days, "
                 f"so {before} changed to {b['workout_type']}, intensity <= 3.",
                 f"Yesterday was a hard day ({ctx['yesterday_hard']}), so today stays easier.",
                 changes_type=before != b["workout_type"])

    hard_days = ctx["hard_days_prev6"]
    if hard_days >= ctx["max_hard_days_per_week"]:
        for b in (b for b in blocks if is_hard(b)):
            before = _downgrade_hard(b)
            _add(overrides, "weekly_cap",
                 f"Already {hard_days} hard days in the last 7 (max {ctx['max_hard_days_per_week']}); "
                 f"{before} changed to {b['workout_type']}, intensity <= 3.",
                 f"You've already had {hard_days} hard days this week, so today stays easier.",
                 changes_type=before != b["workout_type"])

    if ctx["day"].weekday() not in WEEKEND:
        for b in (b for b in blocks if b["workout_type"] == "long_run"):
            _downgrade_hard(b)
            _add(overrides, "long_run_weekday", "Long runs are for weekends; long_run changed to easy_run.",
                 "Long runs are for the weekend, so it's an easy run today.", changes_type=True)

    cap = food_cap(ctx["recent_food"], ctx["recent_food_caps"])
    if cap:
        max_intensity, hit = cap
        reason = (f"Recent food: ate ~{hit['calories']:.0f} kcal {hit['minutes_ago']} min ago "
                  f"(>= {hit['min_calories']} kcal within {hit['within_min']} min)")
        plain = f"You ate about {hit['calories']:.0f} kcal {hit['minutes_ago']} minutes ago, so keep it light."
        for b in blocks:
            if max_intensity <= 2:
                _light_only(b, overrides, "recent_food", reason, plain)
                continue
            if b["workout_type"] == "HIIT":
                _add(overrides, "recent_food", f"{reason}; HIIT changed to easy_run.", plain, changes_type=True)
                b["workout_type"] = "easy_run"
            if b["intensity"] > max_intensity:
                _add(overrides, "recent_food", f"{reason}; intensity {b['intensity']} capped at {max_intensity}.",
                     plain)
                b["intensity"] = max_intensity


def _skipped(override: dict) -> dict:
    """An override the person chose to skip, as recorded."""
    plain = override["plain"]
    return {"rule": "user_choice", "skipped": override["rule"],
            "text": f"Skipped at their request ({override['rule']}): {override['text']}",
            "plain": "You chose this over: " + plain[0].lower() + plain[1:], "changes_type": False}


def policy_check(plan: dict, ctx: dict, user_choice: bool = False) -> tuple[dict, list[dict]]:
    """Apply the hard rules, in this order: readiness floor, band, already worked out, back-to-back,
    weekly cap, long run on weekdays, recent food, then the clock (plan window).

    user_choice=True: they were told what the rules would change and chose their plan anyway. Only
    the clock still applies; every other rule that would have fired is recorded as rule "user_choice".

    ctx keys:
      day (date), readiness (int | None), readiness_floor (int), readiness_usual (float | None: 7-day average),
      band (baseline.effective_band: {"band", "oura_band", "changed_by_checkin", "reason", "plain"}),
      worked_out_today (str | None: what already counts as today's workout),
      yesterday_hard (str | None: why yesterday was a hard day),
      hard_days_prev6 (int: hard days among the 6 days before today), max_hard_days_per_week (int),
      recent_food ([{minutes_ago, calories}]), recent_food_caps (goals.yaml list),
      window (["HH:MM", "HH:MM"] | None: from now until the next event)
    """
    plan, overrides = dict(plan), []
    blocks = blocks_of(plan)
    _type_rules(blocks, ctx, overrides)
    if user_choice:
        overrides = [_skipped(o) for o in overrides]
        blocks = blocks_of(plan)
    blocks = _fit_window(plan, blocks, ctx["window"], overrides)
    return with_blocks(plan, blocks), overrides


def conflicts(requested: dict, final: dict, overrides: list[dict]) -> list[dict]:
    """The overrides worth asking about before planning what they asked for: those that change a
    workout type, or that turn a requested hard day into an easy one. The clock never counts."""
    found = [o for o in overrides if o["rule"] not in TIME_RULES and o["changes_type"]]
    if not found and is_hard(requested) and not is_hard(final):
        found = [o for o in overrides if o["rule"] not in TIME_RULES]
    return found


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
