"""Judge calibration (M4 Part G1b): a grading sheet of graded replies for the person to mark, then agreement
between their marks and the evals' answers, per check. A check under 85% agreement is reworded before its results
are trusted.

  python scripts/calibrate_judge.py --from data/evals/<run>      # writes data/evals/grading.md (+ grading_key.json)
  python scripts/calibrate_judge.py --score                     # after marking: agreement per check
  python scripts/calibrate_judge.py --rejudge data/evals/<run>  # the judge again on the sheet's replies, reworded checks

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
# Checks reworded after a calibration (old text -> new text, logged in docs/evals/rubric_changes.md); --rejudge asks
# the judge the new text on the same replies. Retired checks became code checks and leave the judge's table.
REWORDED = {
    "If they ask about muscle, body composition or how they look, it says it can't measure that":
        "If they ask about muscle, strength gains, body composition, how they look or hybrid progress, it "
        "says lifts and body composition aren't measured",
    "It gives one practical next step":
        "It gives one concrete next step itself (what to do, and when), not a menu of options or an offer to give one",
    "It says which days were not logged when that changes the answer":
        "When a day in the range has no food logged, it names that day (n/a when every day was logged)",
    "If it suggests workouts, it offers one suggestion or at most two options":
        "If it suggests what to do, it gives one clear recommendation; an alternative only if the data is borderline",
}
RETIRED = {"It says its exercise guidance is general, not medical advice": "code check medical_line"}
CITATIONS = "Every [n]-cited claim is supported by passage n"
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


def read_marks() -> dict[tuple[int, int], str]:
    marks = {}
    for line in SHEET.read_text().splitlines():
        m = MARK.match(line.strip())
        if m and m.group(3):
            mark = {"y": "yes", "n": "no", "-": "n/a"}.get(m.group(3).lower()[:1])
            if mark:
                marks[(int(m.group(1)), int(m.group(2)))] = mark
    return marks


def score() -> None:
    marks = read_marks()
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


def rejudge(run: Path) -> None:
    """The judge again on the sheet's replies (the saved run they came from), with the reworded checks; agreement
    with the marks before (the key's answers) and after, per check. The marks are never changed."""
    import os
    import tempfile
    os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"   # calibration needs no traces
    sys.path.insert(0, str(ROOT))
    from core import db, skills
    from evals import checks, judge
    marks, key = read_marks(), json.loads(KEY.read_text())
    records = {}
    for line in (run / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        records.setdefault(r["id"], r)
    decline = skills.load()["off_topic"].decline
    rows = defaultdict(lambda: [0, 0, 0, 0])            # check (new text) -> agree before, agree after, n, changed
    misses = []
    with tempfile.TemporaryDirectory() as tmp:          # the judge's runs rows go here, not into a real database
        db.set_db_path(Path(tmp) / "rejudge.db")
        db.init_db()
        by_item = defaultdict(list)
        for k in key:
            if k["by"] == "judge" and (k["item"], k["n"]) in marks and k["check"] not in RETIRED:
                by_item[k["item"]].append(k)
        for item, ks in sorted(by_item.items()):
            r = records[ks[0]["case"]]
            t = checks.Turn(case={"id": r["id"], "categories": r["categories"]}, message=r["message"],
                            earlier=[tuple(e) for e in r["earlier"]], reply=r["reply"],
                            tool_results=r["tool_results"], categories=r["routed"], decline=decline)
            asked = [k for k in ks if k["check"] != CITATIONS]
            answers = dict(zip([id(k) for k in asked], judge.grade(t, [{"check": REWORDED.get(k["check"], k["check"])}
                                                                         for k in asked])))
            for k in ks:
                answer, reason = judge.citations(t) if k["check"] == CITATIONS else answers[id(k)]
                text = REWORDED.get(k["check"], k["check"])
                mark = marks[(k["item"], k["n"])]
                row = rows[(k["skill"], text)]
                row[0] += mark == k["answer"]
                row[1] += mark == answer
                row[2] += 1
                row[3] = row[3] or text != k["check"]
                if mark != answer:
                    misses.append(f"#{item} [{k['skill']}] you {mark}, judge {answer} ({reason[:80]})")
    print(f"{'before':>7} {'after':>7}  check")
    for (skill, text), (before, after, n, changed) in sorted(rows.items(), key=lambda kv: kv[1][1] / kv[1][2]):
        flag = "" if after / n >= TRUSTED else "  ← under 85%"
        print(f"{before}/{n:<5} {after}/{n:<5}  {'(reworded) ' if changed else ''}[{skill}] {text}{flag}")
    totals = [sum(v[i] for v in rows.values()) for i in range(3)]
    print(f"\noverall before {totals[0]}/{totals[2]} = {100 * totals[0] / totals[2]:.0f}%, "
          f"after {totals[1]}/{totals[2]} = {100 * totals[1] / totals[2]:.0f}%")
    for check, now in RETIRED.items():
        print(f"retired from the judge: {check!r} (now the {now})")
    if misses:
        print("\ndisagreements after:")
        print("\n".join(f"  {m}" for m in misses))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="run", type=Path, help="an eval run's folder (data/evals/<run>)")
    p.add_argument("--size", type=int, default=20)
    p.add_argument("--score", action="store_true")
    p.add_argument("--rejudge", type=Path, help="the eval run the sheet came from (data/evals/<run>)")
    args = p.parse_args()
    if args.score:
        score()
    elif args.rejudge:
        rejudge(args.rejudge)
    elif args.run:
        results = [json.loads(line) for line in (args.run / "results.jsonl").read_text().splitlines()]
        write_sheet(pick(results, args.size))
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
