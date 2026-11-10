"""A test MCP server whose search_gssi answers too late (tests/test_mcp_tools.py checks the timeout)."""
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("slow", log_level="WARNING")


@mcp.tool()
def search_gssi(query: str, k: int = 4) -> dict:
    time.sleep(10)
    return {"results": []}


if __name__ == "__main__":
    mcp.run()
