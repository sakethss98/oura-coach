"""Settings and constants shared across the project."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

TZ = ZoneInfo("America/Chicago")
DB_PATH = ROOT / "oura_coach.db"
CHECKPOINT_DB_PATH = ROOT / "checkpoints.db"
# --fixtures runs write here instead, so demo data never mixes with real history.
DEMO_DB_PATH = ROOT / "demo.db"
DEMO_CHECKPOINT_DB_PATH = ROOT / "demo_checkpoints.db"
FIXTURES_DIR = ROOT / "fixtures"
PROMPTS_DIR = ROOT / "prompts"
GOALS_PATH = ROOT / "goals.yaml"

# The only place the default model name appears. Override with OPENAI_MODEL in .env.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# Free-slot search window (local time) and minimum useful slot length.
DAY_START_HOUR = 6
DAY_END_HOUR = 21
MIN_SLOT_MINUTES = 30


def env(name: str) -> str:
    """Return a required environment variable, or fail with a clear message."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value
