"""Message in, reply out, for any channel. No Telegram code here, so it is all testable offline.

Every message is logged to the user's chat session (core/chat_sessions.py); text goes to the agent
(core/agent.py) on that session's thread. Only two commands exist: /start and /help.
"""
from datetime import datetime

from langchain_core.messages import HumanMessage

from connectors.oura import latest_day, load_fixtures
from core import chat_sessions
from core.agent import build_agent
from core.tools import CORE_HELP
from modules.registry import enabled_modules

START = ("I'm your coach: I plan workouts from your Oura data and calendar, log what you eat, "
         "and remember what you tell me to.\n"
         'Try: "want to work out now", "had poha and chai at 8", "remember I\'m traveling Thu-Sat".')
VOICE = "I can't understand voice notes yet. Please type it."
RECURSION_LIMIT = 25


class Coach:
    def __init__(self, checkpointer=None, use_fixtures: bool = False):
        self.agent = build_agent(checkpointer)
        self.use_fixtures = use_fixtures
        self._fixture_day = latest_day(load_fixtures()) if use_fixtures else None

    def clock(self, user, msg_time: datetime) -> datetime:
        """Local message time. Fixtures mode keeps the clock time but moves it to the fixtures' today."""
        local = msg_time.astimezone(user.tz).replace(second=0, microsecond=0)
        if self._fixture_day:
            return datetime.combine(self._fixture_day, local.time(), user.tz)
        return local

    def _exchange(self, user, msg_time: datetime, logged_text: str, reply_for) -> str:
        """Log their message, work out the reply, log the reply."""
        now = self.clock(user, msg_time)
        session, _ = chat_sessions.touch(user, now)
        chat_sessions.log(user, session, now, "user", logged_text)
        reply = reply_for(session, now)
        chat_sessions.log(user, session, now, "assistant", reply)
        return reply

    def handle_text(self, user, text: str, msg_time: datetime) -> str:
        def ask_agent(session, now):
            config = {"configurable": {"thread_id": session["thread_id"], "user_id": user.id,
                                       "now": now.isoformat(), "session_id": session["id"],
                                       "use_fixtures": self.use_fixtures},
                      "recursion_limit": RECURSION_LIMIT}
            result = self.agent.invoke({"messages": [HumanMessage(text)]}, config)
            return result["messages"][-1].content
        return self._exchange(user, msg_time, text, ask_agent)

    def handle_voice(self, user, msg_time: datetime) -> str:
        return self._exchange(user, msg_time, "[voice note]", lambda session, now: VOICE)

    def handle_command(self, user, command: str, msg_time: datetime) -> str:
        reply = START if command == "start" else help_text(user)
        return self._exchange(user, msg_time, f"/{command}", lambda session, now: reply)


def help_text(user) -> str:
    """What I can do, from the user's enabled modules, in plain words."""
    lines = [line for module in enabled_modules(user) for line in module.help_lines()] + CORE_HELP
    return "Just message me in plain words:\n" + "\n".join(f"- {line}" for line in lines)
