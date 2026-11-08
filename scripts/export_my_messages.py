"""Export my own messages (user role, user 1) from oura_coach.db to data/sample_questions.jsonl.

One JSON object per line: {"text", "ts"}. Commands and voice notes are skipped. The database is opened
read-only; data/ is gitignored (these are personal messages). Prints only the count, never the text.

Usage: python scripts/export_my_messages.py [--db oura_coach.db] [--out data/sample_questions.jsonl]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DB_PATH  # noqa: E402

SKIP = {"/start", "/help", "[voice note]"}


def export(db_path: Path, out: Path, user_id: int = 1) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT text, ts FROM messages WHERE user_id = ? AND role = 'user' ORDER BY ts, id",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    kept = [{"text": text, "ts": ts} for text, ts in rows if text.strip() and text.strip() not in SKIP]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
    return len(kept)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "sample_questions.jsonl")
    args = parser.parse_args()
    count = export(args.db, args.out)
    print(f"Exported {count} messages to {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")


if __name__ == "__main__":
    main()
