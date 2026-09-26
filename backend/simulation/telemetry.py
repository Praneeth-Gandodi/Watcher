"""Per-robot telemetry read model for the dashboard.

The frozen contracts deliberately keep simulation bookkeeping out of ``Robot``
and ``RoutePlan``: they are decision-layer models, and a renderer should not be
able to mutate them. The runtime, however, already knows everything a viewer
needs -- footprint, speed, progress, why a robot is waiting, whether it is in a
conflict. This module projects that into a small, read-only, JSON-friendly
structure so the UI never has to re-derive it (and never has to guess).

Everything here is derived from backend state:

* action comes from ``Robot.status``, the runtime's trajectory, the wait graph,
  and the open conflict records
* progress comes from the committed trajectory's timestamps
* the yield reason comes from the runtime's recorded right-of-way decision
* transient actions (negotiating, replanning, completed) come from real
  canonical events inside a short simulation-time window

No value is invented, and nothing here mutates simulation state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.contracts.events import (
    EventEnvelope,
    EventPayload,
    NegotiationStartedPayload,
    RoutePlannedPayload,
    RouteReplannedPayload,
    TaskCompletedPayload,
)
from backend.contracts.models import (
    CommunicationState,
    ResolutionStatus,
    Robot,
    RobotStatus,
    Task,
    TaskStatus,
)
from backend.simulation.grid import Cell, GridIndex
from backend.simulation.runtime import RobotState, SimulationRuntime

__all__ = [
    "ROBOT_ACTIONS",
    "FleetTelemetry",
    "RobotTelemetry",
    "build_fleet_telemetry",
]

#: Every action the UI can render. Deliberately a closed set so the frontend
#: never has to interpret a free-form string.
ROBOT_ACTIONS: tuple[str, ...] = (
    "IDLE",
    "MOVING",
    "WAITING",
    "BLOCKED",
    "CHARGING",
    "DEGRADED",
    "FAILED",
    "OFFLINE",
    "NEGOTIATING",
    "REPLANNING",
    "TASK_COMPLETED",
)

#: How long a transient event keeps flashing an action, in simulation seconds.
_TRANSIENT_WINDOW_S = 2.0

#: Trajectory points returned per robot, to bound the payload at 500 robots.
_TRAJECTORY_POINT_LIMIT = 12


@dataclass(frozen=True, slots=True)
class RobotTelemetry:
    """Live per-robot state for rendering and the inspector."""

    robot_id: str
    # footprint and speed, straight from RobotProfile
    width_cells: int
    height_cells: int
    speed_mps: float
    battery_percent: float
    battery_percent_per_cell: float
    # placement
    cell_x: int
    cell_y: int
    position_x: float
    position_y: float
    # lifecycle
    status: str
    communication_state: str
    action: str
    action_reason: str
    workload: int
    capabilities: tuple[str, ...]
    failure_code: str | None
    # task and route
    task_id: str | None
    route_id: str | None
    route_status: str | None
    destination_x: int | None
    destination_y: int | None
    # progress
    progress: float
    cells_travelled: int
    remaining_cells: int
    remaining_time_s: float
    # conflicts
    conflict_with: tuple[str, ...]
    conflict_detected_at_s: float | None
    # right of way
    waiting_for_robot_id: str | None
    waiting_since_s: float | None
    # rendering aid: remaining timed placements, capped
    trail: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class FleetTelemetry:
    """Fleet-level aggregates so the UI never recomputes them per robot."""

    simulation_time_s: float
    counts_by_action: Mapping[str, int]
    battery_buckets: Mapping[str, int]
    open_conflict_pairs: tuple[tuple[str, ...], ...]
    deadlocked_robot_ids: tuple[str, ...]
    controller_available: bool
    revision: int
    last_event_sequence: int
    robots: tuple[RobotTelemetry, ...]


def _battery_bucket(percent: float, *, low: float, critical: float) -> str:
    if percent <= critical:
        return "critical"
    if percent <= low:
        return "low"
    return "normal"


def _transient_actions(
    events: Sequence[EventEnvelope[EventPayload]],
    now_s: float,
) -> tuple[dict[str, tuple[str, float]], set[str]]:
    """Return ``(robot_id -> (action, occurred_at_s), negotiating)``.

    Reads only real canonical events, and only inside a short window so the UI
    shows a state transition and then settles on the durable state.
    """

    flashed: dict[str, tuple[str, float]] = {}
    negotiating: set[str] = set()
    for event in events:
        age = now_s - event.occurred_at_s
        if age < 0 or age > _TRANSIENT_WINDOW_S:
            continue
        payload = event.payload
        robot_id: str | None = None
        action: str | None = None
        if isinstance(payload, (RoutePlannedPayload, RouteReplannedPayload)):
            robot_id = payload.route.robot_id
            action = "REPLANNING"
        elif isinstance(payload, TaskCompletedPayload):
            robot_id = payload.robot_id
            action = "TASK_COMPLETED"
        elif isinstance(payload, NegotiationStartedPayload):
            negotiating.update(payload.candidate_robot_ids)
        if robot_id is not None and action is not None:
            previous = flashed.get(robot_id)
            if previous is None or event.occurred_at_s >= previous[1]:
                flashed[robot_id] = (action, event.occurred_at_s)
    return flashed, negotiating


def _build_robot_telemetry(
    state: RobotState,
    *,
    runtime: SimulationRuntime,
    now_s: float,
    open_conflicts: Mapping[str, object],
    yields: Mapping[str, object],
    flashed: Mapping[str, tuple[str, float]],
    negotiating: frozenset[str],
    low_percent: float,
    critical_percent: float,
) -> RobotTelemetry:
    robot: Robot = state.robot
    grid: GridIndex = runtime.grid
    profile = state.profile
    cell = grid.cell_for_position(robot.position)
    index = runtime.grid

    conflict_with: tuple[str, ...] = ()
    conflict_at: float | None = None
    for record in open_conflicts.values():
        if robot.robot_id in getattr(record, "robot_ids", ()):  # type: ignore[attr-defined]
            conflict_with = tuple(
                other for other in record.robot_ids if other != robot.robot_id  # type: ignore[attr-defined]
            )
            conflict_at = float(record.detected_at_s)  # type: ignore[attr-defined]
            break

    yield_context = yields.get(robot.robot_id)
    waiting_for: str | None = None
    waiting_since: float | None = None
    action, action_reason = _derive_action(
        robot=robot,
        state=state,
        now_s=now_s,
        conflict_with=conflict_with,
        yield_context=yield_context,
        flashed=flashed,
        negotiating=negotiating,
    )
    if yield_context is not None:
        waiting_for = getattr(yield_context, "yields_to_robot_id", "") or None
        waiting_since = float(getattr(yield_context, "started_at_s", now_s))

    # Progress straight from the committed trajectory's timestamps.
    total = len(state.trajectory)
    progress = 0.0
    remaining_cells = 0
    remaining_time_s = 0.0
    trail: list[tuple[float, float, float]] = []
    if total > 0:
        reached = 0
        for index_of, point in enumerate(state.trajectory):
            if point.timestamp_s <= now_s:
                reached = index_of + 1
            else:
                if len(trail) < _TRAJECTORY_POINT_LIMIT:
                    position = grid.position_for_cell(point.cell)
                    trail.append((position.x, position.y, point.timestamp_s))
        remaining_cells = max(0, total - reached)
        progress = 1.0 if remaining_cells == 0 else min(
            1.0, max(0.0, (reached - 1) / max(1, total - 1))
        )
        if state.trajectory[-1].timestamp_s > now_s:
            remaining_time_s = state.trajectory[-1].timestamp_s - now_s

    destination: Cell | None = None
    if state.route is not None and state.route.waypoints:
        destination = index.cell_for_position(state.route.waypoints[-1])

    return RobotTelemetry(
        robot_id=robot.robot_id,
        width_cells=profile.width_cells,
        height_cells=profile.height_cells,
        speed_mps=profile.speed_mps,
        battery_percent=robot.battery_percent,
        battery_percent_per_cell=profile.battery_percent_per_cell,
        cell_x=cell[0],
        cell_y=cell[1],
        position_x=robot.position.x,
        position_y=robot.position.y,
        status=robot.status.value,
        communication_state=robot.communication_state.value,
        action=action,
        action_reason=action_reason,
        workload=robot.workload,
        capabilities=tuple(capability.value for capability in robot.capabilities),
        failure_code=robot.failure.code if robot.failure is not None else None,
        task_id=state.task_id,
        route_id=state.route.route_id if state.route is not None else None,
        route_status=state.route.status.value if state.route is not None else None,
        destination_x=destination[0] if destination else None,
        destination_y=destination[1] if destination else None,
        progress=progress,
        cells_travelled=state.cells_travelled,
        remaining_cells=remaining_cells,
        remaining_time_s=remaining_time_s,
        conflict_with=conflict_with,
        conflict_detected_at_s=conflict_at,
        waiting_for_robot_id=waiting_for,
        waiting_since_s=waiting_since,
        trail=tuple(trail),
    )


def _derive_action(
    *,
    robot: Robot,
    state: RobotState,
    now_s: float,
    conflict_with: tuple[str, ...],
    yield_context: object | None,
    flashed: Mapping[str, tuple[str, float]],
    negotiating: frozenset[str],
) -> tuple[str, str]:
    """Pick the single most important action, and say why."""

    if robot.status is RobotStatus.FAILED:
        recovery = "task reassignment" if state.task_id else "awaiting restore"
        return "FAILED", f"robot fault {robot.failure.code if robot.failure else ''}".strip() + f" ({recovery})"
    if robot.status is RobotStatus.OFFLINE:
        return "OFFLINE", "robot is not participating in the fleet"
    if robot.status is RobotStatus.CHARGING:
        return "CHARGING", "on a charging pad"

    # A committed hold outranks everything else: the robot is deliberately
    # stopped and the reason is a real right-of-way decision.
    if (
        yield_context is not None
        and float(getattr(yield_context, "release_at_s", 0.0)) > now_s
    ):
        other = getattr(yield_context, "yields_to_robot_id", "") or "another robot"
        waited = max(0.0, now_s - float(getattr(yield_context, "started_at_s", now_s)))
        return (
            "WAITING",
            f"right of way, yielding to {other} for {waited:.1f}s",
        )

    if robot.status is RobotStatus.DEGRADED:
        return "DEGRADED", "operating with reduced capability"

    # A durable, state-derived conflict outranks a transient event flash.
    if conflict_with:
        return "BLOCKED", f"conflict with {', '.join(conflict_with)}"
    if robot.status is RobotStatus.BLOCKED:
        return "BLOCKED", "no safe movement available"

    flash = flashed.get(robot.robot_id)
    if flash is not None:
        action, occurred_at_s = flash
        return action, f"backend reported {action.lower()} at t={occurred_at_s:.1f}s"
    if robot.robot_id in negotiating:
        return "NEGOTIATING", "bidding in an open negotiation round"

    if state.trajectory and not state.is_parked:
        last_cell = state.trajectory[-1].cell
        if state.current_cell != last_cell:
            return "MOVING", "following its planned route"
    if state.is_parked and state.route is not None and state.route.status.value == "completed":
        return "IDLE", "task complete, holding position"
    if state.task_id is not None:
        return "IDLE", "assigned, waiting to move"
    return "IDLE", "no task assigned"


def build_fleet_telemetry(runtime: SimulationRuntime) -> FleetTelemetry:
    """Project the runtime into the read model the dashboard consumes."""

    now_s = runtime.now_s
    policy = runtime.battery_manager.policy
    open_conflicts = {
        conflict_id: record
        for conflict_id, record in runtime.open_conflicts().items()
        if record.status is ResolutionStatus.OPEN
    }
    yields = runtime.yield_context()
    recent = runtime.event_stream.subscribe(
        max(0, runtime.event_stream.last_sequence - 400)
    )
    flashed, negotiating = _transient_actions(recent, now_s)

    robots = tuple(
        _build_robot_telemetry(
            state,
            runtime=runtime,
            now_s=now_s,
            open_conflicts=open_conflicts,
            yields=yields,
            flashed=flashed,
            negotiating=frozenset(negotiating),
            low_percent=policy.low_threshold_percent,
            critical_percent=policy.critical_threshold_percent,
        )
        for state in runtime.robot_states()
    )

    counts: dict[str, int] = {action: 0 for action in ROBOT_ACTIONS}
    buckets: dict[str, int] = {"normal": 0, "low": 0, "critical": 0}
    for robot in robots:
        counts[robot.action] = counts.get(robot.action, 0) + 1
        buckets[
            _battery_bucket(
                robot.battery_percent,
                low=policy.low_threshold_percent,
                critical=policy.critical_threshold_percent,
            )
        ] += 1

    snapshot = runtime.snapshot()
    return FleetTelemetry(
        simulation_time_s=now_s,
        counts_by_action=counts,
        battery_buckets=buckets,
        open_conflict_pairs=tuple(
            tuple(record.robot_ids) for record in open_conflicts.values()
        ),
        deadlocked_robot_ids=tuple(
            sorted(robot_id for robot_id, nodes in runtime.wait_graph().items() if nodes)
        ),
        controller_available=snapshot.controller_available,
        revision=snapshot.revision,
        last_event_sequence=snapshot.last_event_sequence,
        robots=robots,
    )


def task_index(tasks: Sequence[Task]) -> dict[str, Task]:
    """Return a ``task_id -> Task`` mapping for the dashboard."""

    return {task.task_id: task for task in tasks}


def robot_communication_lost(robot: Robot) -> bool:
    """Return whether a robot is physically present but out of contact."""

    return robot.communication_state is CommunicationState.LOST and (
        robot.status is not RobotStatus.FAILED
    )


def task_is_open(task: Task) -> bool:
    """Return whether a task is still in play."""

    return task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
