"""Who is talking: a user and their settings (time zone, enabled modules, goal template, cutoffs).

Every channel resolves its sender to a `User` first; everything below works on that user.
"""
import json
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from core import db
from core.config import ROOT

DEFAULT_USER_ID = 1   # the only user today: the Telegram chat in .env


@dataclass(frozen=True)
class User:
    id: int
    telegram_chat_id: str | None
    tz: ZoneInfo
    enabled_modules: tuple[str, ...]
    goal_template: str
    goal_rigor: str
    score_cutoff: time
    goals_path: Path
    session_gap: timedelta

    def goals(self) -> dict:
        """The person's goals file (goals.yaml for user 1), read fresh each time."""
        return yaml.safe_load(self.goals_path.read_text())


def get(user_id: int) -> User:
    row = db.get_user_row(user_id)
    if row is None:
        raise LookupError(f"No user {user_id}")
    return User(
        id=row["id"],
        telegram_chat_id=row["telegram_chat_id"],
        tz=ZoneInfo(row["timezone"]),
        enabled_modules=tuple(json.loads(row["enabled_modules"])),
        goal_template=row["goal_template"],
        goal_rigor=row["goal_rigor"],
        score_cutoff=time.fromisoformat(row["score_cutoff"]),
        goals_path=ROOT / row["goals_path"],
        session_gap=timedelta(minutes=row["session_gap_min"]),
    )


def for_telegram_chat(chat_id) -> User | None:
    """The user behind a Telegram chat, or None for a chat we don't know."""
    user_id = db.user_id_for_telegram_chat(str(chat_id))
    return get(user_id) if user_id is not None else None


def default() -> User:
    return get(DEFAULT_USER_ID)
