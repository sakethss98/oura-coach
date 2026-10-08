"""An M2-shaped database is backed up, then migrated to the M3 tables."""
import json
from datetime import date
import sqlite3

import pytest

import db
from config import DB_PATH

M2_SCHEMA = """
CREATE TABLE daily_log (date TEXT PRIMARY KEY, metrics_json TEXT, food_notes TEXT, checkin_json TEXT,
    plan TEXT, policy_json TEXT, completed INTEGER, feeling TEXT, updated_at TEXT NOT NULL);
CREATE TABLE food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, raw_text TEXT NOT NULL,
    entry_type TEXT, est_protein_g REAL, est_fiber_g REAL, est_carbs_g REAL, est_calories REAL,
    est_caffeine_mg REAL, alcohol_drinks REAL, confidence TEXT);
"""
PLAN = {"workout_type": "HIIT", "intensity": 4, "time_slot": "06:30-07:10", "duration_min": 40}


@pytest.fixture
def m2_db(tmp_path):
    path = tmp_path / "oura_coach.db"
    conn = sqlite3.connect(path)
    conn.executescript(M2_SCHEMA)
    conn.execute("INSERT INTO daily_log VALUES ('2026-10-06', '{}', NULL, ?, ?, ?, NULL, NULL, '2026-10-06T07:00:00')",
                 (json.dumps({"energy": 4, "soreness": 2}), json.dumps(PLAN),
                  json.dumps({"llm_plan": PLAN, "overrides": [], "overridden": False})))
    conn.execute("INSERT INTO food_log (timestamp, raw_text, est_calories) VALUES ('2026-10-06T08:00:00-05:00', 'poha', 300)")
    conn.commit()
    conn.close()
    db.set_db_path(path)
    yield path
    db.set_db_path(DB_PATH)


def columns(path, table):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def backups(path):
    return list(path.parent.glob("oura_coach.backup-*.db"))


def test_migration_backs_up_first_and_moves_plans(m2_db):
    db.init_db()

    [backup] = backups(m2_db)
    assert "plan" in columns(backup, "daily_log")           # backup is the untouched M2 database
    assert columns(m2_db, "daily_log") == {"date", "metrics_json", "food_targets_json", "updated_at"}
    assert "logged_at" in columns(m2_db, "food_log")

    [session] = db.sessions_between(date(2026, 10, 6), date(2026, 10, 6))
    assert session["plan_json"] == PLAN
    assert (session["energy"], session["soreness"], session["status"]) == (4, 2, "planned")
    assert db.food_entries(date(2026, 10, 6))[0]["logged_at"] == "2026-10-06T08:00:00-05:00"


def test_second_init_changes_nothing_and_makes_no_second_backup(m2_db):
    db.init_db()
    db.init_db()
    assert len(backups(m2_db)) == 1


def test_failed_backup_leaves_database_untouched(m2_db, monkeypatch):
    def broken_backup():
        raise OSError("disk full")
    monkeypatch.setattr(db, "backup_db", broken_backup)
    with pytest.raises(OSError):
        db.init_db()
    assert "plan" in columns(m2_db, "daily_log")
    assert "sessions" not in {r[0] for r in sqlite3.connect(m2_db).execute("SELECT name FROM sqlite_master")}


def test_fresh_database_needs_no_backup(tmp_path):
    db.set_db_path(tmp_path / "oura_coach.db")
    try:
        db.init_db()
        assert backups(tmp_path / "oura_coach.db") == []
    finally:
        db.set_db_path(DB_PATH)
