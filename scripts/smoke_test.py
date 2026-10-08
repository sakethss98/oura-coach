"""M1 smoke test.

1. Pull the last 7 days from Oura and save raw responses to fixtures/.
2. Print a readable per-day summary.
3. Print today's busy blocks and free slots from Google Calendar
   (the raw feed is saved to fixtures/calendar.ics for offline runs; the URL is never saved).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import icalendar  # noqa: E402

from calendar_client import CALENDAR_FIXTURE, fetch_calendar_bytes, schedule_for  # noqa: E402
from config import FIXTURES_DIR, TZ  # noqa: E402
from oura_client import OuraClient, daily_metrics  # noqa: E402


def save_fixture(name: str, payload: dict) -> None:
    FIXTURES_DIR.mkdir(exist_ok=True)
    (FIXTURES_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2))


def fmt(value, suffix=""):
    return "-" if value is None else f"{value}{suffix}"


def print_oura_summary(raw: dict, days: list) -> None:
    header = f"{'day':<11}{'ready':>6}{'sleep':>6}{'hrs':>5}{'hrv':>5}{'rhr':>5}{'hrAvg':>6}{'steps':>7}  {'stress':<10} workouts"
    print(header)
    print("-" * len(header))
    for day in days:
        m = daily_metrics(raw, day)
        workouts = ", ".join(f"{w['activity']} {w['minutes']}m" for w in m["workouts"]) or "-"
        print(
            f"{m['day']:<11}{fmt(m['readiness_score']):>6}{fmt(m['sleep_score']):>6}"
            f"{fmt(m['sleep_hours']):>5}{fmt(m['hrv_ms']):>5}{fmt(m['resting_hr']):>5}"
            f"{fmt(m['hr_avg']):>6}{fmt(m['steps']):>7}  {fmt(m['stress_summary']):<10} {workouts}"
        )


def print_schedule(schedule: dict) -> None:
    def hm(dt):
        return dt.strftime("%H:%M")

    print("All-day: " + (", ".join(schedule["all_day"]) or "none"))
    print("Busy:")
    for start, end, summary in schedule["busy"] or []:
        print(f"  {hm(start)}-{hm(end)}  {summary}")
    if not schedule["busy"]:
        print("  none")
    print("Free:")
    for start, end in schedule["free"]:
        print(f"  {hm(start)}-{hm(end)}")


def main() -> None:
    today = datetime.now(TZ).date()
    start, end = today - timedelta(days=7), today + timedelta(days=1)

    print(f"Fetching Oura data {start} .. {today}")
    client = OuraClient()
    raw = client.fetch_range(start, end)
    raw["personal_info"] = client.personal_info()
    for name, payload in raw.items():
        save_fixture(name, payload)
    print(f"Saved {len(raw)} raw responses to {FIXTURES_DIR.name}/\n")

    print_oura_summary(raw, [start + timedelta(days=i) for i in range(8)])

    print(f"\nCalendar for today ({today}, {TZ.key})")
    feed = fetch_calendar_bytes()
    CALENDAR_FIXTURE.write_bytes(feed)
    print_schedule(schedule_for(icalendar.Calendar.from_ical(feed), today))


if __name__ == "__main__":
    main()
