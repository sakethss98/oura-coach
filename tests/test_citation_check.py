"""The runtime citation check (core/citations.py check_markers): a [n] stays only on a claim that the passages of
result n support; otherwise the marker goes and the sentence stays, as the coach's own words.

The pure tests always run (scripted vectors). The six real cases (tests/data/citation_cases.jsonl: 3 over-cites
from the live M4-12/M4-13 runs, 3 correct cites) hold our claims and the chunk ids of the passages the coach was
given, never passage text: the passages are read from the local index and the claims embedded for real, so
they run like tests/test_gssi_retrieval.py (name this file, or RUN_LIVE_LLM=1). Each case's label is the gpt-5
citation judge's (tests/conftest.judge_citations), the same verdict 3 times out of 3.
"""
import json
import os
import sys

import pytest

from core import citations
from mcp_servers.gssi import index
from tests.conftest import real_embeddings

CASES = [json.loads(line) for line in open(index.ROOT / "tests" / "data" / "citation_cases.jsonl")]
ASKED = any("test_citation_check" in arg for arg in sys.argv) or bool(os.getenv("RUN_LIVE_LLM"))
SOURCES = [{"n": 1, "title": "T", "url": "https://example.org/a",
            "passage": "Sodium in drinks helps the body hold on to fluid after exercise. Flavor makes people drink more."},
           {"n": 2, "title": "U", "url": "https://example.org/b", "passage": "Drinking to thirst works on short runs."}]


def same_vector(texts):
    return [[1.0, 0.0] for _ in texts]


def test_the_parts_of_a_claim_leave_out_who_says_it_and_their_own_day():
    assert citations.claim_parts("Readiness is 75 and your week was light, so follow the plan — GSSI recommends "
                                 "scaling carbs to the run's demands") == ["scaling carbs to the run's demands"]
    assert citations.claim_parts("Sodium is lost in sweat, and flavored drinks help you drink more") == [
        "Sodium is lost in sweat", "flavored drinks help you drink more"]
    assert citations.claim_parts("Readiness is 75") == []                       # nothing GSSI could say


def test_a_supported_marker_stays_an_unsupported_one_goes_and_the_sentence_stays():
    text = ("Sodium in drinks helps you hold on to fluid after exercise [1].\n"
            "Readiness is 75, so go easy [1]. Thirst works on short runs [2]; water is fine for a 10k [2].")
    checked, decisions = citations.check_markers(text, SOURCES, same_vector)
    assert checked == ("Sodium in drinks helps you hold on to fluid after exercise [1].\n"
                       "Readiness is 75, so go easy. Thirst works on short runs [2]; water is fine for a 10k.")
    assert [d["supported"] for d in decisions] == [True, False, True, False]


def test_similarity_counts_too_and_a_failed_check_keeps_no_marker():
    def far(texts):                     # the claim and the passage share words but not meaning
        return [[1.0, 0.0]] + [[0.0, 1.0]] * (len(texts) - 1)
    assert citations.check_markers("Sodium in drinks helps hold fluid [1].", SOURCES, far)[0] == \
        "Sodium in drinks helps hold fluid."

    def down(texts):
        raise ConnectionError
    text, [decision] = citations.check_markers("Sodium in drinks helps hold fluid [1].", SOURCES, down)
    assert text == "Sodium in drinks helps hold fluid." and decision["checks"][0]["error"] == "ConnectionError"


def test_a_marker_no_result_has_is_left_for_finish_to_drop():
    text, decisions = citations.check_markers("Fine [7].", SOURCES, same_vector)
    assert text == "Fine [7]." and decisions == [] and citations.finish(text, SOURCES) == "Fine."


@real_embeddings
@pytest.mark.skipif(not index.INDEX_DIR.exists(), reason="no GSSI index yet (python scripts/ingest_gssi.py)")
@pytest.mark.skipif(not ASKED, reason="calls the embeddings API: name this file or set RUN_LIVE_LLM=1")
@pytest.mark.parametrize("case", CASES, ids=[c["what"][:40] for c in CASES])
def test_real_over_cites_lose_their_marker_and_correct_cites_keep_it(case):
    found = index.open_collection().get(ids=case["chunk_ids"], include=["documents"])
    by_id = dict(zip(found["ids"], found["documents"]))
    passages = [index.passage(by_id[i]) for i in case["chunk_ids"]]
    checks = citations.claim_checks(case["claim"], passages)
    print(case["what"], [(c["terms"], c["similarity"]) for c in checks])
    assert all(c["supported"] for c in checks) is case["supported"], checks
