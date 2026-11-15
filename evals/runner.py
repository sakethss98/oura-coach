"""Run one eval case on the real coach: real models (config/models.yaml), the real MCP servers, Oura fixtures and
an empty calendar, in a fresh database. Earlier turns of the case set the conversation up; the last one is graded
by its rubric (the skills of the case's categories + the case's own checks).

With tracing on, the graded turn is one LangSmith trace ("eval <id>", tagged with the run) and every applicable
check is saved on it as feedback (1 = yes, 0 = no, with the reason).
"""
import copy
import json
import tempfile
from collections import defaultdict
import time as clock
from contextlib import ExitStack, nullcontext
from datetime import time
from pathlib import Path
from unittest.mock import patch

import icalendar
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

import today
from connectors import oura
from core import config, db, models, skills
from core.coach import Coach
from evals import checks, judge, setups

DEFAULT_AT = "09:00"
ADVICE = ("These numbers in the coach's own words are in no tool result: {}. Each is general advice or an example "
          "(a typical amount, a duration to try), not a claim about their own data or a number computed for them")


def rubric(case: dict, all_skills: dict) -> list[dict]:
    """The checks for a case: each of its categories' rubric (a check once), its own checks, its expected tools."""
    out, seen = [], set()
    for name in case["categories"]:
        for check in all_skills[name].rubric:      # the same check from two skills (numbers, reply rules) once
            key = (check.get("id") or check["check"], json.dumps({k: check.get(k) for k in checks.ARGS}))
            if key not in seen:
                seen.add(key)
                out.append({**check, "skill": name})
    out += [{**c, "skill": "case"} for c in case.get("checks", [])]
    if case.get("expect_tools"):
        out.append({"check": "The tools the case expects were called", "by": "code", "id": "expected_tools",
                    "skill": "case"})
    return out


def is_hit(predicted: list[str], labeled: list[str]) -> bool:
    """scripts/eval_classifier.py's rule: overlap with the label and nothing outside it."""
    return bool(set(predicted) & set(labeled)) and set(predicted) <= set(labeled)


def _hm(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def _runs_after(run_id: int) -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM runs WHERE id > ? ORDER BY id", (run_id,))]


def _last_run_id() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0]


def _usage(rows: list[dict]) -> dict:
    tokens_in = sum(r["input_tokens"] or 0 for r in rows)
    tokens_out = sum(r["output_tokens"] or 0 for r in rows)
    cost = sum(models.cost(r["model"], r["input_tokens"], r["output_tokens"]) or 0 for r in rows)
    seconds = defaultdict(float)
    for r in rows:
        seconds[r["job"] or r["kind"]] += (r["latency_ms"] or 0) / 1000
    return {"input_tokens": tokens_in, "output_tokens": tokens_out, "cost": round(cost, 5),
            "models": sorted({f"{r['job']}={r['model']}" for r in rows if r["job"]}),
            "seconds_by_job": {job: round(v, 1) for job, v in seconds.items()}}


def _this_turn(state: dict) -> list:
    messages = state["messages"]
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return messages[last_human + 1:]


def _trace(case: dict, trial: int, tag: str, message: str):
    if not config.tracing_on():
        return nullcontext(None)
    import langsmith
    return langsmith.trace(f"eval {case['id']}", "chain", inputs={"message": message}, tags=["eval", tag],
                           metadata={"eval_case": case["id"], "trial": trial, "categories": case["categories"],
                                     "eval_run": tag})


def _feedback(run, graded: list[dict]) -> None:
    """Every applicable check as feedback on the graded turn's trace. A LangSmith failure never fails the case."""
    if run is None:
        return
    from langsmith.run_trees import get_cached_client
    client = get_cached_client()
    try:
        project = getattr(run, "session_id", None) or client.read_project(project_name=run.session_name).id
        for g in graded:
            if g["answer"] != "n/a":
                client.create_feedback(run.id, key=f"{g['skill']}: {g['check']}"[:180], trace_id=run.trace_id,
                                       score=1 if g["answer"] == "yes" else 0, comment=g["reason"],
                                       session_id=project, start_time=run.start_time)
    except Exception as e:
        print(f"(LangSmith feedback not saved: {type(e).__name__})")


def run_case(case: dict, trial: int = 1, tag: str = "eval") -> dict:
    """Play the case and grade its last turn. Returns everything about it (data/evals/, never docs/)."""
    all_skills = skills.load()
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        stack.callback(db.set_db_path, db.db_path())
        db.set_db_path(Path(tmp) / "eval.db")
        db.init_db()
        raw = copy.deepcopy(oura.load_fixtures())
        stack.enter_context(patch.object(oura, "load_fixtures", lambda: raw))
        stack.enter_context(patch.object(today, "fixture_calendar", lambda: (icalendar.Calendar(), "empty (eval)")))
        *before, last = case["turns"]
        user = setups.apply(case.get("setup", []), raw, _hm(last.get("at", DEFAULT_AT)))
        coach = Coach(InMemorySaver(), use_fixtures=True)
        earlier = []
        for turn in before:
            reply = coach.handle_text(user, turn["text"], setups.at(*map(int, turn.get("at", DEFAULT_AT).split(":"))))
            earlier += [("you", turn["text"]), ("coach", reply)]
        start_id = _last_run_id()
        with _trace(case, trial, tag, last["text"]) as run:
            started = clock.perf_counter()
            error = None
            try:
                reply = coach.handle_text(user, last["text"], setups.at(*map(int, last.get("at", DEFAULT_AT).split(":"))))
            except Exception as e:              # a crash is a failed trial, reported, never a stopped run
                reply, error = "", f"{type(e).__name__}: {e}"[:300]
            latency = clock.perf_counter() - started
            if run is not None:
                run.end(outputs={"reply": reply})
        coach_rows = _runs_after(start_id)
        routed = next((json.loads(r["output_json"] or "{}").get("categories") for r in reversed(coach_rows)
                       if r["kind"] == "classifier"), None) or [skills.OTHER]
        state = coach.last_state if error is None else {"messages": [HumanMessage(last["text"])]}
        results = [{"name": m.name, "content": m.content, "artifact": m.artifact} for m in _this_turn(state)
                   if isinstance(m, ToolMessage)]
        t = checks.Turn(case=case, message=last["text"], earlier=earlier, reply=reply, tool_results=results,
                        categories=routed, decline=all_skills["off_topic"].decline)
        graded = grade(t, rubric(case, all_skills), user.id)
        judge_rows = _runs_after(max([start_id] + [r["id"] for r in coach_rows]))
        _feedback(run, graded)
    applicable = [g for g in graded if g["answer"] != "n/a"]
    return {"id": case["id"], "trial": trial, "categories": case["categories"], "source": case.get("source"),
            "message": last["text"], "earlier": earlier, "reply": reply, "error": error,
            "routed": routed, "routing_hit": is_hit(routed, case["categories"]),
            "tools": [r["name"] for r in results], "tool_results": json.loads(json.dumps(results, default=str)),
            "checks": graded, "passed": error is None and all(g["answer"] == "yes" for g in applicable),
            "latency_s": round(latency, 2), "coach": _usage(coach_rows), "judge": _usage(judge_rows),
            "params": {job: models.params(job).get("reasoning_effort", "default") for job in ("agent", "classifier")}}


def regrade(record: dict, case: dict) -> dict:
    """A saved trial graded again by today's rubric (no coach call: the same reply and tool results)."""
    all_skills = skills.load()
    t = checks.Turn(case=case, message=record["message"], earlier=[tuple(e) for e in record["earlier"]],
                    reply=record["reply"], tool_results=record["tool_results"], categories=record["routed"],
                    decline=all_skills["off_topic"].decline)
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:      # the judge's runs rows go here
        stack.callback(db.set_db_path, db.db_path())
        db.set_db_path(Path(tmp) / "regrade.db")
        db.init_db()
        graded = grade(t, rubric(case, all_skills))
        judge_rows = _runs_after(0)
    applicable = [g for g in graded if g["answer"] != "n/a"]
    return {**record, "checks": graded, "judge": _usage(judge_rows),
            "passed": record["error"] is None and all(g["answer"] == "yes" for g in applicable)}


def grade(t: checks.Turn, rubric_: list[dict], user_id: int | None = None) -> list[dict]:
    """Code checks in code, the citation check on its own, every other judge check in one judge call."""
    out = [None] * len(rubric_)
    questions = []          # (index, the judge's check) for one judge call
    for i, check in enumerate(rubric_):
        if check["by"] == "code":
            out[i] = checks.run(check, t)
            if check["id"] == "numbers_from_tools" and out[i][0] == "no":
                found = ", ".join(f"{x:g}" for x in checks.unknown_numbers(t))
                questions.append((i, {"check": ADVICE.format(found)}))
        elif check.get("id") == "citations":
            out[i] = judge.citations(t, user_id)
        else:
            questions.append((i, check))
    answers = judge.grade(t, [q for _, q in questions], user_id) if t.reply and questions else []
    for (i, _), (answer, reason) in zip(questions, answers):
        if rubric_[i]["by"] == "code":         # the numbers no tool gave: fine only as general advice
            out[i] = (answer, f"{out[i][1]}; judge: {reason}") if answer != "n/a" else out[i]
        else:
            out[i] = (answer, reason)
    for i, _ in questions:
        out[i] = out[i] or ("no", "no reply")
    return [{"check": c["check"], "by": c["by"], "id": c.get("id"), "skill": c["skill"], "answer": a, "reason": r}
            for c, (a, r) in zip(rubric_, out)]
