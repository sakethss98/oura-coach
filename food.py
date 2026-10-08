"""Food logging: rough LLM macro estimate + a row in food_log.

Used by log_food.py and by Telegram messages (chat.py).
"""
from datetime import datetime

import db
from llm import load_prompt, structured_call
from schemas import FoodEstimate


def log_food(text: str, eaten_at: datetime) -> dict:
    """Estimate macros for `text`, save a new food_log row eaten at `eaten_at`, and return it."""
    estimate = structured_call("food", FoodEstimate, load_prompt("food"), text)
    return db.add_food(text, estimate.model_dump(), eaten_at)


def format_entry(row: dict, time_stated: bool = False) -> str:
    when = "time you gave" if time_stated else "message time"
    return (
        f"Logged #{row['id']} ({row['entry_type']}) at {row['timestamp'][11:16]} ({when}): {row['raw_text']}\n"
        f"  ~{row['est_calories']:.0f} kcal | protein {row['est_protein_g']:.0f} g | "
        f"carbs {row['est_carbs_g']:.0f} g | fiber {row['est_fiber_g']:.0f} g | "
        f"caffeine {row['est_caffeine_mg']:.0f} mg | alcohol {row['alcohol_drinks']:g} drinks\n"
        f"  Confidence: {row['confidence']}. These are rough estimates."
    )
