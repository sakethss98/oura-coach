"""The MCP client (core/mcp_tools.py) against real stdio servers: our GSSI server over a made-up index (test
hash embedder, no network), a server that can't start, and one that answers too late. search_gssi never
raises: when the server is down or slow it says to answer from general knowledge."""
import json
import time

import pytest

from core import mcp_tools
from mcp_servers.gssi import index
from tests.conftest import real_mcp

pytestmark = real_mcp
URL = "https://www.gssiweb.org/en/sports-science-exchange/article/test-article"


@pytest.fixture
def servers(monkeypatch, tmp_path):
    """A fresh set of sessions and a config we can change; every server is closed after the test."""
    config = {"gssi": {"command": "python", "args": ["-m", "mcp_servers.gssi.server"],
                       "env": ["GSSI_INDEX_DIR", "GSSI_EMBEDDER"], "timeout_s": 15}}
    monkeypatch.setattr(mcp_tools, "server_config", lambda: config)
    monkeypatch.setattr(mcp_tools, "_servers", mcp_tools._Servers())
    monkeypatch.setenv("GSSI_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("GSSI_EMBEDDER", "hash")
    yield config
    mcp_tools.close()


def made_up_index(path):
    collection = index.open_collection(path)
    chunks = index.chunk([("Fluids", "Made-up text about water and thirst on short runs. " * 40),
                          ("Carbs", "Made-up text about rice and bread before long runs. " * 40)])
    index.replace_article(collection, index.hash_embed, {"url": URL, "title": "Test", "year": 2020,
                                                         "doc_hash": "h"}, chunks)


def test_search_gssi_over_stdio_returns_cited_passages(servers, tmp_path):
    made_up_index(tmp_path / "index")
    content, artifact = mcp_tools.search_gssi.func("rice bread before long runs", [], 2)
    result = json.loads(content)
    assert result["available"] and len(result["results"]) == 2
    assert {r["url"] for r in result["results"]} == {URL} and all(len(r["passage"]) <= 700 for r in result["results"])
    assert artifact["sources"] == result["results"] and [r["n"] for r in result["results"]] == [1, 1]   # one article
    started = time.perf_counter()
    mcp_tools.search_gssi.func("water", [], 1)                                # the session stays open
    assert time.perf_counter() - started < 2


def test_an_empty_index_says_nothing_was_found(servers):
    result = json.loads(mcp_tools.search_gssi.func("water", [], 1)[0])
    assert result["available"] and result["results"] == []


def test_a_server_that_cannot_start_gives_the_general_knowledge_note(servers):
    servers["gssi"]["args"] = ["-m", "no_such_module_here"]
    started = time.perf_counter()
    content, artifact = mcp_tools.search_gssi.func("water", [], 1)
    assert json.loads(content) == {"available": False, "reason": mcp_tools.UNAVAILABLE} and artifact == {"sources": []}
    assert time.perf_counter() - started < 10


def test_a_slow_server_times_out_and_gives_the_note(servers):
    servers["gssi"].update(args=["tests/mcp_slow_server.py"], timeout_s=2)
    started = time.perf_counter()
    assert json.loads(mcp_tools.search_gssi.func("water", [], 1)[0])["available"] is False
    assert time.perf_counter() - started < 6


def test_the_server_gets_only_the_env_variables_it_lists(servers, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-for-the-server")
    env = mcp_tools.connection("gssi")["env"]
    assert set(env) == {"GSSI_INDEX_DIR", "GSSI_EMBEDDER"}
