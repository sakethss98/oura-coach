"""SQLite storage: Oura tokens, daily log, sessions, context notes, food log, and LLM run traces.

Every function opens its own short-lived connection, so the module is safe to call
from any thread (the Telegram bot runs slow calls in asyncio.to_thread workers).
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from config import DB_PATH, TZ

# Module-level so `--fixtures` runs can point everything at demo.db.
_db_path: Path = DB_PATH


def set_db_path(path: Path) -> None:
    global _db_path
    _db_path = path


def db_path() -> Path:
    return _db_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id            INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_log (
    date              TEXT PRIMARY KEY,  -- YYYY-MM-DD
    metrics_json      TEXT,              -- today's Oura metrics + baseline
    food_targets_json TEXT,              -- full-day targets computed by nutrition.py
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,      -- YYYY-MM-DD
    checkin_at       TEXT,               -- ISO local time the check-in started
    thread_id        TEXT,               -- LangGraph thread (NULL for unplanned)
    status           TEXT NOT NULL,      -- awaiting_checkin / planned / abandoned / unplanned
    source           TEXT NOT NULL,      -- planned / unplanned
    energy           INTEGER,            -- 1-5, NULL = not answered
    soreness         INTEGER,            -- 1-5, NULL = not answered
    note             TEXT,               -- "anything else" from the check-in
    plan_json        TEXT,               -- final plan after policy_check (unplanned: workout_type + duration)
    llm_plan_json    TEXT,               -- what the LLM proposed
    overridden       INTEGER,            -- 0/1
    override_reasons TEXT,               -- JSON list of strings
    completed        INTEGER,            -- 1 = done, NULL = not reported
    feeling          TEXT,
    done_at          TEXT,               -- ISO local time it was reported done
    actual_start     TEXT,               -- ISO local, used to de-duplicate against Oura workouts
    actual_end       TEXT
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
    timestamp        TEXT NOT NULL,  -- when it was eaten: ISO, America/Chicago local time with offset
    raw_text         TEXT NOT NULL,
    entry_type       TEXT,           -- meal / snack / drink / supplement
    est_protein_g    REAL,
    est_fiber_g      REAL,
    est_carbs_g      REAL,
    est_calories     REAL,
    est_caffeine_mg  REAL,
    alcohol_drinks   REAL,           -- standard drinks
    confidence       TEXT,           -- low / med / high
    logged_at        TEXT            -- when the entry was saved (used by /undo)
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,      -- "plan", "food", "router", "answer"
    model        TEXT NOT NULL,
    input_json   TEXT NOT NULL,      -- messages sent to the LLM
    output_json  TEXT                -- parsed structured output (NULL if the call failed)
);
"""

DAILY_LOG_FIELDS = {"metrics_json", "food_targets_json"}
# M2 kept per-plan fields in daily_log; M3 moves them to sessions.
OLD_DAILY_LOG_COLUMNS = ["food_notes", "checkin_json", "plan", "policy_json", "completed", "feeling"]
FOOD_FIELDS = ["entry_type", "est_protein_g", "est_fiber_g", "est_carbs_g", "est_calories",
               "est_caffeine_mg", "alcohol_drinks", "confidence"]


@contextmanager
def connect():
    """A connection that commits on success, rolls back on error, and always closes."""
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _stamp() -> str:
    """Save time with microseconds, so /undo can tell apart entries saved in the same second."""
    return datetime.now(TZ).isoformat(timespec="microseconds")


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


# --- setup and migration ----------------------------------------------------

def backup_db() -> Path:
    """Copy the database next to itself as <name>.backup-YYYYMMDD-HHMMSS.db and return the path."""
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    dest = _db_path.with_name(f"{_db_path.stem}.backup-{stamp}{_db_path.suffix}")
    src, dst = sqlite3.connect(_db_path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def _needs_migration() -> bool:
    if not _db_path.exists():
        return False
    with connect() as conn:
        daily = _columns(conn, "daily_log")
        food = _columns(conn, "food_log")
    old_daily = bool(daily) and (bool(daily & set(OLD_DAILY_LOG_COLUMNS)) or "food_targets_json" not in daily)
    return old_daily or (bool(food) and "logged_at" not in food)


def _migrate(conn) -> None:
    """M2 -> M3: per-plan fields move from daily_log to sessions; food_log gains logged_at."""
    daily = _columns(conn, "daily_log")
    if "plan" in daily:
        rows = conn.execute("SELECT * FROM daily_log WHERE plan IS NOT NULL").fetchall()
        for row in rows:
            checkin = json.loads(row["checkin_json"]) if row["checkin_json"] else {}
            policy = json.loads(row["policy_json"]) if "policy_json" in daily and row["policy_json"] else {}
            conn.execute(
                "INSERT INTO sessions (date, checkin_at, status, source, energy, soreness, plan_json, "
                "llm_plan_json, overridden, override_reasons, completed, feeling) "
                "VALUES (?, ?, 'planned', 'planned', ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["date"], row["updated_at"], checkin.get("energy"), checkin.get("soreness"), row["plan"],
                 json.dumps(policy["llm_plan"]) if "llm_plan" in policy else None,
                 int(policy.get("overridden", False)), json.dumps(policy.get("overrides", [])),
                 row["completed"] if "completed" in daily else None,
                 row["feeling"] if "feeling" in daily else None),
            )
    if "food_targets_json" not in daily:
        conn.execute("ALTER TABLE daily_log ADD COLUMN food_targets_json TEXT")
    for column in OLD_DAILY_LOG_COLUMNS:
        if column in daily:
            conn.execute(f"ALTER TABLE daily_log DROP COLUMN {column}")
    if "logged_at" not in _columns(conn, "food_log"):
        conn.execute("ALTER TABLE food_log ADD COLUMN logged_at TEXT")
        conn.execute("UPDATE food_log SET logged_at = timestamp")


def init_db() -> None:
    """Create tables. An older database is backed up first, then migrated (never without a backup)."""
    migrate = _needs_migration()
    if migrate:
        backup = backup_db()  # raises if it fails, so the migration never runs without a backup
        print(f"Backed up {_db_path.name} to {backup} before migrating.")
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        if migrate:
            _migrate(conn)


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


# --- sessions ---------------------------------------------------------------

def _session(row) -> dict | None:
    if row is None:
        return None
    s = dict(row)
    for key in ("plan_json", "llm_plan_json", "override_reasons"):
        s[key] = json.loads(s[key]) if s[key] else None
    return s


def start_session(day: date, thread_id: str, checkin_at: datetime) -> int:
    """Open a check-in. Any older check-in still waiting for an answer is abandoned."""
    with connect() as conn:
        conn.execute("UPDATE sessions SET status = 'abandoned' WHERE status = 'awaiting_checkin'")
        cur = conn.execute(
            "INSERT INTO sessions (date, checkin_at, thread_id, status, source) "
            "VALUES (?, ?, ?, 'awaiting_checkin', 'planned')",
            (day.isoformat(), checkin_at.isoformat(timespec="seconds"), thread_id),
        )
    return cur.lastrowid


def abandon_pending() -> None:
    with connect() as conn:
        conn.execute("UPDATE sessions SET status = 'abandoned' WHERE status = 'awaiting_checkin'")


def pending_session() -> dict | None:
    """The check-in waiting for an energy/soreness answer, if any (at most one)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE status = 'awaiting_checkin' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _session(row)


def thread_exists(thread_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM sessions WHERE thread_id = ?", (thread_id,)).fetchone() is not None


def finish_session(session_id: int, checkin: dict, plan: dict, llm_plan: dict, overrides: list[str]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET status = 'planned', energy = ?, soreness = ?, note = ?, plan_json = ?, "
            "llm_plan_json = ?, overridden = ?, override_reasons = ? WHERE id = ?",
            (checkin.get("energy"), checkin.get("soreness"), checkin.get("note"), json.dumps(plan),
             json.dumps(llm_plan), int(bool(overrides)), json.dumps(overrides), session_id),
        )


def get_session(session_id: int) -> dict | None:
    with connect() as conn:
        return _session(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())


def latest_open_session(day: date) -> dict | None:
    """Today's most recent plan that has not been marked done."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE date = ? AND status = 'planned' AND completed IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (day.isoformat(),),
        ).fetchone()
    return _session(row)


def latest_checkin(day: date) -> dict | None:
    """Today's most recent planned session whose check-in was answered."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE date = ? AND status = 'planned' "
            "AND (energy IS NOT NULL OR soreness IS NOT NULL) ORDER BY id DESC LIMIT 1",
            (day.isoformat(),),
        ).fetchone()
    return _session(row)


def count_planned(day: date) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE date = ? AND status = 'planned'", (day.isoformat(),)
        ).fetchone()[0]


def mark_done(session_id: int, feeling: str | None, done_at: datetime,
              actual_start: datetime | None, actual_end: datetime | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET completed = 1, feeling = ?, done_at = ?, actual_start = ?, actual_end = ? "
            "WHERE id = ?",
            (feeling, done_at.isoformat(timespec="seconds"),
             actual_start and actual_start.isoformat(timespec="seconds"),
             actual_end and actual_end.isoformat(timespec="seconds"), session_id),
        )


def add_unplanned(day: date, workout_type: str, duration_min: int | None, start: datetime, end: datetime,
                  feeling: str | None, done_at: datetime) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (date, status, source, plan_json, completed, feeling, done_at, "
            "actual_start, actual_end) VALUES (?, 'unplanned', 'unplanned', ?, 1, ?, ?, ?, ?)",
            (day.isoformat(), json.dumps({"workout_type": workout_type, "duration_min": duration_min}),
             feeling, done_at.isoformat(timespec="seconds"), start.isoformat(timespec="seconds"),
             end.isoformat(timespec="seconds")),
        )
    return get_session(cur.lastrowid)


def sessions_between(start: date, end: date) -> list[dict]:
    """Planned and unplanned sessions with start <= date <= end (abandoned/pending ones excluded)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE date BETWEEN ? AND ? AND status IN ('planned', 'unplanned') "
            "ORDER BY id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_session(r) for r in rows]


def delete_session(session_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


# --- context notes ----------------------------------------------------------

def add_context_note(note: str, start_date: date, end_date: date) -> dict:
    row = {"note": note, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
           "created_at": _stamp()}
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO context_notes (note, start_date, end_date, created_at) VALUES (?, ?, ?, ?)",
            list(row.values()),
        )
    return {"id": cur.lastrowid, **row}


def active_context_notes(day: date) -> list[str]:
    """Notes whose date range includes `day` (expired notes are simply ignored)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT note FROM context_notes WHERE start_date <= ? AND end_date >= ? ORDER BY id",
            (day.isoformat(), day.isoformat()),
        ).fetchall()
    return [r["note"] for r in rows]


# --- food log ---------------------------------------------------------------

def add_food(raw_text: str, estimate: dict, eaten_at: datetime) -> dict:
    """Save one food entry (every call is a new row, repeats included) and return it."""
    row = {"timestamp": eaten_at.astimezone(TZ).isoformat(timespec="seconds"), "raw_text": raw_text,
           **{f: estimate.get(f) for f in FOOD_FIELDS}, "logged_at": _stamp()}
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


# --- undo -------------------------------------------------------------------

def undo_last() -> dict | None:
    """Delete the most recently saved food entry or context note. Returns what was removed."""
    with connect() as conn:
        food = conn.execute("SELECT * FROM food_log ORDER BY logged_at DESC, id DESC LIMIT 1").fetchone()
        note = conn.execute("SELECT * FROM context_notes ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if food is None and note is None:
            return None
        if note is None or (food is not None and food["logged_at"] >= note["created_at"]):
            conn.execute("DELETE FROM food_log WHERE id = ?", (food["id"],))
            return {"kind": "food", **dict(food)}
        conn.execute("DELETE FROM context_notes WHERE id = ?", (note["id"],))
        return {"kind": "note", **dict(note)}


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
