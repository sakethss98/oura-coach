"""Short-term memory: every message in and out, grouped into chat sessions.

A new session starts when the user's last message is older than their `session_gap`
(15 min by default). The agent sees only the current session's recent messages; a new
session starts with no history (saved memory and today's data still apply).
Each session is one LangGraph thread: `session-{user_id}-{YYYYMMDDHHMM}`.
"""
from datetime import datetime

from core import db

HISTORY_MESSAGES = 10   # how many recent messages of the session the agent sees


def _thread_id(user_id: int, started_at: datetime) -> str:
    base = f"session-{user_id}-{started_at:%Y%m%d%H%M}"
    thread_id, n = base, 1
    while db.chat_thread_exists(thread_id):
        n += 1
        thread_id = f"{base}-{n}"
    return thread_id


def current(user, now: datetime) -> dict | None:
    """The session `now` would continue, or None if a new one is due."""
    latest = db.latest_chat_session(user.id)
    if latest is None or now - datetime.fromisoformat(latest["last_user_at"]) > user.session_gap:
        return None
    return latest


def touch(user, now: datetime) -> tuple[dict, bool]:
    """The session for a user message at `now` (created if due) and whether it is new."""
    session = current(user, now)
    if session is None:
        return db.add_chat_session(user.id, now, _thread_id(user.id, now)), True
    db.touch_chat_session(session["id"], now)
    return {**session, "last_user_at": now.isoformat(timespec="seconds")}, False


def log(user, session: dict, ts: datetime, role: str, text: str) -> None:
    db.add_message(user.id, session["id"], ts, role, text)


def history(session: dict, limit: int = HISTORY_MESSAGES) -> list[dict]:
    """The session's last `limit` messages, oldest first: [{"role", "text", "ts"}]."""
    return [{"role": m["role"], "text": m["text"], "ts": m["ts"]} for m in db.session_messages(session["id"], limit)]
