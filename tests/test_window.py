from datetime import datetime

from calendar_client import plan_window
from config import TZ


def t(hour, minute=0):
    return datetime(2026, 10, 7, hour, minute, tzinfo=TZ)


DAY = (t(6), t(21))


def window(busy, now):
    return plan_window(busy, now, *DAY)


def test_free_now_until_next_event():
    assert window([(t(9), t(10))], t(7)) == (t(7), t(9))


def test_mid_event_starts_when_it_ends():
    assert window([(t(9), t(10)), (t(12), t(13))], t(9, 30)) == (t(10), t(12))


def test_back_to_back_events_are_chained():
    assert window([(t(9), t(10)), (t(10), t(11)), (t(14), t(15))], t(9, 30)) == (t(11), t(14))


def test_short_gap_jumps_to_next_free_gap():
    # 07:00-07:20 is too short; next gap is 08:00-12:00
    assert window([(t(7, 20), t(8)), (t(12), t(13))], t(7)) == (t(8), t(12))


def test_open_until_end_of_day():
    assert window([], t(18)) == (t(18), t(21))


def test_nothing_left_today():
    assert window([], t(20, 45)) is None
    assert window([], t(22)) is None


def test_before_day_start_waits_for_six():
    assert window([], t(5)) == (t(6), t(21))
