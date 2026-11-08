"""Short-term memory: every message in and out, grouped into chat sessions.

A new session starts when the user's last message is older than their `session_gap`
(15 min by default). The agent sees only the current session's recent messages; a new
session starts with no history (saved memory and today's data still apply).
Each session is one LangGraph thread: `session-{user_id}-{YYYYMMDDHHMM}`.
"""
import re
from datetime import datetime, timedelta

from core import db

HISTORY_MESSAGES = 10   # how many recent messages of the session the agent sees
QUESTION_LASTS = timedelta(hours=24)    # a question left at the end of a session is shown for this long
CONFIRM_AFTER = timedelta(hours=12)     # older than this, a bare "yes" is checked before acting on it
SHORT_REPLY = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|ok sure|no|nope|nah|y|n)[\s!.]*$", re.I)


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


def previous_question(user, session_id: int, now: datetime) -> dict | None:
    """The question the coach ended the previous session with, if it is under 24 h old:
    {"text": the whole message, "question": its last line with a "?", "sent": datetime}. Read from the
    messages table, never saved anywhere else; a newer session (or 24 h) makes it go away."""
    last = db.previous_session_last_message(user.id, session_id)
    if last is None or last["role"] != "assistant" or "?" not in last["text"]:
        return None
    sent = datetime.fromisoformat(last["ts"])
    if now - sent >= QUESTION_LASTS:
        return None
    question = next(line.strip() for line in reversed(last["text"].splitlines()) if "?" in line)
    return {"text": last["text"], "question": question, "sent": sent}


def needs_confirming(question: dict | None, message: str, now: datetime) -> bool:
    """A bare "yes"/"ok" to a question over 12 h old: check which question they mean before acting."""
    return bool(question) and now - question["sent"] > CONFIRM_AFTER and bool(SHORT_REPLY.match(message))
