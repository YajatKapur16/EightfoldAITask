FROM python:3.11-slim

WORKDIR /app

# Install DDG and MCP
RUN pip install mcp[cli] duckduckgo-search uvicorn

COPY server.py .

CMD ["python", "server.py"]
