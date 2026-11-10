"""The runtime citation check (core/citations.py check_markers): a [n] stays only on a claim that the passages of
result n state, by one yes/no call per marker (job citation_check); otherwise the marker goes and the sentence
stays, as the coach's own words.

The pure tests always run (a scripted check). The six real cases (tests/data/citation_cases.jsonl: 3 over-cites
from the live M4-12/M4-13 runs, 3 correct cites) hold our claims and the chunk ids of the passages the coach was
given, never passage text: the passages are read from the local index and the claims checked by the real
model, so they run like tests/test_gssi_retrieval.py (name this file, or RUN_LIVE_LLM=1). Each case's label is
the gpt-5 citation judge's on the claim without its credit ("GSSI notes"), the same verdict 3 times out of 3.
"""
import json
import os
import sys
import threading
import time

import pytest

from core import citations, reply_rules
from mcp_servers.gssi import index
from tests.conftest import real_check

CASES = [json.loads(line) for line in open(index.ROOT / "tests" / "data" / "citation_cases.jsonl")]
ASKED = any("test_citation_check" in arg for arg in sys.argv) or bool(os.getenv("RUN_LIVE_LLM"))
SOURCES = [{"n": 1, "title": "T", "url": "https://example.org/a",
            "passage": "Sodium in drinks helps the body hold on to fluid after exercise. Flavor makes people drink more."},
           {"n": 2, "title": "U", "url": "https://example.org/b", "passage": "Drinking to thirst works on short runs."}]


def test_a_supported_marker_stays_an_unsupported_one_goes_and_the_sentence_stays():
    text = ("Sodium in drinks helps you hold on to fluid after exercise [1].\n"
            "Readiness is 75, so go easy [1]. Thirst works on short runs [2]; water is fine for a 10k [2].")
    checked, decisions = citations.check_markers(text, SOURCES)
    assert checked == ("Sodium in drinks helps you hold on to fluid after exercise [1].\n"
                       "Readiness is 75, so go easy. Thirst works on short runs [2]; water is fine for a 10k.")
    assert [d["supported"] for d in decisions] == [True, False, True, False]
    assert [d["claim"] for d in decisions][2:] == ["Thirst works on short runs", "; water is fine for a 10k"]


def test_each_marker_is_its_own_question_and_they_run_at_once(monkeypatch):
    asked, running, most = [], [0], [0]
    lock = threading.Lock()

    def slow(claim, passages, user_id=None):
        with lock:
            asked.append((claim.strip(), tuple(passages)))
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return {"states": True, "reason": "ok"}
    monkeypatch.setattr(citations, "passage_states", slow)
    started = time.perf_counter()
    citations.check_markers("Sodium helps [1]. Thirst works [2]. Flavor helps [1].", SOURCES)
    assert sorted(asked) == sorted([("Sodium helps", (SOURCES[0]["passage"],)),
                                    ("Thirst works", (SOURCES[1]["passage"],)),
                                    ("Flavor helps", (SOURCES[0]["passage"],))])
    assert most[0] == 3 and time.perf_counter() - started < 0.5          # in parallel, not one after another


def test_a_failed_check_keeps_no_marker(monkeypatch):
    def down(claim, passages, user_id=None):
        raise ConnectionError
    monkeypatch.setattr(citations, "passage_states", down)
    text, [decision] = citations.check_markers("Sodium in drinks helps hold fluid [1].", SOURCES)
    assert text == "Sodium in drinks helps hold fluid." and decision["reason"] == "check failed (ConnectionError)"


def test_a_marker_no_result_has_is_left_for_finish_to_drop():
    text, decisions = citations.check_markers("Fine [7].", SOURCES)
    assert text == "Fine [7]." and decisions == [] and citations.finish(text, SOURCES) == "Fine."


@real_check
@pytest.mark.skipif(not index.INDEX_DIR.exists(), reason="no GSSI index yet (python scripts/ingest_gssi.py)")
@pytest.mark.skipif(not ASKED, reason="calls the model: name this file or set RUN_LIVE_LLM=1")
@pytest.mark.parametrize("case", CASES, ids=[c["what"][:40] for c in CASES])
def test_real_over_cites_lose_their_marker_and_correct_cites_keep_it(case):
    found = index.open_collection().get(ids=case["chunk_ids"], include=["documents"])
    by_id = dict(zip(found["ids"], found["documents"]))
    passages = [index.passage(by_id[i]) for i in case["chunk_ids"]]
    verdict = citations.passage_states(reply_rules.without_credit(case["claim"]), passages)
    print(case["what"], verdict)
    assert verdict["states"] is case["supported"], verdict
