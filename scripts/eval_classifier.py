"""Run the real classifier (job `classifier`) over the labeled messages and report how it routes.

The labeled messages: tests/data/classifier_set.jsonl (scenario messages and added examples, in the repo)
plus data/classifier_personal.jsonl when it exists (the person's own questions and live messages:
gitignored, the repo is public). Each is reported by source.

A hit = the predicted categories overlap the labeled ones and add none outside them (a second category
the label doesn't have is a miss: it would give the agent the wrong tools and guidance).
Targets: overall >= 90%, logging >= 98% (a missed log loses data; asking twice is cheap).
About 20% of the set is `held_out`: never tuned against. It is scored separately and its misses are not
printed (only with --show-held-out), so prompt changes are judged on messages they weren't fitted to.
Also: main-category accuracy, per-category accuracy, a confusion matrix of main categories, and every miss
of the tuning part. Calls are logged to demo.db's runs table (never oura_coach.db).

Usage: python scripts/eval_classifier.py [--workers 6] [--only nutrition] [--show-held-out]
"""
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import classifier, db, models, skills  # noqa: E402
from core.config import DEMO_DB_PATH  # noqa: E402

SET_PATHS = [ROOT / "tests" / "data" / "classifier_set.jsonl", ROOT / "data" / "classifier_personal.jsonl"]
TARGETS = {"overall": 0.90, "logging": 0.98}


def load_set(only: str | None = None) -> list[dict]:
    entries = []
    for path in SET_PATHS:
        if path.exists():
            entries += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        else:
            print(f"(not found, skipped: {path.relative_to(ROOT)})")
    return [e for e in entries if not only or only in e["categories"]]


def route(entry: dict, found: dict) -> dict:
    recent = [tuple(turn) for turn in entry.get("prior_turns", [])]
    try:
        result = classifier.classify(entry["text"], recent, entry.get("notes", []), None, found)
    except Exception as e:
        result = {"categories": [], "reason": f"failed ({type(e).__name__})"}
    return {**entry, "predicted": result["categories"] or [skills.OTHER], "reason": result["reason"]}


def is_hit(r: dict) -> bool:
    predicted, labeled = set(r["predicted"]), set(r["categories"])
    return bool(predicted & labeled) and predicted <= labeled


def rate(results: list[dict]) -> float | None:
    return sum(is_hit(r) for r in results) / len(results) if results else None


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def per_category(results: list[dict]) -> dict[str, tuple[float, int]]:
    """Accuracy over the messages labeled with each category (a two-category label counts for both)."""
    names = sorted({c for r in results for c in r["categories"]})
    return {n: (rate(mine), len(mine)) for n in names if (mine := [r for r in results if n in r["categories"]])}


def report(results: list[dict], show_held_out: bool = False) -> bool:
    tune = [r for r in results if not r.get("held_out")]
    held = [r for r in results if r.get("held_out")]
    print(f"model: {models.model_name('classifier')}   messages: {len(results)} "
          f"({len(tune)} tuning, {len(held)} held out)\n")
    checks = {"overall": (rate(tune), rate(held)),
              "logging": (rate([r for r in tune if "logging" in r["categories"]]),
                          rate([r for r in held if "logging" in r["categories"]]))}
    print(f"{'target':<10}{'tuning':>9}{'held out':>10}   needed")
    passed = True
    for name, (on_tune, on_held) in checks.items():
        ok = on_tune is not None and on_tune >= TARGETS[name]
        passed &= ok
        print(f"{name:<10}{pct(on_tune):>9}{pct(on_held):>10}   >= {TARGETS[name]:.0%} {'OK' if ok else 'BELOW'}")
    main = [r for r in tune if r["predicted"][0] == r["categories"][0]]
    print(f"main category right (tuning): {pct(len(main) / len(tune) if tune else None)}\n")

    print(f"{'per category':<20}{'tuning':>9}{'held out':>10}")
    tune_cats, held_cats = per_category(tune), per_category(held)
    for name in sorted(set(tune_cats) | set(held_cats)):
        t, h = tune_cats.get(name, (None, 0)), held_cats.get(name, (None, 0))
        print(f"{name:<20}{pct(t[0]):>6} ({t[1]:>2}){pct(h[0]):>6} ({h[1]})")

    sources = sorted({r["source"] for r in results})
    print("\nby source: " + ", ".join(
        f"{src} {pct(rate([r for r in results if r['source'] == src]))} ({sum(r['source'] == src for r in results)})"
        for src in sources))

    names = sorted({r["categories"][0] for r in tune} | {r["predicted"][0] for r in tune})
    counts = Counter((r["categories"][0], r["predicted"][0]) for r in tune)
    width = max(len(n) for n in names)
    print("\nconfusion, tuning part (rows = labeled main category, columns = predicted main category)")
    print(" " * (width + 1) + " ".join(f"{n[:6]:>6}" for n in names))
    for labeled in names:
        print(f"{labeled:<{width}} " + " ".join(f"{counts[(labeled, p)] or '.':>6}" for p in names))

    shown = tune + (held if show_held_out else [])
    misses = [r for r in shown if not is_hit(r)]
    if misses:
        print(f"\nmisses ({len(misses)}{', held out included' if show_held_out else ', tuning part only'}):")
        for r in misses:
            tag = " [held out]" if r.get("held_out") else ""
            print(f"- {r['text']!r}{tag}: labeled {r['categories']}, got {r['predicted']} ({r['reason']})")
    held_misses = sum(not is_hit(r) for r in held)
    if held_misses and not show_held_out:
        print(f"\n{held_misses} held-out miss(es) not shown (never tune against them).")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6, help="parallel classifier calls")
    parser.add_argument("--only", help="only messages labeled with this category")
    parser.add_argument("--show-held-out", action="store_true", help="also list held-out misses (not for tuning)")
    args = parser.parse_args()
    db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    found = skills.load()
    entries = load_set(args.only)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda e: route(e, found), entries))
    sys.exit(0 if report(results, args.show_held_out) else 1)


if __name__ == "__main__":
    main()
