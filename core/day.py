"""Which day an event belongs to. Oura is the source of truth.

A day starts at midnight, or later if that night's main sleep (Oura `long_sleep`, whose
`day` is the day you wake up) started after midnight: a 1am snack before a 2am bedtime still
belongs to the day before. Anything after you went to bed belongs to the new day.
Without Oura sleep for that night, the day starts at local midnight.

Sleep comes from the cached Oura fetches (`oura_cache`, `oura_history_cache`); this module never calls Oura. The day of
a food entry is worked out when it is read, so a late snack logged before Oura synced lands on
the right day once it has.
"""
from datetime import date, datetime, time, timedelta

from core import db
from core.users import User


def _main_sleep_starts(user: User) -> dict[date, datetime]:
    """{wake-up day: local bedtime start} of each cached main sleep (the range fetch for history
    questions first, so the newer regular fetch wins where both have a night)."""
    starts = {}
    for cache in (db.get_oura_history(user.id), db.get_oura_cache(user.id)):
        if cache is None:
            continue
        starts.update({
            date.fromisoformat(s["day"]): datetime.fromisoformat(s["bedtime_start"]).astimezone(user.tz)
            for s in cache["raw"].get("sleep", {}).get("data", [])
            if s.get("type") == "long_sleep" and s.get("bedtime_start")
        })
    return starts


def day_start(user: User, day: date, sleeps: dict[date, datetime] | None = None) -> datetime:
    """When `day` begins: local midnight, or that night's bedtime if it was after midnight."""
    midnight = datetime.combine(day, time.min, user.tz)
    bedtime = (sleeps if sleeps is not None else _main_sleep_starts(user)).get(day)
    return max(midnight, bedtime) if bedtime else midnight


def day_bounds(user: User, day: date) -> tuple[datetime, datetime]:
    """[start, end) of `day` as local datetimes."""
    sleeps = _main_sleep_starts(user)
    return day_start(user, day, sleeps), day_start(user, day + timedelta(days=1), sleeps)


def day_of(user: User, ts: datetime) -> date:
    """The day `ts` belongs to."""
    local = ts.astimezone(user.tz)
    calendar_day = local.date()
    return calendar_day - timedelta(days=1) if local < day_start(user, calendar_day) else calendar_day


def bounds_of_days(user: User, days: list[date]) -> dict[date, tuple[datetime, datetime]]:
    """day_bounds for many days, reading the caches once (history questions cover up to 90 days)."""
    sleeps = _main_sleep_starts(user)
    return {d: (day_start(user, d, sleeps), day_start(user, d + timedelta(days=1), sleeps)) for d in days}
