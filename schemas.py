"""Structured-output models the LLM must fill in."""
from typing import Literal

from pydantic import BaseModel, Field

WorkoutType = Literal["HIIT", "easy_run", "long_run", "walk", "rest"]


class Plan(BaseModel):
    workout_type: WorkoutType
    intensity: int = Field(ge=1, le=5, description="1 = very easy, 5 = all-out")
    time_slot: str = Field(description='"HH:MM-HH:MM" inside one of the free slots, or "none" for rest')
    duration_min: int = Field(ge=0)
    reasoning: str = Field(description="2-3 sentences citing the actual numbers")
    nutrition_note: str
    rules_applied: list[str] = Field(description="goals.yaml rules that shaped this plan")


class FoodEstimate(BaseModel):
    entry_type: Literal["meal", "snack", "drink", "supplement"]
    est_protein_g: float = Field(ge=0)
    est_fiber_g: float = Field(ge=0)
    est_carbs_g: float = Field(ge=0)
    est_calories: float = Field(ge=0)
    est_caffeine_mg: float = Field(ge=0)
    alcohol_drinks: float = Field(ge=0, description="US standard drinks (14 g alcohol each)")
    confidence: Literal["low", "med", "high"]
