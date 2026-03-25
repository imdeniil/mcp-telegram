FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/
COPY README.md ./

# Install the package
RUN uv pip install --system -e .

# Create directories
RUN mkdir -p /app/downloads

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

# Default command
CMD ["mcp-telegram", "daemon"]
