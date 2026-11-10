"""The GSSI MCP server (FastMCP, stdio): one tool, search_gssi, over our own index (data/gssi_index/).

Started by core/mcp_tools.py as `python -m mcp_servers.gssi.server`, with only OPENAI_API_KEY in its
environment (to embed the query). Returns passages of at most 700 characters with their title, url and
section, so the coach can cite the article without copying it.
"""
from mcp.server.fastmcp import FastMCP

from mcp_servers.gssi import index

mcp = FastMCP("gssi", log_level="WARNING")   # INFO lines would land in the coach's terminal


@mcp.tool()
def search_gssi(query: str, k: int = 4) -> dict:
    """Search Gatorade Sports Science Institute (GSSI) Sports Science Exchange articles: sports nutrition,
    fueling, hydration, caffeine, protein, sleep. Returns {"results": the k closest passages (title, url, section,
    year, passage of at most 700 characters)}. k: 1 to 8."""
    return {"results": index.search(query, k)}


if __name__ == "__main__":
    mcp.run()
