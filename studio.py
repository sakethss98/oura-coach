"""Entry point for `langgraph dev` (see langgraph.json): the agent and the training planner.

Studio always runs on fixtures and demo.db, so it can never write to oura_coach.db.
- agent: input {"messages": [{"role": "user", "content": "hi"}]}, with configurable
  {"user_id": 1, "now": "2026-10-07T07:00:00-05:00", "session_id": 1, "use_fixtures": true}.
- planner: input {"use_fixtures": true, "checkin": {"energy": 4, "soreness": 2, "note": null}}.
"""
from core import db
from core.agent import build_agent
from core.config import DEMO_DB_PATH
from modules.training.planner import build_graph

db.set_db_path(DEMO_DB_PATH)
db.init_db()
agent = build_agent()      # langgraph dev supplies its own checkpointer
planner = build_graph()
