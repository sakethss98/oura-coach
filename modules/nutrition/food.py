"""Food logging: rough LLM macro estimate + a row in food_log, and edits to a row in place.

Used by log_food.py and by chat messages. The person's saved diet facts go into the estimate prompt.
Numbers the person states ("30 cal", "9g carbs") replace the estimate for that field and are recorded in
`user_numbers_json`; a later re-estimate (a changed description) never overwrites them.
"""
import json
from datetime import datetime

from core import db, memory
from core.labels import clock
from core.llm import load_prompt, structured_call
from modules.nutrition.schemas import FoodEstimate

# Tool argument -> food_log column, for numbers the person can state.
NUMBER_FIELDS = {"calories": "est_calories", "protein_g": "est_protein_g", "carbs_g": "est_carbs_g",
                 "fiber_g": "est_fiber_g", "caffeine_mg": "est_caffeine_mg"}


def stated(numbers: dict | None) -> dict:
    """{"calories": 30, "fiber_g": None} -> {"est_calories": 30} (only the numbers they gave)."""
    return {NUMBER_FIELDS[k]: v for k, v in (numbers or {}).items() if v is not None and k in NUMBER_FIELDS}


def estimate(user, text: str, day, known: dict | None = None, feedback: str | None = None) -> FoodEstimate:
    """`known`: food_log columns the person stated; the estimate keeps the rest consistent with them.
    `feedback`: what they said about an earlier estimate ("I don't think it's 60g protein")."""
    facts = memory.for_prompt(user, day)
    prompt = f"Entry: {text}"
    if feedback:
        prompt += f"\n\nThey said the earlier estimate looked wrong: {feedback!r}. Take that into account."
    if known:
        names = {column: name for name, column in NUMBER_FIELDS.items()}
        prompt += "\n\nNumbers they stated (use exactly these): " + ", ".join(
            f"{names.get(k, k)} = {v:g}" for k, v in known.items())
    if facts:
        prompt += "\n\nSaved facts about this person:\n" + "\n".join(f"- {f}" for f in facts)
    return structured_call("food", FoodEstimate, load_prompt("food"), prompt, user.id)


def log_food(user, text: str, eaten_at: datetime, numbers: dict | None = None) -> dict:
    """Estimate macros for `text`, save a new food_log row eaten at `eaten_at`, and return it.
    `numbers`: what they stated ({"calories": 30, ...}); those replace the estimate."""
    given = stated(numbers)
    fields = estimate(user, text, eaten_at.date(), given).model_dump() | given
    return db.add_food(user.id, text, fields, eaten_at, sorted(given))


def user_numbers(row: dict) -> set[str]:
    return set(json.loads(row.get("user_numbers_json") or "[]"))


def edit_food(user, row: dict, new_text: str | None = None, numbers: dict | None = None,
              eaten_at: datetime | None = None, feedback: str | None = None) -> dict:
    """Change one entry in place (never a new row). A new description, or their doubt about the estimate
    (`feedback`), re-estimates every field they never stated; stated numbers (earlier and now) win.
    Returns the updated row."""
    given = stated(numbers)
    kept = user_numbers(row)
    fields = {}
    text = new_text.strip() if new_text and new_text.strip() != row["raw_text"] else None
    if text or feedback:
        known = {c: row[c] for c in kept} | given
        fresh = estimate(user, text or row["raw_text"], datetime.fromisoformat(row["timestamp"]).date(), known,
                         feedback).model_dump()
        fields |= {k: v for k, v in fresh.items() if k not in kept | set(given)}
    if text:
        fields["raw_text"] = text
    fields |= given
    if eaten_at is not None:
        fields["timestamp"] = eaten_at
    fields["user_numbers_json"] = json.dumps(sorted(kept | set(given)))
    return db.update_food(user.id, row["id"], fields)


def format_entry(row: dict, time_stated: bool = False) -> str:
    when = "time you gave" if time_stated else "message time"
    return (
        f"Logged #{row['id']} ({row['entry_type']}) at {clock(row['timestamp'][11:16])} ({when}): {row['raw_text']}\n"
        f"  ~{row['est_calories']:.0f} kcal | protein {row['est_protein_g']:.0f} g | "
        f"carbs {row['est_carbs_g']:.0f} g | fiber {row['est_fiber_g']:.0f} g | "
        f"caffeine {row['est_caffeine_mg']:.0f} mg | alcohol {row['alcohol_drinks']:g} drinks\n"
        f"  Confidence: {row['confidence']}. These are rough estimates."
    )
