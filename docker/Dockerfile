# Multi-stage Dockerfile for spliceailookup-link.

# --- Builder Stage ---
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY uv.lock pyproject.toml README.md ./

RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev --active --no-install-project

# --- Production Stage ---
FROM python:3.14-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    SPLICEAILOOKUP_LINK_MCP_TRANSPORT=unified \
    SPLICEAILOOKUP_LINK_MCP_HOST=0.0.0.0 \
    SPLICEAILOOKUP_LINK_MCP_PORT=8603 \
    SPLICEAILOOKUP_LINK_MCP_PATH=/mcp

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    chown -R app:app /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./spliceailookup_link ./spliceailookup_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./

RUN /opt/venv/bin/pip install -e . --no-deps

USER app

EXPOSE 8603

CMD ["spliceailookup-link", "--transport", "unified", "--host", "0.0.0.0", "--port", "8603"]
