# syntax=docker/dockerfile:1
# BNK-Forge MCP Server
# Lightweight container — Python + MCP SDK + httpx. No heavy deps.
#
# Build: docker build -t bnk-forge-mcp mcp-server/
# Run:   docker run -p 8081:8081 -e BNK_FORGE_API_URL=http://backend:8000 bnk-forge-mcp

# python:3.11-slim — digest-pinned (Slice B / #300); re-pin when bumping the tag.
FROM python:3.11-slim@sha256:f9fa7f851e38bfb19c9de3afbc4b86ae7176ea7aaf94535c31df5458d5849457 AS base

LABEL maintainer="BNK-Forge Team"
LABEL description="MCP Server for BNK-Forge — AI-accessible tools for F5 BIG-IP Next for Kubernetes"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy build metadata
COPY pyproject.toml README.md ./

# Install third-party dependencies (cached — only busted when pyproject.toml changes).
# Listed explicitly so this layer doesn't need source code at all.
# Keep in sync with [project.dependencies] in pyproject.toml.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install "mcp>=1.26.0,<2" "httpx>=0.27.0" hatchling

# Copy full source code (only this layer rebuilds on code changes)
COPY src/ ./src/

# Install the package itself (fast — deps already cached, only builds the wheel)
RUN pip install --no-deps .

# Non-root user for security
RUN adduser --disabled-password --gecos "" mcpuser
USER mcpuser

EXPOSE 8081

# Health check — POST to MCP endpoint with proper headers (GET returns 406 per MCP spec)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; req=urllib.request.Request('http://localhost:8081/mcp',headers={'Accept':'application/json,text/event-stream','Content-Type':'application/json'},data=b'{\"jsonrpc\":\"2.0\",\"method\":\"ping\",\"id\":1}'); urllib.request.urlopen(req)" || exit 1

CMD ["bnk-forge-mcp"]
