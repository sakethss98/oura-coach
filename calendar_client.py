"""Today's busy blocks and free slots from a Google Calendar iCal feed.

All times are returned in local time (config.TZ). Recurring events are
expanded by `recurring-ical-events`. All-day events are reported separately
and do not block time. Events marked "free" (TRANSP:TRANSPARENT) are ignored.
"""
from datetime import date, datetime, time, timedelta

import httpx
import icalendar
import recurring_ical_events

from config import DAY_END_HOUR, DAY_START_HOUR, FIXTURES_DIR, MIN_SLOT_MINUTES, TZ, env

CALENDAR_FIXTURE = FIXTURES_DIR / "calendar.ics"


def fetch_calendar_bytes() -> bytes:
    resp = httpx.get(env("GCAL_ICAL_URL"), timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def fetch_calendar() -> icalendar.Calendar:
    return icalendar.Calendar.from_ical(fetch_calendar_bytes())


def fixture_calendar() -> tuple[icalendar.Calendar, str]:
    """The feed saved by scripts/smoke_test.py (or an empty calendar), plus where it came from."""
    if CALENDAR_FIXTURE.exists():
        return icalendar.Calendar.from_ical(CALENDAR_FIXTURE.read_bytes()), "fixtures/calendar.ics"
    return icalendar.Calendar(), "none (no fixtures/calendar.ics, whole day treated as free)"


def _to_local(value: datetime) -> datetime:
    """Naive datetimes are floating times: treat them as local."""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def free_slots(busy: list[tuple[datetime, datetime]], window_start: datetime, window_end: datetime,
               min_minutes: int = MIN_SLOT_MINUTES) -> list[tuple[datetime, datetime]]:
    """Gaps of at least `min_minutes` inside the window that no busy block covers."""
    slots, cursor = [], window_start
    for start, end in sorted(busy):
        if start > cursor and start - cursor >= timedelta(minutes=min_minutes):
            slots.append((cursor, start))
        cursor = max(cursor, end)
    if window_end - cursor >= timedelta(minutes=min_minutes):
        slots.append((cursor, window_end))
    return slots


def plan_window(busy: list[tuple[datetime, datetime]], now: datetime, day_start: datetime,
                day_end: datetime, min_minutes: int = MIN_SLOT_MINUTES) -> tuple[datetime, datetime] | None:
    """The time available for a workout starting now.

    Starts now, or when the current event ends (chained through back-to-back events).
    Ends at the next event or at day_end. A gap shorter than `min_minutes` is skipped
    in favour of the next free gap. None if nothing is left today.
    """
    cursor = max(now, day_start)
    for start, end in sorted(busy):
        if end <= cursor:
            continue
        if start <= cursor:                     # in this event now: wait until it ends
            cursor = end
            continue
        if start - cursor >= timedelta(minutes=min_minutes):
            return cursor, start
        cursor = end                            # gap too short: look after this event
    if day_end - cursor >= timedelta(minutes=min_minutes):
        return cursor, day_end
    return None


def schedule_for(cal: icalendar.Calendar, day: date) -> dict:
    """Busy blocks, all-day events, and free slots for `day`."""
    window_start = datetime.combine(day, time(DAY_START_HOUR), TZ)
    window_end = datetime.combine(day, time(DAY_END_HOUR), TZ)

    # Query the full local day with aware datetimes so events that started
    # the night before (and overlap this morning) are included.
    day_start = datetime.combine(day, time.min, TZ)
    events = recurring_ical_events.of(cal).between(day_start, day_start + timedelta(days=1))

    busy, all_day = [], []
    for event in events:
        summary = str(event.get("SUMMARY", "(no title)"))
        if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue
        if not isinstance(event.start, datetime):  # a plain date means all-day
            all_day.append(summary)
            continue
        start, end = _to_local(event.start), _to_local(event.end)
        # Clip to the planning window; skip events entirely outside it.
        start, end = max(start, window_start), min(end, window_end)
        if start < end:
            busy.append((start, end, summary))

    busy.sort()
    return {
        "busy": busy,
        "all_day": all_day,
        "free": free_slots([(s, e) for s, e, _ in busy], window_start, window_end),
    }


def today_schedule() -> dict:
    return schedule_for(fetch_calendar(), datetime.now(TZ).date())
