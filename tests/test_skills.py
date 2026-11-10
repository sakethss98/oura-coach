"""Skill files (skills/*.md) and the route through them: classify -> the skill's tools and guidance only."""
import shutil

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from core import agent, classifier, db, skills
from core.coach import Coach
from core.tools import turn
from tests.conftest import call, needs_fixtures, say
from today import fixtures_now

NEW_SKILL = """---
name: weather
description: >-
  Questions about the weather for a run.
examples:
  - "is it too hot to run?"
tools: [get_today]
rubric:
  - {check: "Says it has no weather data", by: judge}
---
Say you have no weather data yet, then give the hot-day and mild-day rule.
"""


def test_every_skill_file_is_valid_and_names_real_tools():
    found = skills.load()
    assert {"training", "recovery_sleep", "nutrition", "fueling_hydration", "trends_patterns", "goal_program",
            "pain_injury", "memory", "logging", "off_topic"} == set(found)
    skills.validate(found, {t.name for t in agent.all_tools()})
    for s in found.values():
        assert s.description and len(s.examples) >= 2 and (s.tools or s.decline) and s.rubric, s.name
        assert all(r["by"] in ("code", "judge") for r in s.rubric), s.name


def test_a_typo_in_a_tool_name_fails_loudly(tmp_path):
    (tmp_path / "weather.md").write_text(NEW_SKILL.replace("[get_today]", "[get_todya]"))
    with pytest.raises(ValueError, match="unknown tools"):
        skills.validate(skills.load(tmp_path), {t.name for t in agent.all_tools()})


def test_a_new_skill_file_is_a_new_category_with_no_code_change(tmp_path, monkeypatch):
    for path in skills.SKILLS_DIR.glob("*.md"):
        shutil.copy(path, tmp_path)
    (tmp_path / "weather.md").write_text(NEW_SKILL)
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
    seen = {}

    def model(kind, schema, system, user, user_id=None, job="structured"):
        seen.update(system=system, schema=schema)
        return schema(categories=["weather"], reason="asks about heat")
    monkeypatch.setattr(classifier, "structured_call", model)
    route = classifier.classify("is it too hot to run?", [], [], 1)
    assert route["categories"] == ["weather"]
    assert "- weather: Questions about the weather for a run." in seen["system"]
    assert "'is it too hot to run?'" in seen["system"]
    with pytest.raises(ValueError):
        seen["schema"](categories=["snow"], reason="x")                   # only names from the files


def test_clean_keeps_known_names_at_most_two_and_drops_off_topic_next_to_a_real_one():
    names = ["training", "nutrition", "logging", "off_topic"]
    assert classifier.clean(["logging", "training", "nutrition"], names) == ["logging", "training"]
    assert classifier.clean(["off_topic", "nutrition"], names) == ["nutrition"]
    assert classifier.clean(["other"], names) == [] and classifier.clean(["off_topic"], names) == ["off_topic"]


@pytest.fixture
def coach(temp_db, fake_llm, empty_calendar):
    return Coach(InMemorySaver(), use_fixtures=True)


def config(hour=9):
    return {"configurable": {"user_id": 1, "now": fixtures_now().replace(hour=hour, minute=0).isoformat(),
                             "use_fixtures": True}}


def test_two_categories_get_the_union_of_their_tools_and_both_bodies(temp_db):
    tools, guidance, override = agent.turn_setup(turn(config()), ["logging", "training"])
    names = {t.name for t in tools}
    assert {"log_food", "mark_done", "start_checkin", "submit_checkin"} <= names and "suggest_meal" not in names
    assert [name for name, _ in guidance] == ["logging", "training"] and override is None


def test_other_gets_every_tool_but_knowledge_search_and_every_body(temp_db):
    tools, guidance, _ = agent.turn_setup(turn(config()), ["other"])
    assert {t.name for t in tools} == {t.name for t in agent.all_tools()} - {"search_gssi"}
    assert "off_topic" not in [name for name, _ in guidance] and len(guidance) == len(skills.load()) - 1


@needs_fixtures
def test_off_topic_is_a_fixed_line_with_no_agent_call(coach, chat_model):
    from core import users
    reply = coach.handle_text(users.default(), "write me a poem", fixtures_now())
    assert reply == skills.load()["off_topic"].decline and chat_model.seen == []


@needs_fixtures
def test_no_category_goes_to_the_general_agent_and_is_kept_for_review(coach, chat_model, monkeypatch):
    from core import users
    monkeypatch.setattr(classifier, "classify", lambda *a, **k: {"categories": [], "reason": "nothing fits"})
    chat_model.script.append(say("Not sure I follow: is this about training or food?"))
    coach.handle_text(users.default(), "blorp", fixtures_now())
    assert set(chat_model.last_offered) == {t.name for t in agent.all_tools()} - agent.KNOWLEDGE_TOOLS
    [row] = db.unclassified_since(1, fixtures_now().replace(hour=0))
    assert (row["text"], row["reason"]) == ("blorp", "nothing fits")


@needs_fixtures
def test_a_classifier_failure_never_blocks_the_message(coach, chat_model, monkeypatch):
    from core import users

    def broken(*args, **kwargs):
        raise TimeoutError("slow")
    monkeypatch.setattr(classifier, "classify", broken)
    chat_model.script.append(say("Logged it."))
    assert coach.handle_text(users.default(), "had poha", fixtures_now()) == "Logged it."
    assert db.unclassified_since(1, fixtures_now().replace(hour=0))[0]["reason"] == "classifier failed (TimeoutError)"


@needs_fixtures
def test_a_waiting_checkin_always_brings_training_along(coach, chat_model, monkeypatch):
    from core import users
    user = users.default()
    chat_model.script += [call("start_checkin", request_text=None), say("")]
    coach.handle_text(user, "want to work out now", fixtures_now().replace(hour=7))
    monkeypatch.setattr(classifier, "classify", lambda *a, **k: {"categories": ["nutrition"], "reason": "food?"})
    chat_model.script += [call("submit_checkin", answer="4 2"), say("")]
    coach.handle_text(user, "4 2", fixtures_now().replace(hour=7, minute=1))     # offered: training came along


@needs_fixtures
def test_a_tool_the_route_doesnt_offer_is_refused_in_scripted_tests(coach, chat_model):
    from core import users
    chat_model.script += [call("log_food", text="eggs", time_text=None), say("Logged.")]
    with pytest.raises(AssertionError, match="log_food is not offered"):
        coach.handle_text(users.default(), "what do you know about me?", fixtures_now())     # labeled memory


def test_search_gssi_is_offered_only_where_a_skill_lists_it(temp_db):
    listing = sorted(s.name for s in skills.load().values() if "search_gssi" in s.tools)
    assert listing == ["fueling_hydration", "goal_program"]
    for name in skills.load():
        offered = {t.name for t in agent.turn_setup(turn(config()), [name])[0]}
        assert ("search_gssi" in offered) == (name in listing), name
