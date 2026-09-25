"""Stable HTTP entry points established during bootstrap."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    service: Literal["watcher-backend"] = "watcher-backend"
    version: Literal["0.1.0"] = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Report process health without making any coordination decision."""

    return HealthResponse()
