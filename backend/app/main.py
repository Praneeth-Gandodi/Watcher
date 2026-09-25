"""Runnable Watcher backend.

The application owns one ``SimulationService`` and wires the API to it. The
service is started and stopped with the FastAPI lifespan so tests can build an
app without a background clock and without leaking a tick task.
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

from backend.app.api import router
from backend.app.service import SimulationService

DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"


def create_app(service: SimulationService | None = None) -> FastAPI:
    """Build the application around a simulation service."""

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

    @contextlib.asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        instance.state.service = service or SimulationService.from_env()
        await instance.state.service.start()
        try:
            yield
        finally:
            await instance.state.service.stop()

    app.router.lifespan_context = lifespan
    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built dashboard when it is present.

    Serving the SPA from the same origin as the API means the deployed system
    is a single service with no CORS or proxy configuration, and the jury can
    open one URL. When no build exists the API still runs and reports that the
    dashboard is absent, rather than failing to start.
    """

    if not DASHBOARD_DIST.is_dir():
        @app.get("/dashboard")
        async def dashboard_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "dashboard build not found",
                    "hint": "run `npm run build` in dashboard/",
                },
                status_code=404,
            )

        return

    assets = DASHBOARD_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def dashboard_index() -> FileResponse:
        return FileResponse(DASHBOARD_DIST / "index.html")

    @app.get("/{path:path}")
    async def dashboard_spa(path: str) -> FileResponse:
        candidate = DASHBOARD_DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DASHBOARD_DIST / "index.html")


app = create_app()
