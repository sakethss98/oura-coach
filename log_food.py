"""Log anything you ate or drank.

Usage: python log_food.py [--demo] [--at HH:MM] "2 eggs, toast, black coffee"
"""
import argparse
from datetime import datetime, time

import db
from config import DEMO_DB_PATH, TZ
from food import format_entry, log_food
from today import fixtures_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Log a food/drink/supplement entry with rough macro estimates.")
    parser.add_argument("text", help='what you had, e.g. "chicken rice bowl and a diet coke"')
    parser.add_argument("--demo", action="store_true",
                        help="log into demo.db on the fixtures' today (used by run.py --fixtures)")
    parser.add_argument("--at", type=time.fromisoformat, metavar="HH:MM", help="when you ate it (default: now)")
    args = parser.parse_args()

    if args.demo:
        db.set_db_path(DEMO_DB_PATH)
        eaten_at = fixtures_now(args.at)
    else:
        now = datetime.now(TZ)
        eaten_at = datetime.combine(now.date(), args.at, TZ) if args.at else now
    db.init_db()
    print(format_entry(log_food(args.text, eaten_at), time_stated=args.at is not None))


if __name__ == "__main__":
    main()
