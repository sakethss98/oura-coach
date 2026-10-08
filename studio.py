"""Entry point for `langgraph dev` (see langgraph.json).

Studio always runs on fixtures and demo.db, so it can never write to oura_coach.db.
Start a run with input {"use_fixtures": true}; the graph pauses at `checkin` and
resumes with {"energy": 4, "soreness": 2}.
"""
import db
from config import DEMO_DB_PATH
from graph import build_graph

db.set_db_path(DEMO_DB_PATH)
db.init_db()
graph = build_graph(None)  # langgraph dev supplies its own checkpointer
