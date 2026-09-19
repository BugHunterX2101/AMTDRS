# Principal — hosted demo image.
#
# The important property: this image runs with NO credentials. The API starts,
# the dashboard serves, and jobs execute against the bundled fixture through the
# in-process FakeSandbox. Supply NEBIUS_API_KEY and NEBIUS_PROJECT_ID as
# environment variables to switch it to real Token Factory inference and real
# Sandboxes; nothing else changes.

# ---- stage 1: dashboard -----------------------------------------------------
# Kept in its own stage so a missing or broken npm install cannot take the API
# down with it. app.py mounts dashboard/dist only if it exists.
FROM node:20-slim AS dashboard
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---- stage 2: runtime -------------------------------------------------------
FROM python:3.11-slim

# git is not optional: the baseline builder clones the target repository and the
# gates apply diffs with real git operations.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first, so source edits do not invalidate the install. Both
# packages' __init__.py must exist before `pip install -e`: setuptools'
# package-finder (packages.find, include = ["principal*", "mcp_code_graph*"])
# walks the tree at install time to build the editable finder's package map,
# and a package that does not exist yet is a package it never learns to map —
# copying the rest of the source afterward does not retroactively register it.
# This was found the hard way: the image built and served cleanly, and only
# the MCP mount silently failed at startup with "No module named
# 'mcp_code_graph'", logged as a warning rather than a crash.
COPY pyproject.toml README.md ./
COPY principal/__init__.py principal/__init__.py
COPY mcp_code_graph/__init__.py mcp_code_graph/__init__.py
RUN pip install --no-cache-dir -e ".[dev]"

COPY principal/ principal/
COPY mcp_code_graph/ mcp_code_graph/
COPY bench/ bench/
COPY tests/ tests/
COPY LICENSE NOTICE ./
COPY docs/ docs/

COPY --from=dashboard /build/dist dashboard/dist

# Writable state. Kept under /app so a single mounted volume preserves
# everything a run produces.
RUN mkdir -p runs snapshots .model_cache

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    PRINCIPAL_DB=/app/principal.db \
    PRINCIPAL_RUNS_DIR=/app/runs \
    PRINCIPAL_SNAPSHOTS_DIR=/app/snapshots \
    PRINCIPAL_CACHE_DIR=/app/.model_cache

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"

# Shell form so $PORT is expanded — most container hosts inject it.
CMD principal serve --host 0.0.0.0 --port ${PORT}
