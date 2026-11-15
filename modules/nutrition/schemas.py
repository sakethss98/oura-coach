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


Unit = Literal["g", "kg", "oz", "lb", "ml", "l", "fl oz", "cup", "tbsp", "tsp", "piece", "slice", "serving",
               "container", "can", "bottle", "scoop", "bowl", "plate", "glass"]


class ItemGuess(BaseModel):
    """The model's rough numbers for one item, used only when USDA FoodData Central has no match (approximate)."""
    protein_g: float = Field(ge=0)
    fiber_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    calories: float = Field(ge=0)
    caffeine_mg: float = Field(ge=0)
    alcohol_drinks: float = Field(ge=0, description="US standard drinks (14 g alcohol each)")


class FoodItem(BaseModel):
    name: str = Field(description='the item as they said it: "chicken breast", "a Chobani"')
    usda_query: str = Field(description='a USDA-style description with its state, e.g. "chicken breast, meat only, '
                                        'roasted", "rice, white, cooked", "Greek yogurt, plain"')
    brand: str | None = Field(description='the brand when they named one ("Chobani"), else null')
    quantity: float | None = Field(description="the number they gave (200, 1, 0.5), or null")
    unit: Unit | None = Field(description="the unit they gave, or null (a bare count of a whole food = piece)")
    grams_guess: float = Field(ge=0, description="your rough weight in grams of what they had (only used when code "
                                                 "can't convert their amount)")
    guess: ItemGuess


class FoodItems(BaseModel):
    entry_type: Literal["meal", "snack", "drink", "supplement"]
    confidence: Literal["low", "med", "high"]
    items: list[FoodItem] = Field(min_length=1)


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
