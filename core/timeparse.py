"""Turn the time and date phrases the router extracts into real datetimes. Pure functions.

The LLM only copies phrases out of the message ("at 1", "Thu", "Sat"); this module
decides what they mean, so the result is deterministic and testable.
"""
import re
from datetime import date, datetime, timedelta

WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MAX_LOOKBACK = timedelta(hours=12)

_CLOCK = re.compile(r"^(?:at|around|about|@|~)?\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?$")


def parse_clock_time(text: str | None, now: datetime) -> datetime | None:
    """A stated clock time -> the most recent past moment with that time, at most 12 h ago.

    "1" or "at 1" means 13:00 if it is already past 1 pm, else 01:00. "8am", "13:30" and
    "noon" are exact. Future times and anything unparseable give None (the caller then
    uses the message time).
    """
    if not text:
        return None
    text = text.strip().lower()
    if text in {"noon", "at noon", "midday"}:
        hour, minute, half = 12, 0, None
    else:
        match = _CLOCK.match(text)
        if not match:
            return None
        hour, minute, half = int(match[1]), int(match[2] or 0), match[3]
        if minute > 59 or hour > 23 or (half and not 1 <= hour <= 12):
            return None

    if half == "a":
        hours = [0 if hour == 12 else hour]
    elif half == "p":
        hours = [12 if hour == 12 else hour + 12]
    elif hour > 12 or hour == 0:
        hours = [hour]                              # 24-hour clock, unambiguous
    else:
        hours = [hour % 12, hour % 12 + 12]         # "1" could be 01:00 or 13:00

    candidates = []
    for days_back in (0, 1):
        day = now.date() - timedelta(days=days_back)
        for h in hours:
            moment = datetime.combine(day, datetime.min.time(), now.tzinfo).replace(hour=h, minute=minute)
            if moment <= now and now - moment <= MAX_LOOKBACK:
                candidates.append(moment)
    return max(candidates) if candidates else None


def _resolve_day(text: str, today: date, on_or_after: date) -> date | None:
    text = text.strip().lower().rstrip(".")
    if text in {"today", "tonight", "now"}:
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    words = text.split()
    if len(words) == 2 and words[0] == "next" and words[1] in WEEKDAYS:
        return _resolve_day(words[1], today, on_or_after) + timedelta(days=7)
    if text in WEEKDAYS:
        return on_or_after + timedelta(days=(WEEKDAYS[text] - on_or_after.weekday()) % 7)

    month = day_num = None
    if len(words) == 2 and words[0][:3] in MONTHS:
        month, day_num = MONTHS[words[0][:3]], words[1]               # "Oct 12"
    elif len(words) == 2 and words[1][:3] in MONTHS:
        month, day_num = MONTHS[words[1][:3]], words[0]               # "12 Oct"
    elif re.fullmatch(r"\d{1,2}/\d{1,2}", text):
        month, day_num = (int(x) for x in text.split("/"))           # "10/12" (US: month/day)
    if month is None:
        return None
    try:
        day_num = int(str(day_num).rstrip("stndrh"))
        resolved = date(today.year, month, day_num)
    except ValueError:
        return None
    if resolved < today:                                              # already passed: next year
        resolved = resolved.replace(year=today.year + 1)
    return resolved


def resolve_dates(start_text: str | None, end_text: str | None, today: date) -> tuple[date, date] | None:
    """Phrases from a context note -> an inclusive (start, end) date range.

    Weekdays mean the next occurrence on or after today (the end: on or after the start).
    "this weekend" = Sat-Sun. No start = today; no end = the start day. None if a phrase
    cannot be understood or the range runs backwards.
    """
    start_text = (start_text or "").strip().lower()
    end_text = (end_text or "").strip().lower()
    if start_text in {"this weekend", "weekend", "the weekend"} and not end_text:
        saturday = _resolve_day("sat", today, today)
        return (today if today.weekday() == 6 else saturday), (_resolve_day("sun", today, today))

    start = _resolve_day(start_text, today, today) if start_text else today
    if start is None:
        return None
    end = _resolve_day(end_text, today, start) if end_text else start
    if end is None or end < start:
        return None
    return start, end
