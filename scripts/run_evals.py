"""Rubric evals (M4 Part G): run every eval case on the real coach N times, grade each reply by its rubric, and
report per category: routing accuracy, rubric pass rate, trials fully passed, pass^N (every trial passed), average
latency, tokens, cost and the models used.

Cases: tests/data/eval_set.jsonl (scenario messages and the G cases, in the repo) + data/evals/eval_set_personal.jsonl
(the person's own questions: gitignored, the repo is public).

PRIVACY. Per-case questions, replies, tool results and judge reasons go to data/evals/<run>/ (gitignored) and, with
LANGSMITH_TRACING on, to LangSmith (each graded turn is a trace with its checks as feedback). `--publish` writes
docs/evals/<date>.md with category totals and check texts only, never a message (tests/test_eval_privacy.py).

Usage:
  python scripts/run_evals.py --trials 3 --publish           # the full run
  python scripts/run_evals.py --per-category 2 --trials 1     # a pilot (the grading sheet, the cost estimate)
  python scripts/run_evals.py --estimate data/evals/<pilot>   # what a full run would cost, from a pilot
  python scripts/run_evals.py --only G-2/G-4 --trials 10      # one case or category, many times
  python scripts/run_evals.py --effort agent=low --effort classifier=low ...   # G3b: reasoning effort per job
  python scripts/run_evals.py --compare data/evals/<run A> data/evals/<run B>   # side by side on shared cases
"""
import argparse
import json
import multiprocessing
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SET_PATHS = [ROOT / "tests" / "data" / "eval_set.jsonl", ROOT / "data" / "evals" / "eval_set_personal.jsonl"]
OUT_DIR = ROOT / "data" / "evals"
DOCS_DIR = ROOT / "docs" / "evals"


def load_cases(only: str | None = None, per_category: int | None = None) -> list[dict]:
    cases = []
    for path in SET_PATHS:
        if path.exists():
            cases += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        else:
            print(f"(not found, skipped: {path.relative_to(ROOT)})")
    if only:
        cases = [c for c in cases if only in (c["id"], c["categories"][0])]
    if per_category:
        kept, n = [], defaultdict(int)
        for c in cases:                       # public first, so a pilot mixes in the G cases
            if n[c["categories"][0]] < per_category:
                n[c["categories"][0]] += 1
                kept.append(c)
        cases = kept
    return cases


# --- one worker process: its own database per case, its own MCP servers ------------------------------------

def _init_worker(effort: dict) -> None:
    import atexit
    from core import mcp_tools, models
    for job, value in effort.items():
        models.PARAM_OVERRIDES[job] = {"reasoning_effort": value}
    atexit.register(mcp_tools.close)


def _regrade(record: dict, case: dict) -> dict:
    from evals.runner import regrade
    return regrade(record, case)


def _run(case: dict, trial: int, tag: str) -> dict:
    from evals.runner import run_case
    result = run_case(case, trial, tag)
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass
    return result


# --- totals --------------------------------------------------------------------------------------------------

def _rate(hits: int, n: int) -> str:
    return f"{100 * hits / n:.0f}%" if n else "–"


def totals(results: list[dict], trials: int) -> dict:
    """Per main category (and "all"): category totals only, no message."""
    groups = defaultdict(list)
    for r in results:
        groups[r["categories"][0]].append(r)
        groups["all"].append(r)
    out = {}
    for name, rs in sorted(groups.items(), key=lambda kv: (kv[0] == "all", kv[0])):
        checks = [g for r in rs for g in r["checks"] if g["answer"] != "n/a"]
        by_case = defaultdict(list)
        for r in rs:
            by_case[r["id"]].append(r["passed"])
        full = [all(v) for v in by_case.values() if len(v) == trials]
        out[name] = {
            "cases": len(by_case), "trials": len(rs),
            "routing": _rate(sum(r["routing_hit"] for r in rs), len(rs)),
            "checks": _rate(sum(g["answer"] == "yes" for g in checks), len(checks)),
            "trials_passed": _rate(sum(r["passed"] for r in rs), len(rs)),
            f"pass^{trials}": _rate(sum(full), len(full)),
            "latency_s": round(sum(r["latency_s"] for r in rs) / len(rs), 1),
            "tokens_in": round(sum(r["coach"]["input_tokens"] for r in rs) / len(rs)),
            "tokens_out": round(sum(r["coach"]["output_tokens"] for r in rs) / len(rs)),
            "cost": round(sum(r["coach"]["cost"] for r in rs), 3),
            "judge_cost": round(sum(r["judge"]["cost"] for r in rs), 3),
            "models": sorted({m for r in rs for m in r["coach"]["models"]}),
        }
    return out


def check_totals(results: list[dict]) -> list[tuple[str, str, str, int]]:
    """(skill, check, yes rate, applicable) for every check text: skill-file text, never a message."""
    seen = defaultdict(lambda: [0, 0])
    for r in results:
        for g in r["checks"]:
            if g["answer"] != "n/a" and g["skill"] != "case":
                seen[(g["skill"], g["check"])][0] += g["answer"] == "yes"
                seen[(g["skill"], g["check"])][1] += 1
    return [(s, c, _rate(y, n), n) for (s, c), (y, n) in sorted(seen.items())]


def g4_rate(results: list[dict]) -> str:
    rs = [r for r in results if r["id"] == "G-2/G-4"]
    cited = [g for r in rs for g in r["checks"] if g.get("id") == "citations" and g["answer"] != "n/a"]
    bad = sum(g["answer"] == "no" for g in cited)
    if not rs:
        return "not run"
    return (f"{bad} of {len(cited)} replies with a [n] marker kept an unsupported one ({_rate(bad, len(cited))}); "
            f"{len(rs)} trials, {len(rs) - len(cited)} without a marker")


def report(results: list[dict], trials: int, title: str, effort: dict) -> str:
    t = totals(results, trials)
    head = ["category", "cases", "trials", "routing", "checks", "trials passed", f"pass^{trials}", "latency s",
            "tokens in", "tokens out", "cost $", "judge $"]
    lines = [f"# {title}", "", f"Models: {', '.join(t['all']['models'])}. Reasoning effort overrides: "
             f"{', '.join(f'{k}={v}' for k, v in effort.items()) or 'none'}.", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, v in t.items():
        lines.append(f"| {name} | {v['cases']} | {v['trials']} | {v['routing']} | {v['checks']} | "
                     f"{v['trials_passed']} | {v[f'pass^{trials}']} | {v['latency_s']} | {v['tokens_in']} | "
                     f"{v['tokens_out']} | {v['cost']} | {v['judge_cost']} |")
    lines += ["", "Routing: the classifier's categories overlap the case's and add none. Checks: yes / checks that "
              "applied. Trials passed: every applicable check yes. "
              f"pass^{trials}: every trial of the case passed. Latency and tokens: per graded message (coach only).",
              "", f"G-4 (unsupported [n] cite): {g4_rate(results)}.", "", "## Checks", "",
              "| skill | check | yes | applied |", "|---|---|---|---|"]
    lines += [f"| {s} | {c} | {rate} | {n} |" for s, c, rate, n in check_totals(results)]
    return "\n".join(lines) + "\n"


def compare(dirs: list[Path]) -> str:
    """Two or more runs side by side, over the cases every run has (G3b: one setting against another)."""
    runs = {d.name: [json.loads(line) for line in (d / "results.jsonl").read_text().splitlines()] for d in dirs}
    shared = set.intersection(*({r["id"] for r in rs} for rs in runs.values()))
    head = ["run", "efforts", "trials", "checks", "trials passed", "latency s", "agent s", "tokens out", "cost $"]
    lines = [f"Over {len(shared)} shared cases.", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, rs in runs.items():
        rs = [r for r in rs if r["id"] in shared]
        checks = [g for r in rs for g in r["checks"] if g["answer"] != "n/a"]
        efforts = ", ".join(f"{k}={v}" for k, v in rs[0].get("params", {}).items())
        agent = sum(r["coach"].get("seconds_by_job", {}).get("agent", 0) for r in rs) / len(rs)
        lines.append(f"| {name} | {efforts} | {len(rs)} | {_rate(sum(g['answer'] == 'yes' for g in checks), len(checks))} "
                     f"| {_rate(sum(r['passed'] for r in rs), len(rs))} | {sum(r['latency_s'] for r in rs) / len(rs):.1f} "
                     f"| {agent:.1f} | {sum(r['coach']['output_tokens'] for r in rs) / len(rs):.0f} "
                     f"| {sum(r['coach']['cost'] for r in rs):.3f} |")
    return "\n".join(lines) + "\n"


def estimate(pilot_dir: Path, trials: int, workers: int) -> str:
    """A full run's cost and time, from a pilot: the average per trial in each category × the full set."""
    pilot = [json.loads(line) for line in (pilot_dir / "results.jsonl").read_text().splitlines()]
    per = defaultdict(list)
    for r in pilot:
        per[r["categories"][0]].append(r)
    every = [r for rs in per.values() for r in rs]
    cases = load_cases()
    coach = judge = seconds = 0.0
    for c in cases:
        rs = per.get(c["categories"][0]) or every
        coach += trials * sum(r["coach"]["cost"] for r in rs) / len(rs)
        judge += trials * sum(r["judge"]["cost"] for r in rs) / len(rs)
        seconds += trials * sum(r["latency_s"] + 8 for r in rs) / len(rs)      # +8 s: setup turns, judge
    return (f"{len(cases)} cases × {trials} trials = {len(cases) * trials} graded replies: about ${coach:.2f} coach + "
            f"${judge:.2f} judge = ${coach + judge:.2f}, about {seconds / 60 / workers:.0f} min with {workers} workers "
            f"(from {len(pilot)} pilot trials in {pilot_dir.name}).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--only", help="a case id or a category")
    p.add_argument("--per-category", type=int, help="at most this many cases per category (a pilot)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--effort", action="append", default=[], help="job=low|medium|minimal|high, for this run only")
    p.add_argument("--publish", action="store_true", help="also write docs/evals/<date>.md (category totals only)")
    p.add_argument("--estimate", type=Path, help="a pilot's data/evals/<run> folder: print a full run's cost")
    p.add_argument("--tag", help="the run's name (default: date-time)")
    p.add_argument("--regrade", type=Path, help="grade a run's saved replies again by today's rubrics")
    p.add_argument("--compare", type=Path, nargs="+", help="data/evals/<run> folders: side by side on shared cases")
    args = p.parse_args()
    if args.estimate:
        print(estimate(args.estimate, args.trials, args.workers))
        return
    if args.compare:
        print(compare(args.compare))
        return
    effort = dict(e.split("=", 1) for e in args.effort)
    cases = load_cases(args.only, args.per_category)
    tag = args.tag or (f"{args.regrade.name}-regraded" if args.regrade else datetime.now().strftime("%Y-%m-%d-%H%M"))
    out = OUT_DIR / tag
    out.mkdir(parents=True, exist_ok=True)
    if args.regrade:
        by_id = {c["id"]: c for c in load_cases()}
        saved = [json.loads(line) for line in (args.regrade / "results.jsonl").read_text().splitlines()]
        effort = dict(saved[0].get("params", {})) if saved else {}
        args.trials = max((r["trial"] for r in saved), default=1)
    print(f"{len(cases)} cases × {args.trials} trials, {args.workers} workers → {out.relative_to(ROOT)}")
    results = []
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_init_worker, initargs=({} if args.regrade else effort,)) as pool, \
            (out / "results.jsonl").open("w") as f:
        if args.regrade:
            futures = [pool.submit(_regrade, r, by_id[r["id"]]) for r in saved]
        else:
            futures = [pool.submit(_run, c, trial, tag) for c in cases for trial in range(1, args.trials + 1)]
        for done, future in enumerate(as_completed(futures), 1):
            r = future.result()
            results.append(r)
            f.write(json.dumps(r, default=str) + "\n")
            f.flush()
            print(f"[{done}/{len(futures)}] {r['id']} #{r['trial']}: {'pass' if r['passed'] else 'FAIL'} "
                  f"{r['latency_s']}s" + (f" ({r['error']})" if r["error"] else ""))
    title = f"Evals {tag}: {len(cases)} cases × {args.trials}"
    text = report(results, args.trials, title, effort)
    (out / "report.md").write_text(text)
    print("\n" + text)
    if args.publish:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        path = DOCS_DIR / f"{tag[:10]}.md"
        path.write_text(text)
        print(f"published {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
