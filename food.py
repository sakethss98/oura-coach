"""Food logging: rough LLM macro estimate + a row in food_log.

Used by log_food.py now and by free-form Telegram messages in M3.
"""
import db
from llm import load_prompt, structured_call
from schemas import FoodEstimate


def log_food(text: str) -> dict:
    """Estimate macros for `text`, save a new food_log row, and return it."""
    estimate = structured_call("food", FoodEstimate, load_prompt("food"), text)
    return db.add_food(text, estimate.model_dump())


def format_entry(row: dict) -> str:
    return (
        f"Logged #{row['id']} ({row['entry_type']}, {row['timestamp'][11:16]}): {row['raw_text']}\n"
        f"  ~{row['est_calories']:.0f} kcal | protein {row['est_protein_g']:.0f} g | "
        f"carbs {row['est_carbs_g']:.0f} g | fiber {row['est_fiber_g']:.0f} g | "
        f"caffeine {row['est_caffeine_mg']:.0f} mg | alcohol {row['alcohol_drinks']:g} drinks\n"
        f"  Confidence: {row['confidence']}. These are rough estimates."
    )
