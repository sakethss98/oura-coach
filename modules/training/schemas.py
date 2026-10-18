"""Structured-output models for training: the parsed check-in answer and the plan the LLM proposes.

Optional fields are written as `X | None` without a default: OpenAI's strict JSON schema
mode needs every field present, and null is how the model says "not mentioned".
"""
from typing import Literal

from pydantic import BaseModel, Field

WorkoutType = Literal["HIIT", "easy_run", "long_run", "strength", "walk", "mobility", "rest"]
BlockType = Literal["HIIT", "easy_run", "long_run", "strength", "walk", "mobility"]
# What a finished workout reported in chat can be (planned types plus a few everyday ones).
DoneType = Literal["HIIT", "easy_run", "long_run", "walk", "mobility", "strength", "yoga", "other"]


class Block(BaseModel):
    """One part of what they asked for ("run + strength" = two blocks)."""
    workout_type: BlockType = Field(description='"run" = easy_run; long_run only if they say "long"')
    duration_min: int | None = Field(description="minutes, only if they said; else null")


class Request(BaseModel):
    blocks: list[Block] = Field(description="in the order they said them")


class CheckinParse(BaseModel):
    """Their answer to the check-in question (and the workout they asked for earlier, if any)."""
    energy: int | None = Field(description="1-5 as they said it; null if not given or skipped")
    soreness: int | None = Field(description="0-5 as they said it (0 = not sore); null if not given or skipped")
    request: Request | None = Field(description="a specific workout they asked for; null if none")
    feeling: str | None = Field(description="anything else about how they feel, in their words; else null")
    constraints: list[str] = Field(description='limits they mentioned, e.g. "knee hurts", "only 30 min"; else []')


class PlanBlock(BaseModel):
    workout_type: BlockType
    intensity: int = Field(ge=1, le=5)
    duration_min: int = Field(ge=0)


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
    blocks: list[PlanBlock] = Field(description="only when they asked for several parts (e.g. run, then strength), "
                                                "in their order; else []. A warm-up is not a block.")
    rules_applied: list[str] = Field(description="goals.yaml rules that shaped this plan")
