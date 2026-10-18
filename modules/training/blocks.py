"""A plan is one or more blocks done back to back ("20 min run, then 30 min strength"). Pure functions.

A single-block plan has no `blocks` key: its top-level workout_type / intensity / duration_min are the block.
A plan with several blocks keeps them in `blocks`, in order, and its top-level fields are a summary
(the hardest block's type, the highest intensity, the total minutes), so code that only needs
"what kind of day is this" keeps working.
"""
from datetime import date, datetime, time, timedelta

HARD_TYPES = {"HIIT", "long_run"}
HARD_INTENSITY = 4          # intensity >= this also counts as a hard day
# What code assumes when the length or effort wasn't given: a requested block before the coach fills
# in details, or a workout added in chat without minutes.
DEFAULT_INTENSITY = {"HIIT": 5, "long_run": 4, "easy_run": 3, "strength": 3, "walk": 2, "mobility": 1}
DEFAULT_MINUTES = {"HIIT": 30, "long_run": 75, "easy_run": 40, "strength": 45, "walk": 30, "mobility": 20,
                   "yoga": 30, "other": 30}


def default_minutes(workout_type: str) -> int:
    return DEFAULT_MINUTES.get(workout_type, 30)
BLOCK_KEYS = ("workout_type", "intensity", "duration_min")


def is_hard(plan: dict | None) -> bool:
    """A block, or a whole plan (hard if any block is)."""
    if not plan:
        return False
    if len(plan.get("blocks") or []) > 1:
        return any(is_hard(b) for b in plan["blocks"])
    return plan["workout_type"] in HARD_TYPES or (plan.get("intensity") or 0) >= HARD_INTENSITY


def blocks_of(plan: dict | None) -> list[dict]:
    """The plan's blocks in order (a single-block plan gives one block built from its top-level fields)."""
    if not plan:
        return []
    if plan.get("blocks"):
        return [{k: b[k] for k in BLOCK_KEYS} for b in plan["blocks"]]
    return [{k: plan.get(k) for k in BLOCK_KEYS}]


def types_of(plan: dict | None) -> list[str]:
    return [b["workout_type"] for b in blocks_of(plan)]


def _merge_neighbours(blocks: list[dict]) -> list[dict]:
    """Two blocks of the same type in a row become one (e.g. both changed to walk)."""
    merged = []
    for b in blocks:
        if merged and merged[-1]["workout_type"] == b["workout_type"]:
            merged[-1]["duration_min"] += b["duration_min"]
            merged[-1]["intensity"] = max(merged[-1]["intensity"], b["intensity"])
        else:
            merged.append(dict(b))
    return merged


def with_blocks(plan: dict, blocks: list[dict]) -> dict:
    """`plan` with these blocks, its top-level fields set to their summary."""
    plan, blocks = dict(plan), _merge_neighbours(blocks)
    main = max(blocks, key=lambda b: (is_hard(b), b["intensity"], b["duration_min"]))
    plan["workout_type"] = main["workout_type"]
    plan["intensity"] = max(b["intensity"] for b in blocks)
    plan["duration_min"] = sum(b["duration_min"] for b in blocks)
    if len(blocks) > 1:
        plan["blocks"] = blocks
    else:
        plan.pop("blocks", None)
    return plan


def requested_blocks(request: list[dict]) -> list[dict]:
    """What they asked for ([{workout_type, duration_min | None}]) as full blocks, in their order."""
    return [{"workout_type": r["workout_type"], "intensity": DEFAULT_INTENSITY.get(r["workout_type"], 3),
             "duration_min": r.get("duration_min") or default_minutes(r["workout_type"])}
            for r in request]


def pin_to_request(plan: dict, request: list[dict]) -> dict:
    """The coach's plan with its blocks replaced by the request (types and order from the request).
    Minutes: the request's if stated, else the coach's for that type. Intensity: the coach's for that type."""
    proposed = blocks_of(plan)
    pinned = []
    for want, default in zip(request, requested_blocks(request)):
        same = next((b for b in proposed if b["workout_type"] == want["workout_type"]), None)
        pinned.append({
            "workout_type": want["workout_type"],
            "intensity": same["intensity"] if same else default["intensity"],
            "duration_min": want.get("duration_min") or (same["duration_min"] if same else default["duration_min"]),
        })
    return with_blocks(plan, pinned)


def block_slots(plan: dict, day: date, tz) -> list[tuple[datetime, datetime]]:
    """Start and end of each block, back to back from the plan's start ([] for rest)."""
    slot = plan.get("time_slot") or "none"
    if slot == "none":
        return []
    start = datetime.combine(day, time.fromisoformat(slot.split("-")[0]), tz)
    slots = []
    for b in blocks_of(plan):
        end = start + timedelta(minutes=b["duration_min"] or 0)
        slots.append((start, end))
        start = end
    return slots
