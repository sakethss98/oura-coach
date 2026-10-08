"""Turn a free-form Telegram message into a list of actions (one structured LLM call)."""
import json
from datetime import datetime

from llm import load_prompt, structured_call
from schemas import RouterAction, RouterResult


def route(text: str, now: datetime, pending_checkin: bool, open_session: dict | None,
          last_question: str | None = None) -> list[RouterAction]:
    user = json.dumps({
        "message": text,
        "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
        "checkin_waiting_for_answer": pending_checkin,
        "planned_session_not_done": (open_session["plan_json"] or {}).get("workout_type") if open_session else None,
        "coach_last_question": last_question,
    }, indent=2)
    return structured_call("router", RouterResult, load_prompt("router"), user).actions
