"""Part G rubric evals: the skills' rubrics, the code checks, the eval set, and one case run end to end on the
scripted model (no API call: the judge is faked here; scripts/run_evals.py uses the real one)."""
import json
from pathlib import Path

import pytest

from core import skills
from evals import checks, judge, runner, setups
from tests.conftest import call, needs_fixtures, say

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_SET = ROOT / "tests" / "data" / "eval_set.jsonl"
PERSONAL_SET = ROOT / "data" / "evals" / "eval_set_personal.jsonl"
ALL_SKILLS = skills.load()


def turn(reply, results=(), message="q", earlier=(), case=None, decline=None):
    return checks.Turn(case=case or {}, message=message, earlier=list(earlier), reply=reply,
                       tool_results=list(results), decline=decline)


def result(name, content="{}", artifact=None):
    return {"name": name, "content": content, "artifact": artifact}


# --- rubrics --------------------------------------------------------------------------------------------

def test_every_skill_has_three_to_six_checks_and_code_checks_name_a_function():
    for s in ALL_SKILLS.values():
        assert 3 <= len(s.rubric) <= 6, s.name
        for c in s.rubric:
            assert c["by"] in ("code", "judge") and c["check"], s.name
            if c["by"] == "code":
                assert c.get("id") in checks.CHECKS, (s.name, c)
            else:
                assert c.get("id") in (None, "citations"), (s.name, c)


def test_a_case_gets_each_of_its_skills_checks_once_plus_its_own():
    case = {"categories": ["training", "recovery_sleep"], "checks": [{"check": "x", "by": "judge"}],
            "expect_tools": {"any": ["get_today"]}}
    got = runner.rubric(case, ALL_SKILLS)
    numbers = [c for c in got if c.get("id") == "numbers_from_tools"]
    assert len(numbers) == 1                                     # both skills have it: once
    assert [c["skill"] for c in got[-2:]] == ["case", "case"] and got[-1]["id"] == "expected_tools"


# --- code checks ------------------------------------------------------------------------------------------

def test_own_words_leave_out_code_built_text_and_credit_lines():
    built = "Before: 1-4 g/kg carbs 1-4 h before."
    t = turn(f"{built}\nReadiness is solid [1].\nSource [1]: GSSI – Carbs https://x.org/a",
             [result("fueling_plan", artifact={"verbatim": built})])
    assert t.own_words() == "Readiness is solid [1]."


def test_numbers_must_come_from_a_tool_or_their_message():
    t = turn("About 2,218 kcal and 126 g protein left; run at 1pm.",
             [result("get_today", json.dumps({"left": {"calories": 2218.4, "protein_g": 126}, "at": "13:00"}))])
    assert checks.numbers_from_tools(t)[0] == "yes"
    assert checks.numbers_from_tools(turn("Have 500 ml.", [result("get_today", "{}")]))[0] == "no"
    assert checks.numbers_from_tools(turn("Have 500 ml.", message="I drank 500 ml"))[0] == "yes"
    assert checks.numbers_from_tools(turn("Sounds good."))[0] == "n/a"


def test_tool_checks():
    t = turn("ok", [result("fueling_plan"), result("search_gssi")])
    assert checks.tools_called(t, all=["fueling_plan", "search_gssi"])[0] == "yes"
    assert checks.tools_called(t, any=["get_today"])[0] == "no"
    assert checks.tools_called(turn("x"), none=True)[0] == "yes"
    assert checks.expected_tools(turn("x"))[0] == "n/a"
    assert checks.expected_tools(turn("x", [result("edit_food")], case={"expect_tools": {"all": ["edit_food"]}}))[0] \
        == "yes"


def test_source_lines_for_markers_and_usda():
    cited = "Carbs help [1].\nSource [1]: GSSI – Carbs https://www.gssiweb.org/en/a"
    assert checks.source_lines(turn(cited))[0] == "yes"
    assert checks.source_lines(turn("Carbs help [1]."))[0] == "no"
    usda = [result("food_facts", json.dumps({"from_usda": True}), {"verbatim": "x", "source_line": "Numbers: USDA FoodData Central (Olive oil)"})]
    assert checks.source_lines(turn("x", usda))[0] == "no"
    assert checks.source_lines(turn("x\nNumbers: USDA FoodData Central (Olive oil)", usda))[0] == "yes"
    assert checks.source_lines(turn("Fine."))[0] == "n/a"


def test_small_checks():
    assert checks.no_ids(turn("Saved (id 12)."))[0] == "no" and checks.no_ids(turn("Saved."))[0] == "yes"
    assert checks.fixed_decline(turn("I only coach.", decline="I only coach."))[0] == "yes"
    asked = [("coach", "Plan: 12 weeks… Set it up?")]
    assert checks.confirm_after_yes(turn("Done.", [result("confirm_goal")], "yes", asked))[0] == "yes"
    assert checks.confirm_after_yes(turn("Done.", [result("confirm_goal")], "half marathon in May"))[0] == "no"
    assert checks.confirm_after_yes(turn("Done."))[0] == "n/a"
    assert checks.plan_by_code(turn("Do a 30 min easy run now."))[0] == "no"
    assert checks.plan_by_code(turn("Do a 30 min easy run now.", [result("plan_workout")]))[0] == "yes"
    assert checks.plan_by_code(turn("Rest well tonight."))[0] == "n/a"


def test_a_skipped_judge_answer_shows_as_no(monkeypatch):
    class Fake:
        answers = [judge.Answer(n=2, answer="yes", reason="fine")]
    monkeypatch.setattr(judge, "structured_call", lambda *a, **k: Fake())
    assert judge.grade(turn("x"), [{"check": "a"}, {"check": "b"}]) == [("no", "the judge gave no answer"),
                                                                        ("yes", "fine")]


# --- the eval set -------------------------------------------------------------------------------------------

def cases(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_eval_set_cases_are_well_formed():
    public = cases(PUBLIC_SET)
    every = public + (cases(PERSONAL_SET) if PERSONAL_SET.exists() else [])
    assert len({c["id"] for c in every}) == len(every)
    known_setups = {"run_history", "half_goal", "direction", "food_today", "food_week", "user2"}
    for c in every:
        assert c["categories"] and set(c["categories"]) <= set(ALL_SKILLS), c["id"]
        assert c["turns"] and all(t["text"] for t in c["turns"]), c["id"]
        assert all(s in known_setups or s.startswith("readiness_") for s in c.get("setup", [])), c["id"]
    assert {c["id"] for c in public} >= {"G-1", "G-2/G-4", "G-3", "G-5", "G-6", "G-7", "G-8"}
    if PERSONAL_SET.exists():                 # with the person's own questions: 5-10 per category
        per = {}
        for c in every:
            per[c["categories"][0]] = per.get(c["categories"][0], 0) + 1
        assert all(5 <= n <= 10 for n in per.values()) and set(per) == set(ALL_SKILLS), per


def test_the_public_set_holds_none_of_their_own_messages():
    own = ROOT / "data" / "my_questions.txt"
    if not own.exists():
        pytest.skip("data/my_questions.txt is not here")
    text = PUBLIC_SET.read_text().lower()
    already = (ROOT / "tests" / "data" / "classifier_set.jsonl").read_text().lower()   # public scenario texts
    assert not [q for q in own.read_text().splitlines()
                if len(q.split()) >= 3 and q.lower().strip() in text and q.lower().strip() not in already]


# --- one case end to end, scripted -------------------------------------------------------------------------

@needs_fixtures
def test_a_case_runs_on_a_fresh_database_and_is_graded(temp_db, chat_model, fake_llm, monkeypatch):
    from core import db
    monkeypatch.setattr(judge, "grade", lambda t, cs, user_id=None: [("yes", "judged")] * len(cs))
    chat_model.script.extend([call("log_food", text="poha", time_text=None), say("Logged poha.")])
    case = {"id": "logging-1", "categories": ["logging"], "turns": [{"text": "had poha", "at": "09:00"}],
            "expect_tools": {"all": ["log_food"]}}
    before = db.db_path()
    r = runner.run_case(case)
    assert db.db_path() == before                                   # the test's database is back
    assert r["tools"] == ["log_food"] and r["reply"].startswith("Logged poha")
    assert {g["id"]: g["answer"] for g in r["checks"]}["expected_tools"] == "yes"
    assert r["error"] is None and r["latency_s"] >= 0


def test_setups_are_known():
    with pytest.raises(ValueError):
        setups.apply(["no_such_setup"], {}, None)


def test_numbers_no_tool_gave_pass_only_when_the_judge_calls_them_general_advice(monkeypatch):
    asked = []

    def fake(t, cs, user_id=None):
        asked.extend(c["check"] for c in cs)
        return [("yes", "a typical portion")]
    monkeypatch.setattr(judge, "grade", fake)
    check = {"check": "numbers", "by": "code", "id": "numbers_from_tools", "skill": "trends_patterns"}
    t = turn("Add a 20–30 g protein food on low days.", [result("food_totals", '{"total": 297}')], message="protein?")
    [graded] = runner.grade(t, [check])
    assert graded["answer"] == "yes" and "20" in asked[0] and "general advice" in asked[0]
    assert "judge: a typical portion" in graded["reason"]
