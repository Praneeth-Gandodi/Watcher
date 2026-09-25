"""Stable HTTP entry points.

``GET /health`` is the bootstrap contract and is unchanged. The simulation
endpoints are the minimum the dashboard needs: read the authoritative snapshot,
read metrics, read robots/tasks, stream events after a cursor, and submit
canonical commands. They are thin adapters over the composition root -- they
never make a coordination decision themselves.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.composition import FleetCoordinator, get_coordinator
from backend.contracts.commands import (
    ControlCommand,
    CreateTaskCommand,
    InjectCommunicationLossCommand,
    InjectRobotFailureCommand,
    PauseSimulationCommand,
    ResetSimulationCommand,
    ResumeSimulationCommand,
    RestoreRobotCommand,
    SetSimulationSpeedCommand,
    parse_command,
)
from backend.contracts.models import (
    Robot,
    RoutePlan,
    SimulationSnapshot,
    SystemMetrics,
    Task,
    WorldState,
)

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    service: Literal["watcher-backend"] = "watcher-backend"
    version: Literal["0.1.0"] = "0.1.0"


class SimulationResponse(BaseModel):
    """A read model with only what the dashboard needs to draw the world."""

    model_config = ConfigDict(extra="forbid")
    world: WorldState
    robots: tuple[Robot, ...]
    routes: tuple[RoutePlan, ...]
    tasks: tuple[Task, ...]
    simulation_time_s: float
    revision: int
    last_event_sequence: int
    controller_available: bool


class EventResponse(BaseModel):
    """One canonical event, exactly as the runtime published it."""

    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_type: str
    producer: str
    correlation_id: str
    occurred_at_s: float
    payload: dict[str, Any]


class CommandResponse(BaseModel):
    """The outcome of a submitted command, plus the resulting state cursor."""

    model_config = ConfigDict(extra="forbid")
    accepted: bool
    command_type: str
    produced_event_types: tuple[str, ...]
    last_event_sequence: int
    revision: int
    simulation_time_s: float


class CreateTaskRequest(BaseModel):
    """A minimal task creation body, expanded into a canonical command."""

    model_config = ConfigDict(extra="forbid")
    task: Task


class InjectFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    robot_id: str
    failure: dict[str, Any]


class InjectCommunicationLossRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    robot_id: str
    timeout_s: float = 3.0


class RestoreRobotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    robot_id: str


class ResetSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 2026


class SimulationSpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    multiplier: float


Coordinator = Annotated[FleetCoordinator, Depends(get_coordinator)]


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Report process health without making any coordination decision."""

    return HealthResponse()


@router.get("/snapshot", response_model=SimulationResponse)
async def get_snapshot(coordinator: Coordinator) -> SimulationResponse:
    """Return the authoritative backend state for the dashboard."""

    snapshot: SimulationSnapshot = coordinator.snapshot()
    return SimulationResponse(
        world=snapshot.world,
        robots=snapshot.robots,
        routes=snapshot.routes,
        tasks=snapshot.tasks,
        simulation_time_s=snapshot.simulation_time_s,
        revision=snapshot.revision,
        last_event_sequence=snapshot.last_event_sequence,
        controller_available=snapshot.controller_available,
    )


@router.get("/metrics", response_model=SystemMetrics)
async def get_metrics(coordinator: Coordinator) -> SystemMetrics:
    """Return fleet, task, conflict, and recovery counters."""

    return coordinator.metrics()


@router.get("/robots", response_model=list[Robot])
async def get_robots(coordinator: Coordinator) -> list[Robot]:
    """Return every robot in canonical ``robot_id`` order."""

    return list(coordinator.runtime.robots())


@router.get("/tasks", response_model=list[Task])
async def get_tasks(coordinator: Coordinator) -> list[Task]:
    """Return every known task in canonical ``task_id`` order."""

    return list(coordinator.runtime.tasks())


@router.get("/routes", response_model=list[RoutePlan])
async def get_routes(coordinator: Coordinator) -> list[RoutePlan]:
    """Return every planned route."""

    return list(coordinator.runtime.routes())


@router.get("/world", response_model=WorldState)
async def get_world(coordinator: Coordinator) -> WorldState:
    """Return the authoritative grid geometry."""

    return coordinator.runtime.world


@router.get("/events", response_model=list[EventResponse])
async def get_events(
    coordinator: Coordinator,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> list[EventResponse]:
    """Return canonical events after a cursor, as the dashboard polls them."""

    return [
        EventResponse(
            sequence=event.sequence,
            event_type=event.event_type.value,
            producer=event.producer,
            correlation_id=event.correlation_id,
            occurred_at_s=event.occurred_at_s,
            payload=event.payload.model_dump(mode="json"),
        )
        for event in coordinator.events_after(after_sequence)
    ]


@router.post("/commands", response_model=CommandResponse)
async def post_command(
    coordinator: Coordinator,
    body: Annotated[dict[str, Any], Body()],
) -> CommandResponse:
    """Submit one canonical command and return the events it produced.

    The body is validated with the contracts' own discriminated-union parser, so
    the HTTP surface and ``parse_command`` can never disagree about what a valid
    command is.
    """

    try:
        command = parse_command(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _apply(coordinator, command)


@router.post("/tasks", response_model=CommandResponse, status_code=201)
async def post_task(
    coordinator: Coordinator,
    request: CreateTaskRequest,
) -> CommandResponse:
    """Create a task and run negotiation, allocation, and route planning."""

    command = CreateTaskCommand(
        command_id=_command_id(),
        issued_at_s=coordinator.runtime.now_s,
        task=request.task,
    )
    events = coordinator.submit_command(command)
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type=command.command_type.value,
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


@router.post("/faults/failure", response_model=CommandResponse, status_code=201)
async def post_failure(
    coordinator: Coordinator,
    request: InjectFailureRequest,
) -> CommandResponse:
    """Inject a robot failure and let Agent 1 decide about reassignment."""

    command = InjectRobotFailureCommand(
        command_id=_command_id(),
        issued_at_s=coordinator.runtime.now_s,
        robot_id=request.robot_id,
        failure=request.failure,
    )
    return _apply(coordinator, command)


@router.post("/faults/communication-loss", response_model=CommandResponse, status_code=201)
async def post_communication_loss(
    coordinator: Coordinator,
    request: InjectCommunicationLossRequest,
) -> CommandResponse:
    """Inject a communication loss without removing the robot physically."""

    command = InjectCommunicationLossCommand(
        command_id=_command_id(),
        issued_at_s=coordinator.runtime.now_s,
        robot_id=request.robot_id,
        timeout_s=request.timeout_s,
    )
    return _apply(coordinator, command)


@router.post("/faults/restore", response_model=CommandResponse, status_code=201)
async def post_restore(
    coordinator: Coordinator,
    request: RestoreRobotRequest,
) -> CommandResponse:
    """Restore a failed robot or a lost communication link."""

    command = RestoreRobotCommand(
        command_id=_command_id(),
        issued_at_s=coordinator.runtime.now_s,
        robot_id=request.robot_id,
    )
    return _apply(coordinator, command)


@router.post("/simulation/reset", response_model=CommandResponse)
async def post_reset(
    coordinator: Coordinator,
    request: ResetSimulationRequest | None = None,
) -> CommandResponse:
    """Reset the simulation and keep the configured fleet."""

    command = ResetSimulationCommand(
        command_id=_command_id(),
        issued_at_s=coordinator.runtime.now_s,
        seed=(request or ResetSimulationRequest()).seed,
    )
    return _apply(coordinator, command)


@router.post("/simulation/pause", response_model=CommandResponse)
async def post_pause(coordinator: Coordinator) -> CommandResponse:
    """Pause simulated time. Robot execution stops with the clock."""

    return _apply(
        coordinator,
        PauseSimulationCommand(
            command_id=_command_id(), issued_at_s=coordinator.runtime.now_s
        ),
    )


@router.post("/simulation/resume", response_model=CommandResponse)
async def post_resume(coordinator: Coordinator) -> CommandResponse:
    """Resume simulated time."""

    return _apply(
        coordinator,
        ResumeSimulationCommand(
            command_id=_command_id(), issued_at_s=coordinator.runtime.now_s
        ),
    )


@router.post("/simulation/speed", response_model=CommandResponse)
async def post_speed(
    coordinator: Coordinator,
    request: SimulationSpeedRequest,
) -> CommandResponse:
    """Set the simulation speed multiplier, validated by the command contract."""

    if request.multiplier <= 0 or request.multiplier > 10:
        raise HTTPException(
            status_code=422,
            detail="multiplier must be greater than 0 and at most 10",
        )
    return _apply(
        coordinator,
        SetSimulationSpeedCommand(
            command_id=_command_id(),
            issued_at_s=coordinator.runtime.now_s,
            multiplier=request.multiplier,
        ),
    )


@router.post("/simulation/advance", response_model=CommandResponse)
async def post_advance(
    coordinator: Coordinator,
    ticks: Annotated[int, Query(ge=0, le=10_000)] = 1,
) -> CommandResponse:
    """Advance deterministic simulation ticks.

    The backend never uses wall-clock time for the simulation, so a dashboard
    drives the clock explicitly and every run is reproducible.
    """

    events = coordinator.advance(ticks)
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type="ADVANCE",
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


def _apply(coordinator: FleetCoordinator, command: ControlCommand) -> CommandResponse:
    events = coordinator.submit_command(command)
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type=command.command_type.value,
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


def _command_id():
    from uuid import uuid4

    return uuid4()


