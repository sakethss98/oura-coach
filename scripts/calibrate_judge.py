"""Judge calibration (M4 Part G1b): a grading sheet of graded replies for the person to mark, then agreement
between their marks and the evals' answers, per check. A check under 85% agreement is reworded before its results
are trusted.

  python scripts/calibrate_judge.py --from data/evals/<run>      # writes data/evals/grading.md (+ grading_key.json)
  python scripts/calibrate_judge.py --score                     # after marking: agreement per check

The sheet and the key are in data/evals/ (gitignored: they hold the person's messages). The key keeps the evals'
answers out of the sheet, so the marks aren't anchored on them. Mark each check by writing y, n or - (doesn't
apply) after "you:".
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "data" / "evals" / "grading.md"
KEY = ROOT / "data" / "evals" / "grading_key.json"
TRUSTED = 0.85
TOOL_CHARS = 600
MARK = re.compile(r"^- \[(\d+)\.(\d+)\] .*? you:\s*(\S*)\s*$")


def pick(results: list[dict], size: int) -> list[dict]:
    """About `size` replies, spread over the categories (round robin), each case once."""
    by_cat = defaultdict(list)
    seen = set()
    for r in results:
        if r["id"] not in seen and not r["error"]:
            seen.add(r["id"])
            by_cat[r["categories"][0]].append(r)
    out = []
    while len(out) < size and any(by_cat.values()):
        for cat in sorted(by_cat):
            if by_cat[cat] and len(out) < size:
                out.append(by_cat[cat].pop(0))
    return out


def write_sheet(results: list[dict]) -> None:
    lines = ["# Judge calibration sheet", "",
             "For each reply, mark every check: write `y` (yes), `n` (no) or `-` (doesn't apply to this message) "
             "after `you:`. Judge the reply as they'd read it; \"Built by code\" text is part of the reply. "
             "Tool results are cut short. About 30 minutes.", ""]
    key = []
    for i, r in enumerate(results, 1):
        lines += [f"## {i}. {r['categories'][0]} ({r['id']})", ""]
        for who, text in r["earlier"]:
            lines.append(f"> {who}: {text}")
        lines += [f"**They wrote:** {r['message']}", "", "**Reply:**", "", "```", r["reply"], "```", "",
                  "<details><summary>Tool results</summary>", ""]
        for t in r["tool_results"]:
            content = t["content"] if isinstance(t["content"], str) else json.dumps(t["content"])
            lines.append(f"- `{t['name']}`: {content[:TOOL_CHARS]}")
        lines += ["", "</details>", ""]
        for j, g in enumerate(r["checks"], 1):
            lines.append(f"- [{i}.{j}] ({g['by']}) {g['check']} — you: ")
            key.append({"item": i, "n": j, "case": r["id"], "skill": g["skill"], "by": g["by"], "check": g["check"],
                        "answer": g["answer"], "reason": g["reason"]})
        lines.append("")
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    SHEET.write_text("\n".join(lines))
    KEY.write_text(json.dumps(key, indent=1))
    print(f"wrote {SHEET.relative_to(ROOT)}: {len(results)} replies, {len(key)} checks to mark")


def score() -> None:
    marks = {}
    for line in SHEET.read_text().splitlines():
        m = MARK.match(line.strip())
        if m and m.group(3):
            mark = {"y": "yes", "n": "no", "-": "n/a"}.get(m.group(3).lower()[:1])
            if mark:
                marks[(int(m.group(1)), int(m.group(2)))] = mark
    key = json.loads(KEY.read_text())
    per = defaultdict(lambda: [0, 0, []])
    for k in key:
        mark = marks.get((k["item"], k["n"]))
        if mark is None:
            continue
        row = per[(k["by"], k["skill"], k["check"])]
        row[0] += mark == k["answer"]
        row[1] += 1
        if mark != k["answer"]:
            row[2].append(f"#{k['item']} you {mark}, evals {k['answer']} ({k['reason'][:80]})")
    print(f"{len(marks)} of {len(key)} checks marked\n")
    for (by, skill, check), (agree, n, misses) in sorted(per.items(), key=lambda kv: kv[1][0] / kv[1][1]):
        flag = "" if agree / n >= TRUSTED else "  ← under 85%: reword"
        print(f"{agree}/{n} {100 * agree / n:.0f}%  [{by}] {skill}: {check}{flag}")
        for miss in misses:
            print(f"      {miss}")
    total = sum(v[0] for v in per.values()), sum(v[1] for v in per.values())
    if total[1]:
        print(f"\noverall {total[0]}/{total[1]} = {100 * total[0] / total[1]:.0f}%")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="run", type=Path, help="an eval run's folder (data/evals/<run>)")
    p.add_argument("--size", type=int, default=20)
    p.add_argument("--score", action="store_true")
    args = p.parse_args()
    if args.score:
        score()
    elif args.run:
        results = [json.loads(line) for line in (args.run / "results.jsonl").read_text().splitlines()]
        write_sheet(pick(results, args.size))
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
