"""Structured-output models for training (the plan the LLM proposes).

Optional fields are written as `X | None` without a default: OpenAI's strict JSON schema
mode needs every field present, and null is how the model says "not mentioned".
"""
from typing import Literal

from pydantic import BaseModel, Field

WorkoutType = Literal["HIIT", "easy_run", "long_run", "strength", "walk", "mobility", "rest"]
# What a finished workout reported in chat can be (planned types plus a few everyday ones).
DoneType = Literal["HIIT", "easy_run", "long_run", "walk", "mobility", "strength", "yoga", "other"]


class Exercise(BaseModel):
    name: str = Field(description='e.g. "Bench press"')
    sets_reps: str = Field(description='e.g. "4 x 8-10"')


class Plan(BaseModel):
    workout_type: WorkoutType
    intensity: int = Field(ge=1, le=5, description="1 = very easy, 5 = all-out")
    time_slot: str = Field(description='"HH:MM-HH:MM" inside the free time, or "none" for rest')
    duration_min: int = Field(ge=0)
    why: str = Field(description="one plain sentence, at most 1-2 key numbers")
    reasoning: str = Field(description="the full reasoning with the actual numbers (shown on request)")
    food_before: str = Field(description="a light option before this workout that fits their saved diet facts; "
                                         "empty for rest")
    food_after: str = Field(description="one meal sized to what is left today, fitting their saved diet facts")
    follow_up: str = Field(description="rest only: one short question; otherwise empty")
    exercises: list[Exercise] = Field(description="strength only: 5-6 exercises for today's split day; else []")
    rules_applied: list[str] = Field(description="goals.yaml rules that shaped this plan")
