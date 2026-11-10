"""MCP servers (config/mcp.yaml), called from the sync agent, and the agent tools that use them.

langchain-mcp-adapters gives async tools over an MCP session. The agent runs sync (graph.invoke, ToolNode),
so one background thread runs an asyncio loop that holds a persistent session per server, and
`call(server, tool, args)` runs the tool there with the server's timeout (15 s). A server that is down,
slow or broken gives None, never an exception, and is started again on the next call.

The agent tools that use a server (search_gssi) are defined here with a fixed schema, so skills can list
them and the fallback works even when the server never started. They are offered only to the skills that
list them (never to "other").
"""
import asyncio
import json
import logging
import os
import sys
import threading
from functools import lru_cache
from typing import Annotated

import yaml
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import InjectedState

from core import citations
from core.config import ROOT

CONFIG_PATH = ROOT / "config" / "mcp.yaml"
DEFAULT_TIMEOUT_S = 15
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def server_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())["servers"]


def connection(name: str) -> dict:
    """The stdio connection for a server: this interpreter for "python", only the listed env variables."""
    c = server_config()[name]
    command = sys.executable if c["command"] == "python" else c["command"]
    env = {key: os.environ[key] for key in c.get("env", []) if os.getenv(key)}
    return {"transport": "stdio", "command": command, "args": c.get("args", []), "env": env, "cwd": str(ROOT)}


class _Servers:
    """Persistent sessions on a background event loop (one thread for every server)."""

    def __init__(self):
        self.loop = None
        self.thread_lock = threading.Lock()
        self.sessions: dict[str, dict] = {}      # name -> {"tools": {tool name: tool}, "closed": Event}
        self.starting: dict[str, asyncio.Lock] = {}

    def _loop(self) -> asyncio.AbstractEventLoop:
        with self.thread_lock:
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
                threading.Thread(target=self.loop.run_forever, name="mcp", daemon=True).start()
        return self.loop

    async def _hold(self, name: str, ready: asyncio.Future) -> None:
        """Open the session and keep it open (stdio sessions must close in the task that opened them)."""
        closed = asyncio.Event()
        try:
            client = MultiServerMCPClient({name: connection(name)})
            async with client.session(name) as session:
                tools = {t.name: t for t in await load_mcp_tools(session)}
                self.sessions[name] = {"tools": tools, "closed": closed}
                ready.set_result(None)
                await closed.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else RuntimeError(type(e).__name__))
        finally:
            if self.sessions.get(name, {}).get("closed") is closed:
                self.sessions.pop(name, None)

    async def _call(self, name: str, tool_name: str, args: dict):
        lock = self.starting.setdefault(name, asyncio.Lock())
        async with lock:
            if name not in self.sessions:
                ready = asyncio.get_running_loop().create_future()
                asyncio.create_task(self._hold(name, ready))
                await ready
        session = self.sessions[name]
        try:
            return await session["tools"][tool_name].ainvoke(args)
        except Exception:
            session["closed"].set()          # a broken session is dropped; the next call starts the server again
            raise

    def call(self, name: str, tool_name: str, args: dict, timeout: float):
        future = asyncio.run_coroutine_threadsafe(self._call(name, tool_name, args), self._loop())
        try:
            return future.result(timeout)
        except BaseException:
            future.cancel()
            raise

    def close(self) -> None:
        for session in list(self.sessions.values()):
            self.loop.call_soon_threadsafe(session["closed"].set)


_servers = _Servers()


def _as_data(result):
    """A tool result as JSON data: the adapters return the text content (a string or content blocks)."""
    if isinstance(result, tuple):            # (content, artifact)
        result = result[0]
    if isinstance(result, list):
        result = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return result


def call(server: str, tool_name: str, args: dict):
    """The tool's result as JSON data, or None when the server is down, slow or failed (logged by error type)."""
    timeout = server_config()[server].get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
        return _as_data(_servers.call(server, tool_name, args, timeout))
    except Exception as e:
        log.warning("MCP %s.%s unavailable: %s", server, tool_name, type(e).__name__)
        return None


def close() -> None:
    _servers.close()


# --- agent tools ----------------------------------------------------------------

UNAVAILABLE = "the GSSI library can't be reached right now"


def _numbered_this_turn(messages: list) -> list[dict]:
    """Sources earlier searches numbered in this turn (since their latest message)."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return [s for m in messages[last_human + 1:] if isinstance(m, ToolMessage) and m.name == "search_gssi"
            and isinstance(m.artifact, dict) for s in m.artifact.get("sources", [])]


@tool(response_format="content_and_artifact")
def search_gssi(query: str, state: Annotated[list, InjectedState("messages")], k: int = 4) -> tuple[str, dict]:
    """Search the Gatorade Sports Science Institute (GSSI) Sports Science Exchange articles for the science behind
    a fueling, hydration, caffeine, protein or sleep answer. query: the question in a few words. k: 1-8 passages.
    Answer in your own words (never copy a passage) and put the result's number, like [1], after the claim it
    supports. Never paste a url: code adds the source line."""
    found = call("gssi", "search_gssi", {"query": query, "k": k})
    if not isinstance(found, dict) or not isinstance(found.get("results"), list):
        return json.dumps({"available": False, "reason": UNAVAILABLE}), {"sources": []}
    found = citations.numbered(_numbered_this_turn(state), found["results"])
    if not found:
        return json.dumps({"available": True, "results": []}), {"sources": []}
    return (json.dumps({"available": True, "results": found,
                        "cite_as": "[n]"}),
            {"sources": found})


MCP_TOOLS = [search_gssi]
