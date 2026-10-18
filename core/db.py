"""SQLite storage for every user: plans, food, memory, messages, chat sessions, caches, scores, LLM traces.

Every function opens its own short-lived connection, so the module is safe to call
from any thread (the Telegram bot runs slow calls in asyncio.to_thread workers).
Every user-owned row has a `user_id`; functions take it as their first argument.
The Oura `tokens` table is the one exception: it is a single row (user 1) and is never migrated.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import DB_PATH, TZ, env_optional

# Module-level so `--fixtures` runs can point everything at demo.db.
_db_path: Path = DB_PATH


def set_db_path(path: Path) -> None:
    global _db_path
    _db_path = path


def db_path() -> Path:
    return _db_path


# No ";" inside comments: init_db splits this on ";" to run it inside one transaction.
SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id            INTEGER PRIMARY KEY CHECK (id = 1),  -- single row, user 1
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id TEXT UNIQUE,
    name             TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id),
    timezone         TEXT NOT NULL,      -- IANA name, e.g. America/Chicago
    enabled_modules  TEXT NOT NULL,      -- JSON list, e.g. ["training", "nutrition"]
    goal_template    TEXT NOT NULL,      -- key in config/score.yaml
    goal_rigor       TEXT NOT NULL,      -- light / moderate / intense
    score_cutoff     TEXT NOT NULL,      -- HH:MM local, the day's score is final after this
    goals_path       TEXT NOT NULL,      -- the person's goals file, relative to the repo root
    session_gap_min  INTEGER NOT NULL    -- a new chat session starts after this many quiet minutes
);

CREATE TABLE IF NOT EXISTS daily_log (
    user_id           INTEGER NOT NULL,
    date              TEXT NOT NULL,     -- YYYY-MM-DD
    metrics_json      TEXT,              -- the day's Oura metrics + baseline
    food_targets_json TEXT,              -- full-day targets computed by the nutrition module
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS plans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL DEFAULT 1,
    chat_session_id  INTEGER,            -- the chat session the check-in started in
    date             TEXT NOT NULL,      -- YYYY-MM-DD (core.day.day_of at check-in time)
    checkin_at       TEXT,               -- ISO local time the check-in started
    thread_id        TEXT,               -- LangGraph thread of the planner run
    status           TEXT NOT NULL,      -- awaiting_checkin / awaiting_confirm / planned / abandoned / expired
    source           TEXT NOT NULL,      -- planned (kept for older rows)
    energy           INTEGER,            -- 1-5, NULL = not answered
    soreness         INTEGER,            -- 0-5, NULL = not answered
    note             TEXT,               -- the check-in comment
    plan_json        TEXT,               -- final plan after policy_check
    llm_plan_json    TEXT,               -- what the LLM proposed
    overridden       INTEGER,            -- 0/1
    override_reasons TEXT,               -- JSON list of overrides
    completed        INTEGER,            -- 1 = done, NULL = not reported
    feeling          TEXT,
    done_at          TEXT,               -- ISO local time it was reported done
    actual_start     TEXT,               -- ISO local, from Oura when it recorded the workout
    actual_end       TEXT,
    done_by          TEXT,               -- chat / oura / mixed (who marked it done)
    details_json     TEXT,               -- numbers and reasons behind the plan
    requested_json   TEXT                -- what they asked for, and a pending consult (training planner)
);

CREATE TABLE IF NOT EXISTS plan_blocks_done (
    plan_id      INTEGER NOT NULL,
    block_index  INTEGER NOT NULL,       -- position in plan_json.blocks (0 for a single-block plan)
    start        TEXT NOT NULL,          -- ISO local
    end          TEXT NOT NULL,
    minutes      INTEGER NOT NULL,
    done_by      TEXT NOT NULL,          -- oura / chat
    created_at   TEXT NOT NULL,
    PRIMARY KEY (plan_id, block_index)
);

CREATE TABLE IF NOT EXISTS manual_workouts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    workout_type  TEXT NOT NULL,         -- HIIT / easy_run / long_run / walk / mobility / strength / yoga / other
    start         TEXT NOT NULL,         -- ISO local
    end           TEXT NOT NULL,         -- ISO local
    minutes       INTEGER,               -- as stated, NULL = length not given
    feeling       TEXT,
    plan_id       INTEGER,               -- the plan it completed, if any
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL DEFAULT 1,
    timestamp         TEXT NOT NULL,     -- when it was eaten: ISO local time with offset
    raw_text          TEXT NOT NULL,
    entry_type        TEXT,              -- meal / snack / drink / supplement
    est_protein_g     REAL,
    est_fiber_g       REAL,
    est_carbs_g       REAL,
    est_calories      REAL,
    est_caffeine_mg   REAL,
    alcohol_drinks    REAL,              -- standard drinks
    confidence        TEXT,              -- low / med / high
    logged_at         TEXT,              -- when the entry was saved
    updated_at        TEXT,              -- last edit
    user_numbers_json TEXT               -- JSON list of fields the person stated (never re-estimated)
);

CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- goal / diet / preference / instruction / context
    start_date  TEXT,                    -- YYYY-MM-DD inclusive, NULL = always
    end_date    TEXT,                    -- YYYY-MM-DD inclusive, NULL = always
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    started_at    TEXT NOT NULL,
    last_user_at  TEXT NOT NULL,
    thread_id     TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    chat_session_id  INTEGER NOT NULL,
    ts               TEXT NOT NULL,
    role             TEXT NOT NULL,      -- user / assistant
    text             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oura_cache (
    user_id     INTEGER PRIMARY KEY,     -- one cached fetch per user (the newest)
    fetched_at  TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    raw_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_scores (
    user_id          INTEGER NOT NULL,
    day              TEXT NOT NULL,
    score            INTEGER NOT NULL,
    components_json  TEXT NOT NULL,
    final            INTEGER NOT NULL,   -- 1 = stored after the cutoff, never recomputed
    computed_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS meal_suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    dish        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- plan / food / router / answer / agent ...
    model        TEXT NOT NULL,
    input_json   TEXT NOT NULL,          -- messages sent to the LLM
    output_json  TEXT                    -- parsed output (NULL if the call failed)
)
"""

# The M3.1 sessions table, created only while migrating an M2 database (M2 had no sessions table).
LEGACY_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, checkin_at TEXT, thread_id TEXT,
    status TEXT NOT NULL, source TEXT NOT NULL, energy INTEGER, soreness INTEGER, note TEXT,
    plan_json TEXT, llm_plan_json TEXT, overridden INTEGER, override_reasons TEXT, completed INTEGER,
    feeling TEXT, done_at TEXT, actual_start TEXT, actual_end TEXT, done_by TEXT, details_json TEXT
)
"""

DAILY_LOG_FIELDS = {"metrics_json", "food_targets_json"}
# M2 kept per-plan fields in daily_log; M3 moved them to sessions.
OLD_DAILY_LOG_COLUMNS = ["food_notes", "checkin_json", "plan", "policy_json", "completed", "feeling"]
# Columns added to sessions in M3.1.
M31_SESSION_COLUMNS = ["done_by", "details_json"]
FOOD_FIELDS = ["entry_type", "est_protein_g", "est_fiber_g", "est_carbs_g", "est_calories",
               "est_caffeine_mg", "alcohol_drinks", "confidence"]

# What user 1 starts with. goals.yaml stays the source of their training and nutrition numbers.
DEFAULT_SETTINGS = {
    "timezone": "America/Chicago",
    "enabled_modules": ["training", "nutrition"],
    "goal_template": "endurance_body_comp",
    "goal_rigor": "moderate",
    "score_cutoff": "21:00",
    "goals_path": "goals.yaml",
    "session_gap_min": 15,
}
SEED_MEMORY = [("Vegetarian, eats eggs", "diet"), ("Prefers Indian food", "preference")]


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
    """Save time with microseconds, so entries saved in the same second keep their order."""
    return datetime.now(TZ).isoformat(timespec="microseconds")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in _tables(conn) else 0


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
    """True for any database from before M3.2 (it has tables but no `users` table)."""
    if not _db_path.exists():
        return False
    with connect() as conn:
        tables = _tables(conn)
    return bool(tables) and "users" not in tables


def _legacy_to_m31(conn) -> None:
    """M2 -> M3: per-plan fields move from daily_log to sessions; food_log gains logged_at.
    M3 -> M3.1: sessions gains done_by and details_json."""
    conn.execute(LEGACY_SESSIONS)
    daily = _columns(conn, "daily_log")
    if "plan" in daily:
        for row in conn.execute("SELECT * FROM daily_log WHERE plan IS NOT NULL").fetchall():
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
    if daily and "food_targets_json" not in daily:
        conn.execute("ALTER TABLE daily_log ADD COLUMN food_targets_json TEXT")
    for column in OLD_DAILY_LOG_COLUMNS:
        if column in daily:
            conn.execute(f"ALTER TABLE daily_log DROP COLUMN {column}")
    sessions = _columns(conn, "sessions")
    for column in M31_SESSION_COLUMNS:
        if column not in sessions:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
    food = _columns(conn, "food_log")
    if food and "logged_at" not in food:
        conn.execute("ALTER TABLE food_log ADD COLUMN logged_at TEXT")
        conn.execute("UPDATE food_log SET logged_at = timestamp")


def _m31_to_m32(conn) -> None:
    """M3.1 -> M3.2: user_id everywhere, sessions -> plans, unplanned sessions -> manual_workouts,
    daily_log keyed by (user_id, date), active context notes -> memory. Runs before SCHEMA,
    so the renamed tables are not shadowed by new empty ones."""
    conn.execute("ALTER TABLE sessions RENAME TO plans")
    conn.execute("ALTER TABLE plans ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    conn.execute("ALTER TABLE plans ADD COLUMN chat_session_id INTEGER")
    conn.execute("ALTER TABLE plans ADD COLUMN requested_json TEXT")
    tables = _tables(conn)
    if "daily_log" in tables:
        conn.execute("ALTER TABLE daily_log RENAME TO daily_log_m31")
    if "food_log" in tables:
        conn.execute("ALTER TABLE food_log ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        conn.execute("ALTER TABLE food_log ADD COLUMN updated_at TEXT")
        conn.execute("ALTER TABLE food_log ADD COLUMN user_numbers_json TEXT")
    if "runs" in tables:
        conn.execute("ALTER TABLE runs ADD COLUMN user_id INTEGER")
        conn.execute("UPDATE runs SET user_id = 1")


def _m31_copy_rows(conn) -> None:
    """The row moves that need the new tables (called after SCHEMA has created them)."""
    if "daily_log_m31" in _tables(conn):
        conn.execute("INSERT INTO daily_log (user_id, date, metrics_json, food_targets_json, updated_at) "
                     "SELECT 1, date, metrics_json, food_targets_json, updated_at FROM daily_log_m31")
        conn.execute("DROP TABLE daily_log_m31")
    conn.execute(
        "INSERT INTO manual_workouts (user_id, workout_type, start, end, minutes, feeling, created_at) "
        "SELECT user_id, COALESCE(json_extract(plan_json, '$.workout_type'), 'other'), actual_start, actual_end, "
        "json_extract(plan_json, '$.duration_min'), feeling, COALESCE(done_at, actual_end) "
        "FROM plans WHERE source = 'unplanned' AND actual_start IS NOT NULL")
    conn.execute("DELETE FROM plans WHERE source = 'unplanned'")
    if "context_notes" in _tables(conn):
        # Expired notes would never apply again, so only still-active ones become memory.
        # context_notes itself is left in place.
        today = datetime.now(TZ).date().isoformat()
        conn.execute("INSERT INTO memory (user_id, text, kind, start_date, end_date, created_at) "
                     "SELECT 1, note, 'context', start_date, end_date, created_at FROM context_notes "
                     "WHERE end_date >= ?", (today,))


def _seed_first_user(conn) -> None:
    """User 1 (the Telegram chat in .env) with default settings and diet facts, once."""
    if conn.execute("SELECT 1 FROM users").fetchone():
        return
    conn.execute("INSERT INTO users (id, telegram_chat_id, name, created_at) VALUES (1, ?, NULL, ?)",
                 (env_optional("TELEGRAM_CHAT_ID"), _now()))
    s = DEFAULT_SETTINGS
    conn.execute(
        "INSERT INTO user_settings (user_id, timezone, enabled_modules, goal_template, goal_rigor, score_cutoff, "
        "goals_path, session_gap_min) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
        (s["timezone"], json.dumps(s["enabled_modules"]), s["goal_template"], s["goal_rigor"],
         s["score_cutoff"], s["goals_path"], s["session_gap_min"]))
    for text, kind in SEED_MEMORY:
        conn.execute("INSERT INTO memory (user_id, text, kind, created_at) VALUES (1, ?, ?, ?)",
                     (text, kind, _stamp()))


def _run_schema(conn) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def init_db() -> None:
    """Create tables. An older database is backed up first, then migrated in one transaction
    (never without a backup). Row counts are checked before the migration commits."""
    migrate = _needs_migration()
    if migrate:
        backup = backup_db()  # raises if it fails, so the migration never runs without a backup
        print(f"Backed up {_db_path.name} to {backup} before migrating.")
    conn = sqlite3.connect(_db_path, timeout=10, isolation_level=None)  # explicit BEGIN/COMMIT below
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        try:
            if migrate:
                food_before = _count(conn, "food_log")
                _legacy_to_m31(conn)
                plans_before = _count(conn, "sessions")
                _m31_to_m32(conn)
            _run_schema(conn)
            if migrate:
                _m31_copy_rows(conn)
            _seed_first_user(conn)
            if migrate:
                food_after = _count(conn, "food_log")
                plans_after = _count(conn, "plans") + _count(conn, "manual_workouts")
                if (food_after, plans_after) != (food_before, plans_before):
                    raise RuntimeError(f"Migration row check failed (food {food_before}->{food_after}, "
                                       f"plans {plans_before}->{plans_after}); nothing was changed.")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# --- users ------------------------------------------------------------------

def get_user_row(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT u.*, s.* FROM users u JOIN user_settings s ON s.user_id = u.id "
                           "WHERE u.id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_id_for_telegram_chat(chat_id: str) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE telegram_chat_id = ?", (str(chat_id),)).fetchone()
    return row["id"] if row else None


# --- tokens (user 1, single row) ---------------------------------------------

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

def upsert_daily_log(user_id: int, day: date, **fields) -> None:
    """Insert or update one day's log. Dict/list values are stored as JSON."""
    unknown = set(fields) - DAILY_LOG_FIELDS
    if unknown:
        raise ValueError(f"Unknown daily_log fields: {unknown}")
    values = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    columns = ["user_id", "date", *values, "updated_at"]
    params = [user_id, day.isoformat(), *values.values(), _now()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns[2:])
    with connect() as conn:
        conn.execute(
            f"INSERT INTO daily_log ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
            f"ON CONFLICT(user_id, date) DO UPDATE SET {updates}",
            params,
        )


def get_daily_log(user_id: int, day: date) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM daily_log WHERE user_id = ? AND date = ?",
                            (user_id, day.isoformat())).fetchone()


# --- plans (check-ins and the workouts planned from them) --------------------

def _plan(row) -> dict | None:
    if row is None:
        return None
    p = dict(row)
    for key in ("plan_json", "llm_plan_json", "override_reasons", "details_json", "requested_json"):
        p[key] = json.loads(p[key]) if p[key] else None
    return p


def _with_blocks_done(conn, plans: list[dict]) -> list[dict]:
    """Each plan gets `blocks_done`: {block_index: {start, end, minutes, done_by}} (empty if none)."""
    for p in plans:
        rows = conn.execute("SELECT * FROM plan_blocks_done WHERE plan_id = ? ORDER BY block_index",
                            (p["id"],)).fetchall()
        p["blocks_done"] = {r["block_index"]: {k: r[k] for k in ("start", "end", "minutes", "done_by")}
                            for r in rows}
    return plans


def _plans(conn, where: str, params: tuple, order: str = "id") -> list[dict]:
    rows = conn.execute(f"SELECT * FROM plans WHERE {where} ORDER BY {order}", params).fetchall()
    return _with_blocks_done(conn, [_plan(r) for r in rows])


def start_plan(user_id: int, day: date, thread_id: str, checkin_at: datetime,
               chat_session_id: int | None = None, requested: dict | None = None, replace_open: bool = True) -> int:
    """Open a check-in. It replaces any older check-in or question waiting for an answer and, with
    `replace_open`, the day's plans with nothing done yet (a new check-in means a new plan)."""
    with connect() as conn:
        conn.execute("UPDATE plans SET status = 'abandoned' WHERE user_id = ? "
                     "AND status IN ('awaiting_checkin', 'awaiting_confirm')", (user_id,))
        if replace_open:
            conn.execute("UPDATE plans SET status = 'abandoned' WHERE user_id = ? AND date = ? AND status = 'planned' "
                         "AND completed IS NULL AND id NOT IN (SELECT plan_id FROM plan_blocks_done)",
                         (user_id, day.isoformat()))
        cur = conn.execute(
            "INSERT INTO plans (user_id, chat_session_id, date, checkin_at, thread_id, status, source, requested_json) "
            "VALUES (?, ?, ?, ?, ?, 'awaiting_checkin', 'planned', ?)",
            (user_id, chat_session_id, day.isoformat(), _iso(checkin_at), thread_id,
             json.dumps(requested) if requested else None),
        )
    return cur.lastrowid


def abandon_pending(user_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE plans SET status = 'abandoned' WHERE user_id = ? AND status = 'awaiting_checkin'",
                     (user_id,))


def pending_plan(user_id: int) -> dict | None:
    """The check-in waiting for an energy/soreness answer, if any (at most one)."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND status = 'awaiting_checkin'", (user_id,), "id DESC LIMIT 1")
    return found[0] if found else None


def thread_exists(thread_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM plans WHERE thread_id = ?", (thread_id,)).fetchone() is not None


def finish_plan(plan_id: int, checkin: dict, plan: dict, llm_plan: dict, overrides: list[dict],
                details: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE plans SET status = 'planned', energy = ?, soreness = ?, note = ?, plan_json = ?, "
            "llm_plan_json = ?, overridden = ?, override_reasons = ?, details_json = ? WHERE id = ?",
            (checkin.get("energy"), checkin.get("soreness"), checkin.get("note"), json.dumps(plan),
             json.dumps(llm_plan), int(bool(overrides)), json.dumps(overrides), json.dumps(details), plan_id),
        )


def get_plan(plan_id: int) -> dict | None:
    with connect() as conn:
        found = _plans(conn, "id = ?", (plan_id,))
    return found[0] if found else None


# --- a consult: "…I'd suggest a walk instead. Still want HIIT?" ----------------

def ask_confirm(plan_id: int, checkin: dict, requested: dict) -> None:
    """The plan they asked for breaks a rule: wait for their yes or no (requested_json holds the
    request and the code-built suggestion, so the question can expire without an LLM call)."""
    with connect() as conn:
        conn.execute("UPDATE plans SET status = 'awaiting_confirm', energy = ?, soreness = ?, note = ?, "
                     "requested_json = ? WHERE id = ?",
                     (checkin.get("energy"), checkin.get("soreness"), checkin.get("note"),
                      json.dumps(requested, default=str), plan_id))


def pending_confirm(user_id: int) -> dict | None:
    """The consult question waiting for a yes or no, if any (at most one)."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND status = 'awaiting_confirm'", (user_id,), "id DESC LIMIT 1")
    return found[0] if found else None


def expire_consults(user_id: int, now: datetime, ttl: timedelta) -> list[dict]:
    """Questions unanswered for `ttl` expire: status `expired`, no plan is made, nothing is sent, and
    nothing counts toward the score (only `planned` rows do). Returns them."""
    with connect() as conn:
        waiting = _plans(conn, "user_id = ? AND status = 'awaiting_confirm'", (user_id,))
        expired = [p for p in waiting if now - datetime.fromisoformat(p["requested_json"]["asked_at"]) >= ttl]
        for p in expired:
            conn.execute("UPDATE plans SET status = 'expired' WHERE id = ?", (p["id"],))
    return expired


def expired_consult(user_id: int, day: date) -> dict | None:
    """The day's latest question that expired unanswered, if any."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND date = ? AND status = 'expired'", (user_id, day.isoformat()),
                       "id DESC LIMIT 1")
    return found[0] if found else None


def latest_open_plan(user_id: int, day: date) -> dict | None:
    """The day's most recent plan that has not been marked done."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND date = ? AND status = 'planned' AND completed IS NULL",
                       (user_id, day.isoformat()), "id DESC LIMIT 1")
    return found[0] if found else None


def latest_planned(user_id: int, day: date) -> dict | None:
    """The day's most recent plan (done or not)."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND date = ? AND status = 'planned'", (user_id, day.isoformat()),
                       "id DESC LIMIT 1")
    return found[0] if found else None


def open_plans(user_id: int, day: date) -> list[dict]:
    """The day's plans not marked done, oldest first."""
    with connect() as conn:
        return _plans(conn, "user_id = ? AND date = ? AND status = 'planned' AND completed IS NULL",
                      (user_id, day.isoformat()))


def done_strength_plans(user_id: int) -> list[dict]:
    """Plans with strength in them and something done, oldest first (for the split rotation)."""
    with connect() as conn:
        return _plans(conn, "user_id = ? AND status = 'planned' AND plan_json LIKE '%\"strength\"%' "
                            "AND (completed = 1 OR id IN (SELECT plan_id FROM plan_blocks_done))",
                      (user_id,), "date, id")


def latest_checkin(user_id: int, day: date) -> dict | None:
    """The day's most recent plan whose check-in was answered."""
    with connect() as conn:
        found = _plans(conn, "user_id = ? AND date = ? AND status = 'planned' "
                             "AND (energy IS NOT NULL OR soreness IS NOT NULL)",
                       (user_id, day.isoformat()), "id DESC LIMIT 1")
    return found[0] if found else None


def count_planned(user_id: int, day: date) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM plans WHERE user_id = ? AND date = ? AND status = 'planned'",
                            (user_id, day.isoformat())).fetchone()[0]


def mark_block_done(plan_id: int, block_index: int, start: datetime, end: datetime, minutes: int,
                    done_by: str, done_at: datetime, feeling: str | None = None) -> None:
    """One block of a plan is done (a block already done keeps its first record). When every block
    is, the plan is completed: actual_start/end span the blocks, done_by is oura, chat or mixed."""
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO plan_blocks_done (plan_id, block_index, start, end, minutes, done_by, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (plan_id, block_index, _iso(start), _iso(end), minutes, done_by, _stamp()))
        if feeling:
            conn.execute("UPDATE plans SET feeling = ? WHERE id = ?", (feeling, plan_id))
        plan = _plans(conn, "id = ?", (plan_id,))[0]
        blocks = (plan["plan_json"] or {}).get("blocks") or [None]
        done = plan["blocks_done"]
        if len(done) < len(blocks):
            return
        by = {d["done_by"] for d in done.values()}
        starts = [datetime.fromisoformat(d["start"]) for d in done.values()]
        ends = [datetime.fromisoformat(d["end"]) for d in done.values()]
        conn.execute("UPDATE plans SET completed = 1, done_at = ?, actual_start = ?, actual_end = ?, done_by = ? "
                     "WHERE id = ?", (_iso(done_at), _iso(min(starts)), _iso(max(ends)),
                                      by.pop() if len(by) == 1 else "mixed", plan_id))


def set_feeling(plan_id: int, feeling: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE plans SET feeling = ? WHERE id = ?", (feeling, plan_id))


def mark_done(plan_id: int, feeling: str | None, done_at: datetime,
              actual_start: datetime | None, actual_end: datetime | None, done_by: str = "chat") -> None:
    """The whole plan done at once, without per-block records (older rows and tests)."""
    with connect() as conn:
        conn.execute(
            "UPDATE plans SET completed = 1, feeling = ?, done_at = ?, actual_start = ?, actual_end = ?, "
            "done_by = ? WHERE id = ?",
            (feeling, _iso(done_at), _iso(actual_start), _iso(actual_end), done_by, plan_id),
        )


def plans_between(user_id: int, start: date, end: date) -> list[dict]:
    """Plans with start <= date <= end (abandoned and pending ones excluded)."""
    with connect() as conn:
        return _plans(conn, "user_id = ? AND date BETWEEN ? AND ? AND status = 'planned'",
                      (user_id, start.isoformat(), end.isoformat()))


def delete_plan(plan_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))


# --- manual workouts (exercise Oura missed, added in chat) --------------------

def add_manual_workout(user_id: int, workout_type: str, start: datetime, end: datetime,
                       minutes: int | None, feeling: str | None, plan_id: int | None = None) -> dict:
    row = {"user_id": user_id, "workout_type": workout_type, "start": _iso(start), "end": _iso(end),
           "minutes": minutes, "feeling": feeling, "plan_id": plan_id, "created_at": _stamp()}
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO manual_workouts ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           list(row.values()))
    return {"id": cur.lastrowid, **row}


def manual_workouts_between(user_id: int, start: datetime, end: datetime) -> list[dict]:
    """Manual workouts that started in [start, end), oldest first."""
    return _rows_between("manual_workouts", "start", user_id, start, end)


def delete_manual_workout(workout_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM manual_workouts WHERE id = ?", (workout_id,))


def _rows_between(table: str, column: str, user_id: int, start: datetime, end: datetime) -> list[dict]:
    """Rows whose ISO-with-offset `column` falls in [start, end). Compared as datetimes, not strings,
    so a change of UTC offset (daylight saving) cannot misplace a row."""
    lo, hi = (start - timedelta(days=1)).date().isoformat(), (end + timedelta(days=1)).date().isoformat()
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM {table} WHERE user_id = ? AND substr({column}, 1, 10) BETWEEN ? AND ? "
                            f"ORDER BY {column}, id", (user_id, lo, hi)).fetchall()
    return [dict(r) for r in rows if start <= datetime.fromisoformat(r[column]) < end]


# --- saved memory -----------------------------------------------------------

def add_memory(user_id: int, text: str, kind: str, start_date: date | None = None,
               end_date: date | None = None) -> dict:
    row = {"user_id": user_id, "text": text, "kind": kind,
           "start_date": start_date.isoformat() if start_date else None,
           "end_date": end_date.isoformat() if end_date else None, "created_at": _stamp()}
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO memory ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           list(row.values()))
    return {"id": cur.lastrowid, **row}


def list_memory(user_id: int, day: date | None = None) -> list[dict]:
    """All facts, or (with `day`) the ones that apply that day: undated facts plus dated ones covering it."""
    with connect() as conn:
        if day is None:
            rows = conn.execute("SELECT * FROM memory WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        else:
            d = day.isoformat()
            rows = conn.execute(
                "SELECT * FROM memory WHERE user_id = ? AND (start_date IS NULL OR start_date <= ?) "
                "AND (end_date IS NULL OR end_date >= ?) ORDER BY id", (user_id, d, d)).fetchall()
    return [dict(r) for r in rows]


def delete_memory(user_id: int, memory_ids: list[int]) -> list[dict]:
    """Delete these facts (only the user's own) and return what was removed."""
    with connect() as conn:
        marks = ", ".join("?" * len(memory_ids))
        rows = conn.execute(f"SELECT * FROM memory WHERE user_id = ? AND id IN ({marks}) ORDER BY id",
                            (user_id, *memory_ids)).fetchall()
        conn.execute(f"DELETE FROM memory WHERE user_id = ? AND id IN ({marks})", (user_id, *memory_ids))
    return [dict(r) for r in rows]


# --- food log ---------------------------------------------------------------

def add_food(user_id: int, raw_text: str, estimate: dict, eaten_at: datetime,
             user_numbers: list[str] | None = None) -> dict:
    """Save one food entry (every call is a new row, repeats included) and return it."""
    row = {"user_id": user_id, "timestamp": _iso(eaten_at), "raw_text": raw_text,
           **{f: estimate.get(f) for f in FOOD_FIELDS}, "logged_at": _stamp(),
           "user_numbers_json": json.dumps(user_numbers or [])}
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO food_log ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           list(row.values()))
    return {"id": cur.lastrowid, **row}


def food_between(user_id: int, start: datetime, end: datetime) -> list[dict]:
    """Food eaten in [start, end), oldest first."""
    return _rows_between("food_log", "timestamp", user_id, start, end)


def get_food(user_id: int, entry_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM food_log WHERE user_id = ? AND id = ?", (user_id, entry_id)).fetchone()
    return dict(row) if row else None


def update_food(user_id: int, entry_id: int, fields: dict) -> dict | None:
    """Change fields of one entry in place (never a new row). Returns the updated entry."""
    allowed = set(FOOD_FIELDS) | {"raw_text", "timestamp", "user_numbers_json"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown food_log fields: {unknown}")
    values = {k: _iso(v) if isinstance(v, datetime) else v for k, v in fields.items()}
    values["updated_at"] = _stamp()
    with connect() as conn:
        conn.execute(f"UPDATE food_log SET {', '.join(f'{k} = ?' for k in values)} WHERE user_id = ? AND id = ?",
                     (*values.values(), user_id, entry_id))
    return get_food(user_id, entry_id)


def delete_food(user_id: int, entry_ids: list[int]) -> list[dict]:
    """Delete these entries (only the user's own) and return what was removed."""
    with connect() as conn:
        marks = ", ".join("?" * len(entry_ids))
        rows = conn.execute(f"SELECT * FROM food_log WHERE user_id = ? AND id IN ({marks}) ORDER BY timestamp",
                            (user_id, *entry_ids)).fetchall()
        conn.execute(f"DELETE FROM food_log WHERE user_id = ? AND id IN ({marks})", (user_id, *entry_ids))
    return [dict(r) for r in rows]


def last_food(user_id: int) -> dict | None:
    """The most recently saved entry."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM food_log WHERE user_id = ? ORDER BY logged_at DESC, id DESC LIMIT 1",
                           (user_id,)).fetchone()
    return dict(row) if row else None


# --- chat sessions and messages ---------------------------------------------

def latest_chat_session(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                           (user_id,)).fetchone()
    return dict(row) if row else None


def add_chat_session(user_id: int, started_at: datetime, thread_id: str) -> dict:
    with connect() as conn:
        cur = conn.execute("INSERT INTO chat_sessions (user_id, started_at, last_user_at, thread_id) "
                           "VALUES (?, ?, ?, ?)", (user_id, _iso(started_at), _iso(started_at), thread_id))
    return {"id": cur.lastrowid, "user_id": user_id, "started_at": _iso(started_at),
            "last_user_at": _iso(started_at), "thread_id": thread_id}


def touch_chat_session(session_id: int, at: datetime) -> None:
    with connect() as conn:
        conn.execute("UPDATE chat_sessions SET last_user_at = ? WHERE id = ?", (_iso(at), session_id))


def chat_thread_exists(thread_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM chat_sessions WHERE thread_id = ?", (thread_id,)).fetchone() is not None


def add_message(user_id: int, chat_session_id: int, ts: datetime, role: str, text: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO messages (user_id, chat_session_id, ts, role, text) VALUES (?, ?, ?, ?, ?)",
                     (user_id, chat_session_id, _iso(ts), role, text))


def session_messages(chat_session_id: int, limit: int | None = None) -> list[dict]:
    """The session's messages, oldest first (the newest `limit` if given)."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE chat_session_id = ? ORDER BY id DESC LIMIT ?",
                            (chat_session_id, -1 if limit is None else limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- Oura cache -------------------------------------------------------------

def save_oura_cache(user_id: int, start: date, end: date, raw: dict, fetched_at: datetime) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO oura_cache (user_id, fetched_at, start_date, end_date, raw_json) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET fetched_at = excluded.fetched_at, start_date = excluded.start_date, "
            "end_date = excluded.end_date, raw_json = excluded.raw_json",
            (user_id, _iso(fetched_at), start.isoformat(), end.isoformat(), json.dumps(raw)))


def get_oura_cache(user_id: int) -> dict | None:
    """{"fetched_at", "start_date", "end_date", "raw"} of the user's newest fetch, or None."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM oura_cache WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {"fetched_at": datetime.fromisoformat(row["fetched_at"]), "start_date": date.fromisoformat(row["start_date"]),
            "end_date": date.fromisoformat(row["end_date"]), "raw": json.loads(row["raw_json"])}


# --- legacy (Phase 1 only: the old /undo and context-note flow) ---------------

def undo_last(user_id: int) -> dict | None:
    """Delete the most recently saved food entry or dated memory fact. Returns what was removed."""
    with connect() as conn:
        food = conn.execute("SELECT * FROM food_log WHERE user_id = ? ORDER BY logged_at DESC, id DESC LIMIT 1",
                            (user_id,)).fetchone()
        note = conn.execute("SELECT * FROM memory WHERE user_id = ? AND kind = 'context' "
                            "ORDER BY created_at DESC, id DESC LIMIT 1", (user_id,)).fetchone()
        if food is None and note is None:
            return None
        if note is None or (food is not None and food["logged_at"] >= note["created_at"]):
            conn.execute("DELETE FROM food_log WHERE id = ?", (food["id"],))
            return {"kind": "food", **dict(food)}
        conn.execute("DELETE FROM memory WHERE id = ?", (note["id"],))
        return {"kind": "note", **dict(note)}


# --- LLM run traces ---------------------------------------------------------

def log_run(kind: str, model: str, input_data, output_data, user_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (user_id, created_at, kind, model, input_json, output_json) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, _now(), kind, model, json.dumps(input_data),
             None if output_data is None else json.dumps(output_data, default=str)),
        )


def recent_runs(limit: int = 5) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
