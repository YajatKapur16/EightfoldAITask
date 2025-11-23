from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient
import os
import json
import logging

# Initialize FastMCP
mcp = FastMCP("TavilySearch")
logger = logging.getLogger("tavily_server")
logging.basicConfig(level=logging.INFO)

# You must set TAVILY_API_KEY in your environment
tavily_api_key = os.getenv("TAVILY_API_KEY")
if not tavily_api_key:
    logger.warning("TAVILY_API_KEY environment variable not set!")

tavily = TavilyClient(api_key=tavily_api_key)

@mcp.tool()
def research_query(query: str) -> str:
    """
    Performs a 'smart' research search optimized for AI agents.
    It searches multiple sources, aggregates data, and returns a compiled answer.
    Use this for complex questions like 'Competitor analysis of X' or 'Financials of Y'.
    """
    try:
        logger.info(f"Executing Tavily search for query: {query}")
        # search_depth="advanced" digs deeper but costs 2 credits per call
        # include_answer=True asks Tavily to generate a direct answer
        response = tavily.search(
            query=query,
            search_depth="advanced", 
            max_results=5,
            include_answer=True,
            include_raw_content=False
        )
        
        # We simplify the output to save tokens for the LLM
        simplified_results = {
            "direct_answer": response.get("answer", ""),
            "sources": [
                {
                    "title": r["title"],
                    "url": r["url"],
                    "content": r["content"][:500] # Context snippet
                }
                for r in response.get("results", [])
            ]
        }
        
        return json.dumps(simplified_results, indent=2)

    except Exception as e:
        logger.error(f"Error executing Tavily search: {str(e)}")
        return f"Error executing Tavily search: {str(e)}"

if __name__ == "__main__":
    # Bind to 0.0.0.0 to allow external connections from other containers
    import uvicorn
    mcp.settings.port = 8000
    mcp.settings.host = "0.0.0.0"
    mcp.run(transport="sse")
