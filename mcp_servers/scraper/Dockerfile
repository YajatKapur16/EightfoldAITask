FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
RUN pip install mcp[cli] playwright uvicorn

# Install Playwright Browsers (Chromium only to save space)
RUN playwright install chromium
RUN playwright install-deps chromium

COPY server.py .

CMD ["python", "server.py"]
