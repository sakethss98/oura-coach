"""Structured-output models the LLM must fill in.

Optional fields are written as `X | None` without a default: OpenAI's strict JSON schema
mode needs every field present, and null is how the model says "not mentioned".
"""
from typing import Literal

from pydantic import BaseModel, Field

WorkoutType = Literal["HIIT", "easy_run", "long_run", "walk", "mobility", "rest"]
# What a finished workout reported in chat can be (planned types plus a few everyday ones).
DoneType = Literal["HIIT", "easy_run", "long_run", "walk", "mobility", "strength", "yoga", "other"]


class Plan(BaseModel):
    workout_type: WorkoutType
    intensity: int = Field(ge=1, le=5, description="1 = very easy, 5 = all-out")
    time_slot: str = Field(description='"HH:MM-HH:MM" inside the plan window, or "none" for rest')
    duration_min: int = Field(ge=0)
    reasoning: str = Field(description="2-3 sentences citing the actual numbers")
    nutrition_note: str = Field(description="pre/post-workout food suggestion for right now")
    meal_ideas: list[str] = Field(description="first plan of the day only: meal ideas for the day; else []")
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


class RouterAction(BaseModel):
    type: Literal["food", "context_note", "workout_done", "start_checkin", "question", "unclear"]
    text: str | None = Field(description="food: what was consumed; context_note: the note; question: the question")
    time_text: str | None = Field(description='a clock time exactly as written, e.g. "at 1", "8am", "13:30"; else null')
    start_text: str | None = Field(description='context_note: first day as written, e.g. "Thu", "tomorrow"; else null')
    end_text: str | None = Field(description='context_note: last day as written, e.g. "Sat"; else null')
    refers_to_plan: bool | None = Field(description="workout_done: true if it refers to the planned session "
                                                    '("did the run", "done"); false if it describes a new activity')
    workout_type: DoneType | None = Field(description="workout_done: the kind of activity, if stated")
    duration_min: int | None = Field(description="workout_done: minutes, only if stated")
    feeling: str | None = Field(description='workout_done: how it felt, e.g. "heavy", "great"; else null')


class RouterResult(BaseModel):
    actions: list[RouterAction]


class Answer(BaseModel):
    reply: str = Field(description="a short Telegram reply, plain text")
