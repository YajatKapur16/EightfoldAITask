FROM python:3.11-slim

WORKDIR /app

# Install Tavily SDK and MCP
RUN pip install mcp[cli] tavily-python uvicorn

COPY server.py .

CMD ["python", "server.py"]
