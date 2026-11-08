"""Print recent LLM calls from the runs table: job, model, tokens, cost, latency, then input and output.

Usage: python scripts/show_runs.py [--demo] [--last 3] [--brief]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db, models  # noqa: E402
from core.config import DEMO_DB_PATH  # noqa: E402


def header(row) -> str:
    cost = models.cost(row["model"], row["input_tokens"], row["output_tokens"])
    tokens = (f"tokens={row['input_tokens']}+{row['output_tokens']}" if row["input_tokens"] is not None
              else "tokens=n/a")
    return (f"=== run #{row['id']}  {row['created_at']}  kind={row['kind']}  job={row['job'] or 'n/a'}  "
            f"model={row['model']}  {tokens}  cost=" + (f"${cost:.5f}" if cost is not None else "n/a")
            + f"  latency={row['latency_ms'] if row['latency_ms'] is not None else 'n/a'} ms")


def show_input(trace) -> None:
    if isinstance(trace, dict):                       # structured calls: {"system", "user"}
        print("--- system prompt ---\n" + trace["system"].strip())
        print("--- input ---\n" + trace["user"].strip())
        return
    for m in trace:                                   # agent calls: the messages, in order
        content = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        print(f"--- {m['role']} ---\n{content.strip()}" + (f"\n(tool calls: {m['tool_calls']})" if m.get("tool_calls") else ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", "-n", type=int, default=3, help="how many runs to show")
    parser.add_argument("--demo", action="store_true", help="read demo.db (fixtures runs)")
    parser.add_argument("--brief", action="store_true", help="only the header line of each run")
    args = parser.parse_args()

    if args.demo:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    for row in reversed(db.recent_runs(args.last)):
        print(header(row))
        if args.brief:
            continue
        show_input(json.loads(row["input_json"]))
        output = json.loads(row["output_json"]) if row["output_json"] else "(call failed)"
        print("--- output ---\n" + json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
