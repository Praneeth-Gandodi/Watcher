"""Runnable Watcher backend.

The application owns the API router. The service is deliberately explicit about
time: simulation time advances only when a caller asks it to, so there is no
background clock to pause, step, or rewind.

When a console build is present the same process also serves it, so a deployment
is a single container on a single URL with no CORS configuration and no reverse
proxy. When no build is present the API still runs and says so, rather than
failing to start.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router

#: Built console, when the image or a local ``npm run build`` has produced one.
CONSOLE_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    """Build the application around the API router and the console."""

    app = FastAPI(
        title="Watcher Fleet API",
        summary="Decentralized multi-robot coordination simulation",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("WATCHER_CORS_ORIGINS", "*").split(","),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api/v1")
    _mount_console(app)
    return app


def _mount_console(app: FastAPI) -> None:
    """Serve the built console from the same origin as the API.

    The router is registered first, so every ``/api/v1`` path is matched before
    the SPA fallback can see it. The fallback resolves a real file when one
    exists and returns ``index.html`` otherwise, which is what a single-page app
    needs for client-side routes.
    """

    if not CONSOLE_DIST.is_dir():
        @app.get("/")
        async def console_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "console build not found",
                    "hint": "run `npm run build` in frontend/",
                },
                status_code=404,
            )

        return

    assets = CONSOLE_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def console_index() -> FileResponse:
        return FileResponse(CONSOLE_DIST / "index.html")

    @app.get("/{path:path}")
    async def console_spa(path: str) -> FileResponse:
        candidate = CONSOLE_DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(CONSOLE_DIST / "index.html")


app = create_app()
