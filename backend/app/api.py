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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
from backend.simulation.scenarios import scenario_specs
from backend.simulation.telemetry import build_fleet_telemetry

#: Grid presets the scenario editor offers.
GRID_PRESETS: tuple[tuple[int, int], ...] = ((20, 15), (30, 20), (40, 25), (50, 30))

#: Fleet presets for the scale test.
FLEET_PRESETS: tuple[int, ...] = (10, 25, 50, 100, 250, 500)

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


class RandomAssignmentRequest(BaseModel):
    """How much to blur the bid distance term for a randomised round."""

    model_config = ConfigDict(extra="forbid")
    #: ``0`` is the deterministic allocation; larger values blur it further.
    jitter: float = Field(default=0.75, ge=0, le=5, allow_inf_nan=False)
    #: Replaying a seed reproduces the same bids and the same winners.
    seed: int | None = None


class SimulationSpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    multiplier: float


class RobotTelemetryResponse(BaseModel):
    """Live per-robot state for rendering and the inspector.

    Every field is derived from backend state. ``width_cells``/``height_cells``/
    ``speed_mps`` come straight from ``RobotProfile``, which is the single
    source of truth for robot geometry, so the rendered footprint always matches
    the footprint the collision engine uses.
    """

    model_config = ConfigDict(extra="forbid")
    robot_id: str
    width_cells: int
    height_cells: int
    speed_mps: float
    battery_percent: float
    battery_percent_per_cell: float
    cell_x: int
    cell_y: int
    position_x: float
    position_y: float
    status: str
    communication_state: str
    action: str
    action_reason: str
    workload: int
    capabilities: tuple[str, ...]
    failure_code: str | None
    task_id: str | None
    route_id: str | None
    route_status: str | None
    destination_x: int | None
    destination_y: int | None
    progress: float
    cells_travelled: int
    remaining_cells: int
    remaining_time_s: float
    conflict_with: tuple[str, ...]
    conflict_detected_at_s: float | None
    waiting_for_robot_id: str | None
    waiting_since_s: float | None
    #: Remaining timed placements as ``[x_m, y_m, t_s]``, capped server side.
    trail: tuple[tuple[float, float, float], ...]


class TelemetryResponse(BaseModel):
    """Fleet telemetry plus per-robot detail."""

    model_config = ConfigDict(extra="forbid")
    simulation_time_s: float
    scenario: str
    counts_by_action: dict[str, int]
    battery_buckets: dict[str, int]
    open_conflict_pairs: tuple[tuple[str, ...], ...]
    deadlocked_robot_ids: tuple[str, ...]
    controller_available: bool
    revision: int
    last_event_sequence: int
    robots: tuple[RobotTelemetryResponse, ...]


class ScenarioInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    layout: str
    description: str
    columns: int
    rows: int
    robot_count: int
    task_count: int


class ScenarioListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: tuple[ScenarioInfo, ...]
    grid_presets: tuple[tuple[int, int], ...]
    fleet_presets: tuple[int, ...]
    active: str


class ScenarioRequest(BaseModel):
    """Load a preset, optionally resized for a scale test."""

    model_config = ConfigDict(extra="forbid")
    name: str
    robot_count: int | None = Field(default=None, ge=1, le=1000)
    columns: int | None = Field(default=None, ge=6, le=200)
    rows: int | None = Field(default=None, ge=6, le=200)
    seed: int | None = Field(default=None, ge=0)
    run_initial_tasks: bool = True


class ScenarioLoadedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str
    robots: int
    tasks: tuple[str, ...]
    columns: int
    rows: int
    last_event_sequence: int
    simulation_time_s: float


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


# ----------------------------------------------------------------------
# Simulation read models for the dashboard
# ----------------------------------------------------------------------


@router.get("/telemetry", response_model=TelemetryResponse)
async def get_telemetry(coordinator: Coordinator) -> TelemetryResponse:
    """Return live per-robot state and fleet aggregates.

    This is the endpoint that carries ``RobotProfile`` data. ``/snapshot`` keeps
    the canonical contract shapes untouched, so the geometry the renderer uses
    comes from the same registry the planner and collision engine read, rather
    than from anything the client remembers.
    """

    telemetry = build_fleet_telemetry(coordinator.runtime)
    return TelemetryResponse(
        simulation_time_s=telemetry.simulation_time_s,
        scenario=coordinator.scenario_name or "default",
        counts_by_action=dict(telemetry.counts_by_action),
        battery_buckets=dict(telemetry.battery_buckets),
        open_conflict_pairs=telemetry.open_conflict_pairs,
        deadlocked_robot_ids=telemetry.deadlocked_robot_ids,
        controller_available=telemetry.controller_available,
        revision=telemetry.revision,
        last_event_sequence=telemetry.last_event_sequence,
        robots=tuple(
            RobotTelemetryResponse(
                robot_id=robot.robot_id,
                width_cells=robot.width_cells,
                height_cells=robot.height_cells,
                speed_mps=robot.speed_mps,
                battery_percent=robot.battery_percent,
                battery_percent_per_cell=robot.battery_percent_per_cell,
                cell_x=robot.cell_x,
                cell_y=robot.cell_y,
                position_x=robot.position_x,
                position_y=robot.position_y,
                status=robot.status,
                communication_state=robot.communication_state,
                action=robot.action,
                action_reason=robot.action_reason,
                workload=robot.workload,
                capabilities=robot.capabilities,
                failure_code=robot.failure_code,
                task_id=robot.task_id,
                route_id=robot.route_id,
                route_status=robot.route_status,
                destination_x=robot.destination_x,
                destination_y=robot.destination_y,
                progress=robot.progress,
                cells_travelled=robot.cells_travelled,
                remaining_cells=robot.remaining_cells,
                remaining_time_s=robot.remaining_time_s,
                conflict_with=robot.conflict_with,
                conflict_detected_at_s=robot.conflict_detected_at_s,
                waiting_for_robot_id=robot.waiting_for_robot_id,
                waiting_since_s=robot.waiting_since_s,
                trail=robot.trail,
            )
            for robot in telemetry.robots
        ),
    )


@router.post("/simulation/dispatch", response_model=CommandResponse)
async def post_dispatch(coordinator: Coordinator) -> CommandResponse:
    """Negotiate and assign every task that is still waiting for an owner.

    A scenario registers its tasks up front, so this is what starts the rounds.
    It is the same negotiation and allocation path a single task takes.
    """

    events = coordinator.dispatch_pending_tasks()
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type="DISPATCH",
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


class ManualAssignmentRequest(BaseModel):
    """Bind one task to one chosen robot."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    robot_id: str


@router.post("/tasks/assign", response_model=CommandResponse)
async def post_task_assignment(
    coordinator: Coordinator,
    request: ManualAssignmentRequest,
) -> CommandResponse:
    """Assign a task to a specific robot from the console.

    The operator picks the owner, the backend still does the work: the route is
    requested, planned or replanned, and the safety pass runs before the robot
    is allowed to move. A task that already had an owner is migrated and the
    canonical ``TASK_REASSIGNED`` event is published.
    """

    try:
        events = coordinator.assign_task_to_robot(request.task_id, request.robot_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type="TASK_ASSIGNED",
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


@router.post("/simulation/random-assignment", response_model=CommandResponse)
async def post_random_assignment(
    coordinator: Coordinator,
    request: RandomAssignmentRequest | None = None,
) -> CommandResponse:
    """Re-run every open task's negotiation with randomised bid costs.

    The console's random-allocation control. This is a real allocation round:
    open tasks lose their current owner, bids are collected again with a
    randomised distance term, and allocation still picks the cheapest bid. A
    different robot therefore wins because it genuinely bid less this time,
    rather than because the UI relabelled anything.
    """

    body = request or RandomAssignmentRequest()
    events = coordinator.randomize_assignments(body.jitter, body.seed)
    snapshot = coordinator.snapshot()
    return CommandResponse(
        accepted=True,
        command_type="RANDOM_ASSIGNMENT",
        produced_event_types=tuple(event.event_type.value for event in events),
        last_event_sequence=snapshot.last_event_sequence,
        revision=snapshot.revision,
        simulation_time_s=snapshot.simulation_time_s,
    )


@router.get("/scenarios", response_model=ScenarioListResponse)
async def get_scenarios(coordinator: Coordinator) -> ScenarioListResponse:
    """List the scenario presets and the grid/fleet size presets."""

    return ScenarioListResponse(
        scenarios=tuple(
            ScenarioInfo(
                name=spec.name,
                layout=spec.layout,
                description=spec.description,
                columns=spec.columns,
                rows=spec.rows,
                robot_count=spec.robot_count,
                task_count=spec.task_count,
            )
            for spec in scenario_specs()
        ),
        grid_presets=GRID_PRESETS,
        fleet_presets=FLEET_PRESETS,
        active=coordinator.scenario_name or "default",
    )


@router.post("/simulation/scenario", response_model=ScenarioLoadedResponse)
async def post_scenario(
    coordinator: Coordinator,
    request: ScenarioRequest,
) -> ScenarioLoadedResponse:
    """Load a named preset, optionally resized for a scale test.

    Rebuilds the simulation from scratch, which is what makes a preset
    reproducible. The dashboard's event cursor is reset with the old stream, so
    the client must resynchronise from sequence 0 after this call.
    """

    try:
        coordinator.load_scenario(
            request.name,
            robot_count=request.robot_count,
            columns=request.columns,
            rows=request.rows,
            seed=request.seed,
            run_initial_tasks=request.run_initial_tasks,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    snapshot = coordinator.snapshot()
    return ScenarioLoadedResponse(
        scenario=request.name,
        robots=len(snapshot.robots),
        tasks=tuple(task.task_id for task in snapshot.tasks),
        columns=snapshot.world.columns,
        rows=snapshot.world.rows,
        last_event_sequence=snapshot.last_event_sequence,
        simulation_time_s=snapshot.simulation_time_s,
    )


