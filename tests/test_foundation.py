"""M3.2 Phase 1: day boundaries, chat sessions, saved memory, Oura cache, manual workouts, registry, users."""
from datetime import date, datetime, timedelta

import pytest

from connectors import oura
from connectors.health import Health
from connectors.manual import ManualSource
from core import chat_sessions, db, memory, users
from core.config import TZ
from core.day import day_bounds, day_of
from modules.registry import enabled_modules
from tests.conftest import needs_fixtures


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def sleep(day, start, kind="long_sleep"):
    """A sleep record with the fields core.day reads (shape as in fixtures/sleep.json)."""
    return {"day": day, "type": kind, "bedtime_start": start, "bedtime_end": start}


@pytest.fixture
def cached_sleep(user):
    raw = {"sleep": {"data": [
        sleep("2026-10-03", "2026-10-03T00:27:29.000-05:00"),   # went to bed after midnight
        sleep("2026-10-04", "2026-10-04T13:47:26.000-05:00", kind="sleep"),   # a nap: ignored
        sleep("2026-10-06", "2026-10-05T21:53:00.000-05:00"),   # went to bed before midnight
    ]}}
    db.save_oura_cache(user.id, date(2026, 9, 30), date(2026, 10, 8), raw, at(10, 7, 9))
    return raw


# --- day boundaries -----------------------------------------------------------

def test_snack_after_midnight_before_bed_belongs_to_the_day_before(user, cached_sleep):
    assert day_of(user, at(10, 3, 0, 15)) == date(2026, 10, 2)
    assert day_of(user, at(10, 3, 0, 30)) == date(2026, 10, 3)       # after bedtime: the new day
    assert day_of(user, at(10, 3, 9)) == date(2026, 10, 3)
    assert day_bounds(user, date(2026, 10, 2)) == (at(10, 2, 0), datetime.fromisoformat("2026-10-03T00:27:29-05:00"))


def test_bedtime_before_midnight_means_the_day_starts_at_midnight(user, cached_sleep):
    assert day_of(user, at(10, 5, 23)) == date(2026, 10, 5)
    assert day_of(user, at(10, 6, 0, 10)) == date(2026, 10, 6)


def test_without_oura_sleep_it_is_the_calendar_day(user):
    assert day_of(user, at(10, 3, 0, 15)) == date(2026, 10, 3)
    assert day_bounds(user, date(2026, 10, 3)) == (at(10, 3, 0), at(10, 4, 0))


def test_food_totals_follow_the_oura_day(user, cached_sleep):
    estimate = {"est_calories": 300}
    db.add_food(user.id, "late snack", estimate, at(10, 3, 0, 15))
    db.add_food(user.id, "breakfast", estimate, at(10, 3, 8))
    assert [f["raw_text"] for f in db.food_between(user.id, *day_bounds(user, date(2026, 10, 2)))] == ["late snack"]
    assert [f["raw_text"] for f in db.food_between(user.id, *day_bounds(user, date(2026, 10, 3)))] == ["breakfast"]


# --- chat sessions ------------------------------------------------------------

def test_a_gap_over_15_minutes_starts_a_new_session(user):
    first, new = chat_sessions.touch(user, at(10, 7, 9))
    assert new and first["thread_id"] == "session-1-202610070900"
    same, new = chat_sessions.touch(user, at(10, 7, 9, 15))          # 15 min gap: same session
    assert not new and same["id"] == first["id"]
    later, new = chat_sessions.touch(user, at(10, 7, 9, 31))         # 16 min after the last message
    assert new and later["id"] != first["id"]


def test_history_is_the_sessions_last_messages_only(user):
    old, _ = chat_sessions.touch(user, at(10, 7, 8))
    chat_sessions.log(user, old, at(10, 7, 8), "user", "from the old session")
    session, _ = chat_sessions.touch(user, at(10, 7, 9))
    for i in range(12):
        chat_sessions.log(user, session, at(10, 7, 9, i), "user" if i % 2 == 0 else "assistant", f"m{i}")
    history = chat_sessions.history(session)
    assert [m["text"] for m in history] == [f"m{i}" for i in range(2, 12)]    # newest 10, oldest first


# --- saved memory ---------------------------------------------------------------

def test_dated_facts_apply_only_on_their_days(user):
    memory.remember(user, "Traveling, keep it light", "context", date(2026, 10, 8), date(2026, 10, 10))
    assert "Traveling, keep it light (Thu Oct 08 - Sat Oct 10)" in memory.for_prompt(user, date(2026, 10, 9))
    assert memory.for_prompt(user, date(2026, 10, 11)) == ["Vegetarian, eats eggs", "Prefers Indian food"]


def test_forget_removes_and_returns_the_fact(user):
    fact = memory.remember(user, "I eat eggs", "diet")
    assert [f["text"] for f in memory.forget(user, [fact["id"]])] == ["I eat eggs"]
    assert "I eat eggs" not in [f["text"] for f in memory.facts(user)]
    assert memory.forget(user, [fact["id"]]) == []                     # already gone


def test_users_cannot_touch_each_others_rows(user):
    fact = memory.remember(user, "mine", "preference")
    assert db.delete_memory(2, [fact["id"]]) == []
    entry = db.add_food(user.id, "poha", {"est_calories": 300}, at(10, 7, 8))
    assert db.delete_food(2, [entry["id"]]) == [] and db.get_food(user.id, entry["id"])


# --- connectors -------------------------------------------------------------------

@needs_fixtures
def test_oura_source_caches_for_ten_minutes(user, monkeypatch):
    loads = []
    real = oura.load_fixtures
    monkeypatch.setattr(oura, "load_fixtures", lambda: loads.append(1) or real())
    source = oura.OuraSource(use_fixtures=True)
    day = oura.latest_day(real())
    now = datetime.combine(day, datetime.min.time(), TZ).replace(hour=9)
    source.raw(user, day, now=now)
    source.raw(user, day, now=now + timedelta(minutes=9))
    assert len(loads) == 1
    source.raw(user, day, now=now + timedelta(minutes=11))
    assert len(loads) == 2
    assert source.refresh(user, day, now=now + timedelta(minutes=12)) is False   # fixtures never change


def test_only_user_1_has_oura_tokens(user):
    other = users.User(**{**user.__dict__, "id": 2})
    with pytest.raises(oura.OuraAuthError):
        oura.OuraSource().raw(other, date(2026, 10, 7))


@needs_fixtures
def test_bundle_includes_manual_workouts(user):
    day = oura.latest_day(oura.load_fixtures())
    start = datetime.combine(day, datetime.min.time(), TZ).replace(hour=6)
    ManualSource().add_workout(user, "walk", start, start + timedelta(minutes=30), 30, "easy")
    bundle = Health(use_fixtures=True).bundle(user, day, now=start + timedelta(hours=3))
    [walk] = bundle["manual_by_day"][day.isoformat()]
    assert (walk["source"], walk["workout_type"], walk["minutes"]) == ("manual", "walk", 30)
    assert len(bundle["history"]) == 7 and bundle["today"]["day"] == day.isoformat()


# --- registry and users -------------------------------------------------------------

def test_registry_loads_only_enabled_modules(user):
    assert [m.name for m in enabled_modules(user)] == ["training", "nutrition"]
    only_food = users.User(**{**user.__dict__, "enabled_modules": ("nutrition",)})
    assert [m.name for m in enabled_modules(only_food)] == ["nutrition"]


def test_module_contexts_are_short_plain_summaries(user):
    training, nutrition = enabled_modules(user)
    assert training.context(user, date(2026, 10, 7)) == "No workout planned today."
    assert nutrition.context(user, date(2026, 10, 7)) == "No food logged today."


def test_telegram_chat_maps_to_its_user(user):
    db_row = db.get_user_row(1)
    if db_row["telegram_chat_id"]:
        assert users.for_telegram_chat(db_row["telegram_chat_id"]).id == 1
    assert users.for_telegram_chat("999999999999") is None
