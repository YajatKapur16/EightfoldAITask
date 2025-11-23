from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
import asyncio
import logging

# Initialize FastMCP
mcp = FastMCP("AdvancedScraper")
logger = logging.getLogger("scraper")

@mcp.tool()
async def scrape_dynamic_webpage(url: str) -> str:
    """
    Visits a URL using a headless browser (Chromium). 
    Renders JavaScript, waits for content to load, and extracts the main text.
    Use this for modern websites that don't show content with simple curl requests.
    """
    async with async_playwright() as p:
        try:
            # Launch browser in headless mode
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            
            # Create a new context with a realistic user agent to avoid bot detection
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            logger.info(f"Navigating to {url}...")
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # Wait for common reading indicators (optional, helps with slow JS)
            await asyncio.sleep(2) 
            
            # Extract readable content using evaluation script
            # This logic tries to grab the main article body or falls back to body text
            content = await page.evaluate("""() => {
                const article = document.querySelector('article') || document.querySelector('main') || document.body;
                
                // Remove clutter (ads, navs, footers)
                const clutter = article.querySelectorAll('nav, footer, script, style, .ad, .advertisement');
                clutter.forEach(el => el.remove());
                
                return article.innerText;
            }""")
            
            await browser.close()
            
            # Cleanup text
            cleaned_lines = [line.strip() for line in content.split('\n') if line.strip()]
            result = '\n'.join(cleaned_lines)
            
            return result[:15000] # Return first 15k chars to fit LLM context context

        except Exception as e:
            return f"Failed to scrape {url}: {str(e)}"

if __name__ == "__main__":
    # Bind to 0.0.0.0 to allow external connections from other containers
    import uvicorn
    mcp.settings.port = 8000
    mcp.settings.host = "0.0.0.0"
    mcp.run(transport="sse")
