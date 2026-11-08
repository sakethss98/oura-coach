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


_DAYS_AGO = re.compile(r"^(\d{1,2}|a|one|two|three|four|five|six|seven)\s+days?\s+ago$")
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def resolve_past_day(text: str | None, today: date) -> date | None:
    """A phrase about a day that has happened -> that date (looking back, never forward).

    "today", "yesterday", "3 days ago"; a weekday = its most recent occurrence ("Monday" on a Monday =
    today), "last Monday" = the one before that; "Oct 10", "10 Oct", "10/10" and ISO dates = this year,
    or last year if that date is still ahead. None if it can't be read or lies in the future.
    """
    text = (text or "today").strip().lower().rstrip(".")
    if text in {"today", "tonight", "now", "this morning"}:
        return today
    if text in {"yesterday", "last night"}:
        return today - timedelta(days=1)
    ago = _DAYS_AGO.match(text)
    if ago:
        n = _NUMBER_WORDS.get(ago[1]) or int(ago[1])
        return today - timedelta(days=n)
    words = text.split()
    if words and words[-1] in WEEKDAYS and len(words) <= 2 and words[0] in {words[-1], "last", "on", "this"}:
        back = (today.weekday() - WEEKDAYS[words[-1]]) % 7
        if words[0] == "last" and back == 0:
            back = 7
        return today - timedelta(days=back)
    try:
        found = date.fromisoformat(text)
    except ValueError:
        if words[0] == "next" or text == "tomorrow":
            return None
        found = _resolve_day(text, today, today)            # "Oct 10": the next one on or after today...
        if found is None:
            return None
        if found > today:                                    # ...so step back to the one that has happened
            found = found.replace(year=found.year - 1)
            if found > today:
                return None
    return found if found <= today else None


MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7
_LAST_N = re.compile(r"^(?:the\s+)?(?:last|past)\s+(\d{1,3}|a|one|two|three|four|five|six|seven)\s+(days?|weeks?|months?)$")


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _whole_range(text: str, today: date) -> tuple[date, date] | None:
    """A phrase that names a whole range by itself ("this week", "last month", "last 14 days")."""
    monday = today - timedelta(days=today.weekday())
    if text in {"this week", "the week", "week"}:
        return monday, today
    if text in {"the last week", "past week", "the past week"}:
        return today - timedelta(days=6), today          # "over the last week" = the 7 days ending today
    if text in {"last week", "previous week", "the previous week"}:
        return monday - timedelta(days=7), monday - timedelta(days=1)   # "this week vs last week"
    if text in {"this month", "the month", "month"}:
        return _month_start(today), today
    if text in {"last month", "previous month", "the last month"}:
        end = _month_start(today) - timedelta(days=1)
        return _month_start(end), end
    match = _LAST_N.match(text)
    if match:
        n = _NUMBER_WORDS.get(match[1]) or int(match[1])
        days = n * {"day": 1, "week": 7, "month": 30}[match[2].rstrip("s")]
        return today - timedelta(days=days - 1), today
    return None


def resolve_range(days_back: int | None, start_text: str | None, end_text: str | None,
                  today: date) -> tuple[date, date, str | None] | None:
    """A past range as the person said it -> (first day, last day, note), both inclusive, never in the future.

    days_back N = the N days ending today. start_text may name a whole range by itself ("this week" =
    Monday..today, "last week" = the Monday-Sunday before it, "the last week" / "past week" = the 7 days
    ending today, "this month", "last month",
    "last 14 days"); otherwise start_text and end_text are single past days ("Oct 1", "last Monday"), and a
    missing end is today. Nothing given = the last 7 days. Ranges longer than 90 days keep the newest
    90 and say so in the note. None if a phrase can't be read or the range runs backwards.
    """
    start_text = (start_text or "").strip().lower().rstrip(".")
    end_text = (end_text or "").strip().lower().rstrip(".")
    if days_back:
        start, end = today - timedelta(days=max(days_back, 1) - 1), today
    elif start_text and not end_text and (whole := _whole_range(start_text, today)):
        start, end = whole
    elif start_text:
        start = resolve_past_day(start_text, today)
        end = resolve_past_day(end_text, today) if end_text else today
        if start is None or end is None or end < start:
            return None
    else:
        start, end = today - timedelta(days=DEFAULT_RANGE_DAYS - 1), today
    note = None
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        note = f"Only the last {MAX_RANGE_DAYS} days are covered."
    return start, end, note
