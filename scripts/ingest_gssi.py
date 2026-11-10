"""Build or update the GSSI index (data/gssi_index/, gitignored) from docs/gssi_sources.txt.

Each article's text comes from a page or PDF saved into data/gssi_raw/ (gssiweb.org blocks automated
fetching), or else a polite fetch (robots.txt first, our User-Agent, 3 s apart). Unchanged articles are
skipped, so a second run embeds nothing. Prints one line per article (status, chunks, year, title) and
every URL that failed, was blocked, or turned out to be a listing page instead of an article.
The text itself is never printed or committed.

Usage: python scripts/ingest_gssi.py [--no-fetch] [--sources docs/gssi_sources.txt] [--raw data/gssi_raw]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from core import db, models  # noqa: E402  (core.config loads .env: OPENAI_API_KEY for the embeddings)
from mcp_servers.gssi import index, ingest  # noqa: E402

PROBLEMS = {"blocked", "failed", "listing", "missing", "unmatched"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=ROOT / "docs" / "gssi_sources.txt")
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "gssi_raw")
    parser.add_argument("--no-fetch", action="store_true", help="only use saved files in --raw")
    args = parser.parse_args()

    collection = index.open_collection()
    sources = ingest.read_sources(args.sources)
    with httpx.Client(headers={"User-Agent": ingest.USER_AGENT}, follow_redirects=True,
                      timeout=ingest.FETCH_TIMEOUT_S) as client:
        fetcher = None if args.no_fetch else ingest.Fetcher(client)
        report = ingest.run(sources, args.raw, collection, index.embedder(), fetcher)

    if fetcher and fetcher.robots:
        print("robots.txt: " + "; ".join(f"{host}: {note}" for host, (_, note) in fetcher.robots.items()) + "\n")
    print(f"{'status':<10}{'chunks':>7}{'year':>6}  title / url")
    for row in report:
        if row["status"] in {"indexed", "unchanged"}:
            print(f"{row['status']:<10}{row['chunks']:>7}{row['year'] or '':>6}  {row['title'][:70]}")
            if "redirected" in row["note"] or "own address" in row["note"]:
                print(f"{'':<23}{row['note']}")
    problems = [r for r in report if r["status"] in PROBLEMS | {"removed"}]
    if problems:
        print("\nNot indexed:")
        for row in problems:
            print(f"- {row['status']}: {row['url'] or ''} {row['note']}".replace("  ", " "))
    indexed = [r for r in report if r["status"] == "indexed"]
    counts = {s: sum(r["status"] == s for r in report) for s in ("indexed", "unchanged")}
    print(f"\n{counts['indexed']} indexed, {counts['unchanged']} unchanged, {len(problems)} not indexed; "
          f"{collection.count()} chunks in {index.INDEX_DIR.relative_to(ROOT)}")
    tokens = sum(r["tokens"] for r in indexed)
    if tokens:      # one runs row for the embedding calls of this run (model + tokens, never the text)
        db.init_db()
        model = index.embedding_model()
        db.log_run("ingest", model, {"articles": len(indexed)}, {"chunks": sum(r["chunks"] for r in indexed)},
                   job="embeddings", input_tokens=tokens, output_tokens=0)
        cost = models.cost(model, tokens, 0)
        print(f"embedded {tokens} tokens" + (f" (${cost:.4f})" if cost is not None else ""))


if __name__ == "__main__":
    main()
