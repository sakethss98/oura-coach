"""The module contract. A module is one area of coaching a person can turn on (training, nutrition;
sleep and habits later). Kept deliberately small: no plugin framework, just these five methods.

Deterministic rules live inside each module as plain code; the agent only gets the tools of
the modules the user has enabled.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class ScoreComponent:
    """A module's deterministic share of the daily score."""
    name: str                 # e.g. "training"
    fraction: float | None    # 0.0-1.0 of this component earned (None = nothing to score)
    note: str                 # plain words for the breakdown, e.g. "run done"
    details: dict = field(default_factory=dict)
    next: str | None = None   # what would still help today, as a plain fact ("a 20-minute walk"); None when done


@dataclass
class NudgeSpec:
    """A proactive message the module would like to send. Declared only; M4 fires them."""
    name: str                 # e.g. "log_dinner"
    when: str                 # plain description of the trigger, e.g. "no food logged by 20:00"
    text: str


class Module(Protocol):
    name: str

    def setup_questions(self) -> list[str]:
        """Onboarding questions (used in M5)."""

    def tools(self) -> list:
        """LangChain tools the agent can call when this module is enabled."""

    def context(self, user, day: date) -> str:
        """A short "right now" summary for the agent (a few lines)."""

    def nudges(self) -> list[NudgeSpec]:
        """Declared only; nothing fires them in M3.2."""

    def score(self, user, day: date, now: datetime | None = None) -> ScoreComponent:
        """This module's deterministic contribution to the day's score (`now` None = the day is over)."""

    def help_lines(self) -> list[str]:
        """Plain-language lines for /help."""
