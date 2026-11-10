"""Retrieval over the real GSSI index: 5 known questions find their article in the top 4.

Needs the index (python scripts/ingest_gssi.py) and makes 5 small embedding calls, so it runs only when
this file is named on the command line (pytest -q tests/test_gssi_retrieval.py) or with RUN_LIVE_LLM=1;
a plain `pytest -q` skips it.
"""
import os
import sys

import pytest

from mcp_servers.gssi import index
from mcp_servers.gssi.ingest import slug

ASKED = any("test_gssi_retrieval" in arg for arg in sys.argv) or bool(os.getenv("RUN_LIVE_LLM"))
pytestmark = [
    pytest.mark.skipif(not index.INDEX_DIR.exists(), reason="no GSSI index yet (python scripts/ingest_gssi.py)"),
    pytest.mark.skipif(not ASKED, reason="calls the embeddings API: name this file or set RUN_LIVE_LLM=1"),
]

# question -> the start of the articles' slugs that answer it (any one in the top 4 passes). Short prefixes,
# not whole slugs: a whole slug repeats the article's title, and no file in the repo carries GSSI text.
KNOWN = {
    "How many grams of carbohydrate per hour should I take during a long race?": ["sse-108-", "sse-106-"],
    "Should I drink to thirst or follow a drinking plan during a long run?": ["fluid-intake-strategies"],
    "How much caffeine before exercise improves performance, and how long before?": ["caffeine-and-exercise"],
    "Can I train my gut to handle more carbohydrate while running without stomach problems?":
        ["training-the-gut", "the-athlete-gut"],
    "Can a vegetarian athlete get enough protein for training?": ["vegetarian-and-vegan", "alternative-forms-of"],
}


def answers(prefixes: list[str], slugs) -> set[str]:
    return {s for s in slugs if any(s.startswith(p) for p in prefixes)}


@pytest.fixture(scope="module")
def collection():
    return index.open_collection()


@pytest.mark.parametrize("question", sorted(KNOWN))
def test_a_known_question_finds_its_article_in_the_top_4(collection, question):
    if not answers(KNOWN[question], [slug(u) for u in index.articles(collection)]):
        pytest.skip(f"no article starting {KNOWN[question]} is indexed (see the ingest report)")
    found = [slug(hit["url"]) for hit in index.search(question, 4, collection)]
    assert answers(KNOWN[question], found), f"top 4 for {question!r}: {found}"
