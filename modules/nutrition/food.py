"""Food logging and food questions, on one path: the model reads the text into items and amounts, code looks each
item up in USDA FoodData Central and does the arithmetic (modules/nutrition/usda.py); an item USDA can't answer
uses the model's guess for it, marked approximate. log_food and food_facts both call `lookup_and_sum`, so a logged
entry and the answer to "how much protein is in it?" always have the same numbers (the reading of a text is kept
for a few hours, so the same words give the same items).

Used by log_food.py and by chat messages. The person's saved diet facts go into the prompt. Numbers the person
states ("30 cal", "9g carbs") replace the sum for that field and are recorded in `user_numbers_json`; a later
re-estimate (a changed description) never overwrites them.
"""
import json
import time
from datetime import datetime

from core import db, memory
from core.labels import clock
from core.llm import load_prompt, structured_call
from modules.nutrition import usda
from modules.nutrition.schemas import FoodItems

READ_KEEP_S = 4 * 3600          # the same words, read again within this time, give the same items
_read: dict[tuple, tuple[float, dict]] = {}

# Tool argument -> food_log column, for numbers the person can state.
NUMBER_FIELDS = {"calories": "est_calories", "protein_g": "est_protein_g", "carbs_g": "est_carbs_g",
                 "fiber_g": "est_fiber_g", "caffeine_mg": "est_caffeine_mg"}


def stated(numbers: dict | None) -> dict:
    """{"calories": 30, "fiber_g": None} -> {"est_calories": 30} (only the numbers they gave)."""
    return {NUMBER_FIELDS[k]: v for k, v in (numbers or {}).items() if v is not None and k in NUMBER_FIELDS}


def read_items(user, text: str, day, known: dict | None = None, feedback: str | None = None) -> dict:
    """The entry read into items and amounts (one structured call; the same words reuse it for a while).
    `known`: food_log columns the person stated; `feedback`: their doubt about an earlier estimate."""
    key = (user.id, " ".join(text.lower().split()))
    hit = _read.get(key)
    if hit and not known and not feedback and time.time() - hit[0] < READ_KEEP_S:
        return hit[1]
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
    read = structured_call("food", FoodItems, load_prompt("food"), prompt, user.id).model_dump()
    if not known and not feedback:
        _read[key] = (time.time(), read)
    return read


def lookup_and_sum(user, text: str, day, known: dict | None = None, feedback: str | None = None) -> dict:
    """{"fields": food_log columns (the sums), "items": per item grams, numbers and where they came from}."""
    read = read_items(user, text, day, known, feedback)
    items = [usda.lookup(i) for i in read["items"]]
    totals = usda.total(items)
    approximate = any(i["approximate"] for i in items)
    confidence = read["confidence"] if not approximate else ("low" if read["confidence"] == "low" else "med")
    fields = {"entry_type": read["entry_type"], "est_protein_g": totals["protein_g"], "est_fiber_g": totals["fiber_g"],
              "est_carbs_g": totals["carbs_g"], "est_calories": totals["calories"],
              "est_caffeine_mg": totals["caffeine_mg"], "alcohol_drinks": totals["alcohol_drinks"],
              "confidence": confidence}
    return {"fields": fields, "items": items}


def estimate(user, text: str, day, known: dict | None = None, feedback: str | None = None) -> dict:
    """The entry's food_log columns (see lookup_and_sum)."""
    return lookup_and_sum(user, text, day, known, feedback)["fields"]


def log_food(user, text: str, eaten_at: datetime, numbers: dict | None = None) -> dict:
    """Estimate macros for `text`, save a new food_log row eaten at `eaten_at`, and return it.
    `numbers`: what they stated ({"calories": 30, ...}); those replace the estimate."""
    given = stated(numbers)
    found = lookup_and_sum(user, text, eaten_at.date(), given)
    return db.add_food(user.id, text, found["fields"] | given, eaten_at, sorted(given), found["items"])


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
        found = lookup_and_sum(user, text or row["raw_text"], datetime.fromisoformat(row["timestamp"]).date(),
                               known, feedback)
        fields |= {k: v for k, v in found["fields"].items() if k not in kept | set(given)}
        fields["nutrition_json"] = json.dumps(found["items"], default=str)
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
