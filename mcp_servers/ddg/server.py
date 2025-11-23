from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
import json
import logging

# Initialize FastMCP
mcp = FastMCP("DuckDuckGoSearch")
logger = logging.getLogger("ddg_server")
logging.basicConfig(level=logging.INFO)

@mcp.tool()
def ddg_search(query: str, max_results: int = 5) -> str:
    """
    Performs a web search using DuckDuckGo.
    Use this as the PRIMARY search tool for discovery. It is fast and free.
    Returns a list of search results with titles, snippets, and URLs.
    """
    try:
        logger.info(f"Executing DDG search for query: {query}")
        results = DDGS().text(query, max_results=max_results)
        return json.dumps(results, indent=2)
    except Exception as e:
        logger.error(f"Error executing DDG search: {str(e)}")
        return f"Error executing DDG search: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    mcp.settings.port = 8000
    mcp.settings.host = "0.0.0.0"
    mcp.run(transport="sse")
