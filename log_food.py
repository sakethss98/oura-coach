"""Log anything you ate or drank.

Usage: python log_food.py [--demo] "2 eggs, toast, black coffee"
"""
import argparse

import db
from config import DEMO_DB_PATH
from food import format_entry, log_food


def main() -> None:
    parser = argparse.ArgumentParser(description="Log a food/drink/supplement entry with rough macro estimates.")
    parser.add_argument("text", help='what you had, e.g. "chicken rice bowl and a diet coke"')
    parser.add_argument("--demo", action="store_true", help="log into demo.db (used by run.py --fixtures)")
    args = parser.parse_args()

    if args.demo:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    print(format_entry(log_food(args.text)))


if __name__ == "__main__":
    main()
