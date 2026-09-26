# syntax=docker/dockerfile:1

# Watcher ships as one service. The console is built into static files and
# served by the same FastAPI process that publishes the simulation, so a
# deployment is a single container on a single URL with no CORS or proxy
# configuration to get wrong in front of a judge.

# ------------------------------------------------------------------- console
FROM node:22-alpine AS console

WORKDIR /build

# Dependencies are copied first so a source-only change reuses the cached
# install layer instead of reinstalling on every build.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is only here for the health check below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# README.md is a build input: pyproject.toml declares it as the project readme,
# so the wheel cannot be built without it.
COPY pyproject.toml README.md ./
COPY backend/ ./backend/

RUN pip install --no-cache-dir .

COPY --from=console /build/dist ./frontend/dist

# Runs unprivileged. Nothing in the service writes to the image: the simulation
# holds its world, fleet, and event log in memory and rebuilds from its seed.
RUN useradd --system --create-home --uid 10001 watcher \
    && chown -R watcher:watcher /app
USER watcher

EXPOSE 8000

# Reads $PORT so the check is correct on hosts that assign a different one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/v1/health" || exit 1

# One worker on purpose. The runtime is a single authoritative in-process world;
# several workers would each get their own fleet and a caller would see a
# different world per request.
#
# The port comes from $PORT with a local fallback. Hosts that inject PORT route
# traffic to whatever it holds, and an exec-form CMD cannot expand a variable.
# The inner exec keeps uvicorn as PID 1 so it takes SIGTERM and shuts down
# cleanly on redeploy.
CMD ["/bin/sh", "-c", "exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
