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


class Portion(BaseModel):
    food: str = Field(description='one ingredient as eaten, e.g. "cooked toor dal", "chapati"')
    grams: int = Field(ge=0, description="weight in grams (cooked weight for cooked food)")
    household: str = Field(description='the same amount in a household measure: "1 katori", "3/4 cup", "2 chapatis"')


class MealOption(BaseModel):
    dish: str = Field(description='a short dish name, e.g. "Dal palak with jeera rice"')
    portions: list[Portion]
    est_calories: float = Field(ge=0)
    est_protein_g: float = Field(ge=0)


class MealIdeas(BaseModel):
    options: list[MealOption] = Field(description="1 or 2 different dishes")


class Recipe(BaseModel):
    portions: list[Portion]
    steps: list[str] = Field(description="at most 6 short steps")
    est_calories: float = Field(ge=0)
    est_protein_g: float = Field(ge=0)
