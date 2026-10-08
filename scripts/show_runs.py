"""Print recent LLM calls (input and output) from the runs table.

Usage: python scripts/show_runs.py [--demo] [-n 3]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from config import DEMO_DB_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=3, help="how many runs to show")
    parser.add_argument("--demo", action="store_true", help="read demo.db (fixtures runs)")
    args = parser.parse_args()

    if args.demo:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    for row in reversed(db.recent_runs(args.n)):
        trace = json.loads(row["input_json"])
        print(f"=== run #{row['id']}  {row['created_at']}  kind={row['kind']}  model={row['model']}")
        print("--- system prompt ---\n" + trace["system"].strip())
        print("--- input ---\n" + trace["user"].strip())
        output = json.loads(row["output_json"]) if row["output_json"] else "(call failed)"
        print("--- output ---\n" + json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
