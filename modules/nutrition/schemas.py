"""Structured-output models for nutrition."""
from typing import Literal

from pydantic import BaseModel, Field


class FoodEstimate(BaseModel):
    entry_type: Literal["meal", "snack", "drink", "supplement"]
    est_protein_g: float = Field(ge=0)
    est_fiber_g: float = Field(ge=0)
    est_carbs_g: float = Field(ge=0)
    est_calories: float = Field(ge=0)
    est_caffeine_mg: float = Field(ge=0)
    alcohol_drinks: float = Field(ge=0, description="US standard drinks (14 g alcohol each)")
    confidence: Literal["low", "med", "high"]
