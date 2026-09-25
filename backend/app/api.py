"""HTTP and WebSocket entry points for the Watcher fleet runtime.

Endpoints under ``/api/v1`` serve the canonical projection and accept the
canonical commands:

* ``GET  /health``    process liveness, no domain decision
* ``GET  /snapshot``  the full ``SimulationSnapshot`` projection
* ``GET  /robots``    robots only, for narrow refreshes
* ``GET  /tasks``     tasks only
* ``GET  /events``    canonical events after a cursor
* ``GET  /metrics``   ``SystemMetrics`` only
* ``GET  /conflicts`` open conflicts only
* ``POST /commands``  one canonical ``ControlCommand``
* ``WS   /stream``    canonical events after a cursor

The WebSocket is a transport for the same events the REST history serves. It
adds no second vocabulary and makes no decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.service import SimulationService
from backend.contracts.commands import parse_command
from backend.contracts.events import parse_event
from backend.contracts.models import SimulationSnapshot, SystemMetrics

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulation"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "ok"
    service: str = "watcher-backend"
    version: str = "0.1.0"


class CommandAccepted(BaseModel):
    """Acknowledgement of an accepted command.

    Acceptance is not completion: the simulation applies the command on its own
    clock and the resulting state change arrives as canonical events.
    """

    model_config = ConfigDict(extra="forbid")
    command_id: str
    command_type: str
    accepted: bool = True
    last_event_sequence: int


def get_service(request: Request) -> SimulationService:
    """Resolve the simulation service belonging to the running application.

    The service is read from the request's own app rather than a module-level
    global, so a test can build an isolated app with its own runtime and never
    reach the process-wide one.
    """

    service: SimulationService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - only reachable before startup
        raise HTTPException(status_code=503, detail="simulation service is not running")
    return service


ServiceDep = Annotated[SimulationService, Depends(get_service)]


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Report process health without making any coordination decision."""

    return HealthResponse()


@router.get("/snapshot", response_model=SimulationSnapshot)
async def get_snapshot(service: ServiceDep) -> SimulationSnapshot:
    """Return the authoritative point-in-time projection."""

    return service.runtime.snapshot()


@router.get("/robots")
async def get_robots(service: ServiceDep) -> dict[str, Any]:
    """Return robots only, for clients that do not need the whole world."""

    snapshot = service.runtime.snapshot()
    return {
        "revision": snapshot.revision,
        "last_event_sequence": snapshot.last_event_sequence,
        "robots": [robot.model_dump(mode="json") for robot in snapshot.robots],
    }


@router.get("/tasks")
async def get_tasks(service: ServiceDep) -> dict[str, Any]:
    """Return the tracked task queue."""

    snapshot = service.runtime.snapshot()
    return {
        "revision": snapshot.revision,
        "tasks": [task.model_dump(mode="json") for task in snapshot.tasks],
    }


@router.get("/conflicts")
async def get_conflicts(service: ServiceDep) -> dict[str, Any]:
    """Return unresolved conflicts and their resolution state."""

    snapshot = service.runtime.snapshot()
    return {
        "revision": snapshot.revision,
        "conflicts": [conflict.model_dump(mode="json") for conflict in snapshot.conflicts],
    }


@router.get("/metrics", response_model=SystemMetrics)
async def get_metrics(service: ServiceDep) -> SystemMetrics:
    """Return the current system metrics."""

    return service.runtime.metrics()


@router.get("/events")
async def get_events(
    service: ServiceDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    """Return canonical events after the supplied cursor."""

    events = service.runtime.events_after(after_sequence, limit=limit)
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "last_event_sequence": service.runtime.snapshot().last_event_sequence,
    }


@router.post("/commands", response_model=CommandAccepted)
async def post_command(service: ServiceDep, payload: dict[str, Any]) -> CommandAccepted:
    """Validate and apply one canonical command.

    The body is validated against the canonical command union before anything
    is applied, so an invalid command changes no state at all.
    """

    try:
        command = parse_command(payload)
    except ValidationError as error:
        # ``include_context`` is dropped because a contract validator can raise
        # a plain ``ValueError``, and its exception object is not JSON
        # serialisable. Returning it raw turns a 422 into a 500.
        raise HTTPException(
            status_code=422,
            detail=error.errors(include_url=False, include_context=False, include_input=False),
        ) from error
    service.runtime.apply_command(command)
    service.request_tick()
    return CommandAccepted(
        command_id=str(command.command_id),
        command_type=command.command_type.value,
        last_event_sequence=service.runtime.snapshot().last_event_sequence,
    )


@router.websocket("/stream")
async def stream_events(websocket: WebSocket) -> None:
    """Stream canonical events to a client after its cursor.

    The stream sends three kinds of frame and nothing else:

    * ``event``    one canonical event envelope
    * ``snapshot`` the full projection, on request and on a slow heartbeat
    * ``cursor``   the latest sequence, so a client can resynchronise cheaply

    A client that cannot keep up still receives the snapshot frame, which is
    what makes a dropped or delayed stream recoverable.
    """

    service: SimulationService | None = getattr(websocket.app.state, "service", None)
    if service is None:  # pragma: no cover - only reachable before startup
        await websocket.close(code=1013)
        return

    await websocket.accept()
    after_sequence = _cursor_from_websocket(websocket)
    last_sent = after_sequence

    async def send_snapshot() -> None:
        nonlocal last_sent
        snapshot = service.runtime.snapshot()
        last_sent = snapshot.last_event_sequence
        await websocket.send_json(
            {"kind": "snapshot", "data": snapshot.model_dump(mode="json")}
        )

    await send_snapshot()
    try:
        while True:
            events = service.runtime.events_after(last_sent, limit=200)
            for event in events:
                await websocket.send_json(
                    {"kind": "event", "data": event.model_dump(mode="json")}
                )
                last_sent = max(last_sent, event.sequence)
            if not events:
                await websocket.send_json(
                    {
                        "kind": "cursor",
                        "data": {"last_event_sequence": service.runtime.snapshot().last_event_sequence},
                    }
                )
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
            except TimeoutError:
                continue
    except WebSocketDisconnect:
        return
    except (RuntimeError, ConnectionError) as error:  # pragma: no cover - transport loss
        logger.debug("event stream closed: %s", error)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
        return


def _cursor_from_websocket(websocket: WebSocket) -> int:
    raw = websocket.query_params.get("after_sequence", "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@router.get("/")
async def service_root() -> JSONResponse:
    """Point clients at the canonical endpoints."""

    return JSONResponse(
        {
            "service": "watcher-backend",
            "endpoints": [
                "/api/v1/health",
                "/api/v1/snapshot",
                "/api/v1/robots",
                "/api/v1/tasks",
                "/api/v1/conflicts",
                "/api/v1/metrics",
                "/api/v1/events?after_sequence={cursor}",
                "/api/v1/commands",
                "/api/v1/stream?after_sequence={cursor}",
            ],
        }
    )
