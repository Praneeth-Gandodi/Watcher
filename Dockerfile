# syntax=docker/dockerfile:1

# Watcher ships as one service. The dashboard is built into static files and
# served by the same FastAPI process that publishes the simulation, so a
# deployment is a single container with a single URL and no CORS or proxy
# configuration to get wrong in front of a judge.

# ---------------------------------------------------------------- dashboard
FROM node:22-alpine AS dashboard

WORKDIR /build

# Dependencies are copied first so a source-only change reuses the cached
# install layer instead of reinstalling on every build.
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY dashboard/ ./
RUN npm run build

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WATCHER_FLEET_SIZE=500 \
    WATCHER_SEED=2026

WORKDIR /app

# The simulation owns a hard tick budget that a large fleet is sized to fit
# inside, and the container advertises exactly that many cores. Raising this
# above the fleet's real parallelism would only add scheduler overhead.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
COPY safety/ ./safety/
COPY config/ ./config/

RUN pip install --no-cache-dir .

COPY --from=dashboard /build/dist ./dashboard/dist

# Runs unprivileged. Nothing in the service writes to the image.
RUN useradd --system --create-home --uid 10001 watcher \
    && chown -R watcher:watcher /app
USER watcher

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries 3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/v1/health" || exit 1

# One worker on purpose. The simulation runtime is a single authoritative
# in-process world behind a lock; running several workers would give each one its
# own world and the jury would see a different fleet per request. Scale by
# raising WATCHER_FLEET_SIZE, not by adding workers.
#
# The port comes from $PORT with a local fallback. Hosts that inject PORT
# (Render, Railway, Heroku, Cloud Run, Fly.io) route traffic to whatever it
# holds, and an exec-form CMD cannot expand it. The inner exec keeps uvicorn as
# PID 1 so it takes SIGTERM directly and shuts down cleanly on redeploy.
CMD ["/bin/sh", "-c", "exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
