"""Minimal runnable backend skeleton.

Only health and metadata endpoints exist at bootstrap. Domain endpoints are
added by the simulation/integration work, not faked by this module.
"""

from __future__ import annotations

from fastapi import FastAPI

from .api import router

app = FastAPI(
    title="Watcher Fleet API",
    summary="Read models and commands for the decentralized robot simulation",
    version="0.1.0",
)
app.include_router(router, prefix="/api/v1")
