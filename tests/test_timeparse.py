from datetime import date, datetime

from config import TZ
from timeparse import parse_clock_time, resolve_dates

WEDNESDAY = date(2026, 10, 7)


def at(hour, minute=0, day=WEDNESDAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_bare_hour_is_most_recent_past():
    assert parse_clock_time("at 1", at(14, 10)) == at(13)
    assert parse_clock_time("1", at(9)) == at(1)           # 13:00 is still in the future


def test_explicit_times():
    assert parse_clock_time("8am", at(10)) == at(8)
    assert parse_clock_time("8 pm", at(21)) == at(20)
    assert parse_clock_time("13:30", at(15)) == at(13, 30)
    assert parse_clock_time("1:30", at(15)) == at(13, 30)
    assert parse_clock_time("noon", at(15)) == at(12)


def test_future_or_too_old_times_are_rejected():
    assert parse_clock_time("8pm", at(10)) is None          # later today, and yesterday 20:00 is > 12 h ago
    assert parse_clock_time("11", at(9)) == at(23, day=date(2026, 10, 6))  # last night, within 12 h


def test_no_or_unreadable_time():
    assert parse_clock_time(None, at(10)) is None
    assert parse_clock_time("after the run", at(10)) is None
    assert parse_clock_time("25:00", at(10)) is None


def test_weekday_range_from_wednesday():
    assert resolve_dates("Thu", "Sat", WEDNESDAY) == (date(2026, 10, 8), date(2026, 10, 10))


def test_same_weekday_means_today():
    assert resolve_dates("wednesday", None, WEDNESDAY) == (WEDNESDAY, WEDNESDAY)


def test_relative_and_calendar_dates():
    assert resolve_dates("tomorrow", None, WEDNESDAY) == (date(2026, 10, 8),) * 2
    assert resolve_dates("this weekend", None, WEDNESDAY) == (date(2026, 10, 10), date(2026, 10, 11))
    assert resolve_dates("Oct 12", "Oct 14", WEDNESDAY) == (date(2026, 10, 12), date(2026, 10, 14))
    assert resolve_dates("10/12", None, WEDNESDAY) == (date(2026, 10, 12),) * 2
    assert resolve_dates(None, "Fri", WEDNESDAY) == (WEDNESDAY, date(2026, 10, 9))
    assert resolve_dates("next fri", None, WEDNESDAY) == (date(2026, 10, 16),) * 2


def test_no_dates_means_today():
    assert resolve_dates(None, None, WEDNESDAY) == (WEDNESDAY, WEDNESDAY)


def test_unreadable_dates():
    assert resolve_dates("someday", None, WEDNESDAY) is None
    assert resolve_dates("Oct 40", None, WEDNESDAY) is None
