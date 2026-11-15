"""A test MCP server shaped like the pinned USDA server failing (tests/test_trace_redaction.py): search_foods
returns the error as text, the way the real one does (`f"Error searching foods: {e}"`, where httpx's message has
the request url with the api key in it), and get_food_details raises, so the MCP result is an error."""
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("usda-error", log_level="WARNING")
API = "https://api.nal.usda.gov/fdc/v1"


def failed_url(path: str, query: str) -> str:
    return f"{API}/{path}?api_key={os.environ['USDA_API_KEY']}&{query}"


@mcp.tool()
def search_foods(query: str, page_size: int = 50) -> str:
    return (f"Error searching foods: Client error '400 Bad Request' for url "
            f"'{failed_url('foods/search', f'query={query}&pageSize={page_size}')}'")


@mcp.tool()
def get_food_details(fdc_id: int) -> dict:
    raise RuntimeError(f"Server error '503 Service Unavailable' for url '{failed_url(f'food/{fdc_id}', 'format=full')}'")


if __name__ == "__main__":
    mcp.run()
