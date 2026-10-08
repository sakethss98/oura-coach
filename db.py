"""SQLite storage: Oura tokens, daily log, context notes, food log, and LLM run traces."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from config import DB_PATH, TZ

# Module-level so `--fixtures` runs can point everything at demo.db.
_db_path: Path = DB_PATH


def set_db_path(path: Path) -> None:
    global _db_path
    _db_path = path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id            INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_log (
    date          TEXT PRIMARY KEY,  -- YYYY-MM-DD
    metrics_json  TEXT,
    food_notes    TEXT,
    checkin_json  TEXT,
    plan          TEXT,              -- final plan (JSON) after policy_check
    policy_json   TEXT,              -- {"llm_plan", "overrides", "overridden"}
    completed     INTEGER,           -- 0/1, NULL = unknown
    feeling       TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note        TEXT NOT NULL,
    start_date  TEXT NOT NULL,       -- YYYY-MM-DD, inclusive
    end_date    TEXT NOT NULL,       -- YYYY-MM-DD, inclusive
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,  -- ISO, America/Chicago local time with offset
    raw_text         TEXT NOT NULL,
    entry_type       TEXT,           -- meal / snack / drink / supplement
    est_protein_g    REAL,
    est_fiber_g      REAL,
    est_carbs_g      REAL,
    est_calories     REAL,
    est_caffeine_mg  REAL,
    alcohol_drinks   REAL,           -- standard drinks
    confidence       TEXT            -- low / med / high
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,      -- "plan" or "food"
    model        TEXT NOT NULL,
    input_json   TEXT NOT NULL,      -- messages sent to the LLM
    output_json  TEXT                -- parsed structured output (NULL if the call failed)
);
"""

DAILY_LOG_FIELDS = {"metrics_json", "food_notes", "checkin_json", "plan", "policy_json", "completed", "feeling"}
FOOD_FIELDS = ["entry_type", "est_protein_g", "est_fiber_g", "est_carbs_g", "est_calories",
               "est_caffeine_mg", "alcohol_drinks", "confidence"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Databases created during M1 lack this column.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(daily_log)")}
        if "policy_json" not in columns:
            conn.execute("ALTER TABLE daily_log ADD COLUMN policy_json TEXT")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- tokens -----------------------------------------------------------------

def get_tokens() -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM tokens WHERE id = 1").fetchone()


def save_tokens(access_token: str, refresh_token: str, expires_at: str | None = None) -> None:
    """Store the token pair in a single transaction (both or neither)."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tokens (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at    = excluded.expires_at,
                updated_at    = excluded.updated_at
            """,
            (access_token, refresh_token, expires_at, _now()),
        )


# --- daily log --------------------------------------------------------------

def upsert_daily_log(day: date, **fields) -> None:
    """Insert or update one day's log. Dict/list values are stored as JSON."""
    unknown = set(fields) - DAILY_LOG_FIELDS
    if unknown:
        raise ValueError(f"Unknown daily_log fields: {unknown}")
    values = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    columns = ["date", *values, "updated_at"]
    params = [day.isoformat(), *values.values(), _now()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns[1:])
    with connect() as conn:
        conn.execute(
            f"INSERT INTO daily_log ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}",
            params,
        )


def get_daily_log(day: date) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM daily_log WHERE date = ?", (day.isoformat(),)).fetchone()


def delete_daily_log(day: date) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM daily_log WHERE date = ?", (day.isoformat(),))


def daily_logs_between(start: date, end: date) -> list[sqlite3.Row]:
    """Daily log rows with start <= date <= end, oldest first."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM daily_log WHERE date BETWEEN ? AND ? ORDER BY date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


# --- context notes ----------------------------------------------------------

def add_context_note(note: str, start_date: date, end_date: date) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO context_notes (note, start_date, end_date, created_at) VALUES (?, ?, ?, ?)",
            (note, start_date.isoformat(), end_date.isoformat(), _now()),
        )


def active_context_notes(day: date) -> list[str]:
    """Notes whose date range includes `day` (expired notes are simply ignored)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT note FROM context_notes WHERE start_date <= ? AND end_date >= ? ORDER BY id",
            (day.isoformat(), day.isoformat()),
        ).fetchall()
    return [r["note"] for r in rows]


# --- food log ---------------------------------------------------------------

def add_food(raw_text: str, estimate: dict) -> dict:
    """Save one food entry (every call is a new row, repeats included) and return it."""
    row = {"timestamp": datetime.now(TZ).isoformat(timespec="seconds"), "raw_text": raw_text,
           **{f: estimate.get(f) for f in FOOD_FIELDS}}
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO food_log ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            list(row.values()),
        )
    return {"id": cur.lastrowid, **row}


def food_entries(day: date) -> list[dict]:
    """All food entries for a local calendar day (timestamps are stored in local time)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM food_log WHERE substr(timestamp, 1, 10) = ? ORDER BY timestamp",
            (day.isoformat(),),
        ).fetchall()
    return [dict(r) for r in rows]


# --- LLM run traces ---------------------------------------------------------

def log_run(kind: str, model: str, input_data, output_data) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (created_at, kind, model, input_json, output_json) VALUES (?, ?, ?, ?, ?)",
            (_now(), kind, model, json.dumps(input_data),
             None if output_data is None else json.dumps(output_data)),
        )


def recent_runs(limit: int = 5) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
