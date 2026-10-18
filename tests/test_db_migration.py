"""Older databases (M2, M3.1) are backed up, then migrated to M3.2 in one transaction."""
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from core import db
from core.config import DB_PATH, TZ

M2_SCHEMA = """
CREATE TABLE daily_log (date TEXT PRIMARY KEY, metrics_json TEXT, food_notes TEXT, checkin_json TEXT,
    plan TEXT, policy_json TEXT, completed INTEGER, feeling TEXT, updated_at TEXT NOT NULL);
CREATE TABLE food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, raw_text TEXT NOT NULL,
    entry_type TEXT, est_protein_g REAL, est_fiber_g REAL, est_carbs_g REAL, est_calories REAL,
    est_caffeine_mg REAL, alcohol_drinks REAL, confidence TEXT);
"""

# The live M3.1 schema (copied from oura_coach.db on 2026-10-16).
M31_SCHEMA = """
CREATE TABLE tokens (id INTEGER PRIMARY KEY CHECK (id = 1), access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL, expires_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE daily_log (date TEXT PRIMARY KEY, metrics_json TEXT, updated_at TEXT NOT NULL, food_targets_json TEXT);
CREATE TABLE context_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL, start_date TEXT NOT NULL,
    end_date TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, raw_text TEXT NOT NULL,
    entry_type TEXT, est_protein_g REAL, est_fiber_g REAL, est_carbs_g REAL, est_calories REAL,
    est_caffeine_mg REAL, alcohol_drinks REAL, confidence TEXT, logged_at TEXT);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL,
    model TEXT NOT NULL, input_json TEXT NOT NULL, output_json TEXT);
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, checkin_at TEXT, thread_id TEXT,
    status TEXT NOT NULL, source TEXT NOT NULL, energy INTEGER, soreness INTEGER, note TEXT, plan_json TEXT,
    llm_plan_json TEXT, overridden INTEGER, override_reasons TEXT, completed INTEGER, feeling TEXT, done_at TEXT,
    actual_start TEXT, actual_end TEXT, done_by TEXT, details_json TEXT);
"""
PLAN = {"workout_type": "HIIT", "intensity": 4, "time_slot": "06:30-07:10", "duration_min": 40}
TOKENS = ("access-abc", "refresh-single-use-xyz", "2026-10-08T10:00:00", "2026-10-08T09:00:00")


def columns(path, table):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def backups(path):
    return list(path.parent.glob("oura_coach.backup-*.db"))


@pytest.fixture
def at_path(tmp_path):
    path = tmp_path / "oura_coach.db"
    db.set_db_path(path)
    yield path
    db.set_db_path(DB_PATH)


@pytest.fixture
def m2_db(at_path):
    conn = sqlite3.connect(at_path)
    conn.executescript(M2_SCHEMA)
    conn.execute("INSERT INTO daily_log VALUES ('2026-10-06', '{}', NULL, ?, ?, ?, NULL, NULL, '2026-10-06T07:00:00')",
                 (json.dumps({"energy": 4, "soreness": 2}), json.dumps(PLAN),
                  json.dumps({"llm_plan": PLAN, "overrides": [], "overridden": False})))
    conn.execute("INSERT INTO food_log (timestamp, raw_text, est_calories) VALUES ('2026-10-06T08:00:00-05:00', 'poha', 300)")
    conn.commit()
    conn.close()
    return at_path


@pytest.fixture
def m31_db(at_path):
    today = datetime.now(TZ).date()
    conn = sqlite3.connect(at_path)
    conn.executescript(M31_SCHEMA)
    conn.execute("INSERT INTO tokens VALUES (1, ?, ?, ?, ?)", TOKENS)
    conn.execute("INSERT INTO daily_log VALUES ('2026-10-07', '{\"x\": 1}', '2026-10-07T07:00:00', '{\"t\": 2}')")
    conn.execute("INSERT INTO food_log (timestamp, raw_text, est_calories, logged_at) "
                 "VALUES ('2026-10-07T08:00:00-05:00', 'poha', 300, '2026-10-07T08:00:01-05:00')")
    conn.execute("INSERT INTO runs (created_at, kind, model, input_json) VALUES ('2026-10-07', 'food', 'm', '{}')")
    conn.execute("INSERT INTO sessions (date, status, source, plan_json, completed) VALUES "
                 "('2026-10-07', 'planned', 'planned', ?, 1)", (json.dumps(PLAN),))
    conn.execute("INSERT INTO sessions (date, status, source) VALUES ('2026-10-07', 'abandoned', 'planned')")
    conn.execute("INSERT INTO sessions (date, status, source, plan_json, completed, feeling, done_at, actual_start, "
                 "actual_end, done_by) VALUES ('2026-10-07', 'unplanned', 'unplanned', ?, 1, 'easy', "
                 "'2026-10-07T17:00:00-05:00', '2026-10-07T16:30:00-05:00', '2026-10-07T17:00:00-05:00', 'chat')",
                 (json.dumps({"workout_type": "walk", "duration_min": 30}),))
    conn.execute("INSERT INTO context_notes (note, start_date, end_date, created_at) VALUES "
                 "('correction on that it is 3g fiber', '2026-10-08', '2026-10-08', '2026-10-08T15:38:14')")
    conn.execute("INSERT INTO context_notes (note, start_date, end_date, created_at) VALUES "
                 "('traveling, keep it light', ?, ?, '2026-10-08T15:40:00')",
                 (today.isoformat(), (today + timedelta(days=2)).isoformat()))
    conn.commit()
    conn.close()
    return at_path


def test_m31_database_is_backed_up_then_migrated(m31_db):
    db.init_db()

    [backup] = backups(m31_db)
    assert "sessions" in tables(backup) and "users" not in tables(backup)     # backup = untouched M3.1
    assert "sessions" not in tables(m31_db)
    assert {"users", "user_settings", "plans", "manual_workouts", "memory", "messages", "chat_sessions",
            "oura_cache", "daily_scores", "meal_suggestions"} <= tables(m31_db)

    # Oura tokens: byte for byte the same row
    with sqlite3.connect(m31_db) as conn:
        assert conn.execute("SELECT access_token, refresh_token, expires_at, updated_at FROM tokens").fetchall() \
            == [TOKENS]

    # plans: the two planned rows stay, the unplanned walk becomes a manual workout
    [planned] = db.plans_between(1, date(2026, 10, 7), date(2026, 10, 7))
    assert planned["plan_json"] == PLAN and planned["user_id"] == 1
    [walk] = db.manual_workouts_between(1, datetime(2026, 10, 7, tzinfo=TZ), datetime(2026, 10, 8, tzinfo=TZ))
    assert (walk["workout_type"], walk["minutes"], walk["feeling"]) == ("walk", 30, "easy")
    assert walk["start"] == "2026-10-07T16:30:00-05:00"

    # food, daily log and runs belong to user 1
    [food] = db.food_between(1, datetime(2026, 10, 7, tzinfo=TZ), datetime(2026, 10, 8, tzinfo=TZ))
    assert food["raw_text"] == "poha" and food["logged_at"] == "2026-10-07T08:00:01-05:00"
    assert json.loads(db.get_daily_log(1, date(2026, 10, 7))["food_targets_json"]) == {"t": 2}
    with sqlite3.connect(m31_db) as conn:
        assert conn.execute("SELECT user_id FROM runs").fetchall() == [(1,)]

    # memory: seeded diet facts + only the still-active context note
    texts = [m["text"] for m in db.list_memory(1)]
    assert texts == ["traveling, keep it light", "Vegetarian, eats eggs", "Prefers Indian food"]
    assert "context_notes" in tables(m31_db)                                 # left in place, read-only


def test_m2_database_migrates_all_the_way(m2_db):
    db.init_db()
    assert len(backups(m2_db)) == 1
    assert columns(m2_db, "daily_log") == {"user_id", "date", "metrics_json", "food_targets_json", "updated_at"}
    [plan] = db.plans_between(1, date(2026, 10, 6), date(2026, 10, 6))
    assert plan["plan_json"] == PLAN
    assert (plan["energy"], plan["soreness"], plan["status"]) == (4, 2, "planned")
    [food] = db.food_between(1, datetime(2026, 10, 6, tzinfo=TZ), datetime(2026, 10, 7, tzinfo=TZ))
    assert food["logged_at"] == "2026-10-06T08:00:00-05:00"


def test_second_init_changes_nothing_and_makes_no_second_backup(m31_db):
    db.init_db()
    db.init_db()
    assert len(backups(m31_db)) == 1
    assert len(db.list_memory(1)) == 3                                        # seeding happens once


def test_failed_backup_leaves_database_untouched(m31_db, monkeypatch):
    def broken_backup():
        raise OSError("disk full")
    monkeypatch.setattr(db, "backup_db", broken_backup)
    with pytest.raises(OSError):
        db.init_db()
    assert "sessions" in tables(m31_db) and "users" not in tables(m31_db)


def test_failed_row_check_rolls_everything_back(m31_db, monkeypatch):
    real_copy = db._m31_copy_rows

    def lose_a_row(conn):
        real_copy(conn)
        conn.execute("DELETE FROM food_log")
    monkeypatch.setattr(db, "_m31_copy_rows", lose_a_row)
    with pytest.raises(RuntimeError, match="row check failed"):
        db.init_db()
    assert "sessions" in tables(m31_db) and "users" not in tables(m31_db)    # transaction rolled back
    assert len(backups(m31_db)) == 1


def test_fresh_database_needs_no_backup_and_seeds_user_1(at_path):
    db.init_db()
    assert backups(at_path) == []
    row = db.get_user_row(1)
    assert row["timezone"] == "America/Chicago" and json.loads(row["enabled_modules"]) == ["training", "nutrition"]
    assert row["score_cutoff"] == "21:00" and row["session_gap_min"] == 15
    assert [m["text"] for m in db.list_memory(1)] == ["Vegetarian, eats eggs", "Prefers Indian food"]
