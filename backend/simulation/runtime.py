"""Simulation clock, fleet runtime, and the fleet-wide ``SafetyEngine``.

This module owns the authoritative backend state Agent 3 renders:

* simulated time (pause/resume/speed, never wall-clock),
* robot movement along committed trajectories,
* battery, failure, and communication observations,
* safety evaluation, right-of-way resolution, and deadlock checks,
* canonical event production plus the ``SimulationSnapshot``/``SystemMetrics``
  projections.

Event-driven safety
-------------------

Safety is **not** re-checked on a per-second timer. The runtime re-evaluates
the fleet whenever something material changes: a route is planned or replanned,
a robot starts, finishes, faults, or is restored, or a safety action rewrites a
trajectory. A step that only advances the clock leaves the safety decision
untouched. For N=10 robots one pass is 45 pair comparisons, which is why no
spatial index is needed yet.

Dependencies
------------

The runtime imports ``backend.contracts``, ``backend.safety``, and
``backend.simulation.grid``. It never imports ``backend.negotiation`` or
``backend.allocation``: assignments and reassignments arrive as canonical
``TASK_ASSIGNED``/``TASK_REASSIGNED`` events through :meth:`handle_event`, and
this module reports safety observations as canonical events instead of deciding
who should pick up the work.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import isfinite
from uuid import UUID, uuid5

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
)
from backend.contracts.events import (
    BatteryLowPayload,
    ConflictDetectedPayload,
    DeadlockDetectedPayload,
    EventEnvelope,
    EventPayload,
    RecoveryStartedPayload,
    RobotFailedPayload,
    RoutePlannedPayload,
    RouteReplannedPayload,
    RouteRequestedPayload,
    TaskAssignedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskReassignedPayload,
)
from backend.contracts.interfaces import SafetyEngine
from backend.contracts.models import (
    CommunicationState,
    Conflict,
    ConflictKind,
    ConflictSeverity,
    FailureInfo,
    FailureKind,
    Position2D,
    RecoveryAction,
    RecoveryActionType,
    ResolutionStatus,
    Robot,
    RobotStatus,
    RoutePlan,
    RouteStatus,
    SafetyDecision,
    SimulationSnapshot,
    SystemMetrics,
    Task,
    TaskStatus,
    WorldState,
)
from backend.safety.battery import BatteryManager, BatteryObservation, BatteryPolicy
from backend.safety.deadlock import (
    build_deadlock_report,
    build_recovery_action,
    find_deadlock_cycles,
    recover_deadlock,
)
from backend.safety.failure import (
    communication_lost_payload,
    evolve_robot,
    mark_communication_lost,
    restore_communication,
    restore_robot,
)
from backend.safety.pathfinding import AStarPathPlanner, PathStep
from backend.safety.right_of_way import (
    DEFAULT_SAFETY_MARGIN_S,
    FleetConflict,
    RobotPriority,
    YieldResolution,
    find_earliest_conflict,
    try_timing_resolution,
)
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.safety.trajectory import (
    Trajectory,
    TrajectoryPoint,
    create_trajectory,
    point_at_time,
)
from backend.simulation.grid import Cell, GridIndex, footprint_cells
from backend.simulation.world import FleetBlueprint

__all__ = [
    "EVENT_PRODUCER",
    "FleetSafetyEngine",
    "InMemoryEventStream",
    "RobotState",
    "SafetyEvaluation",
    "SafetyStep",
    "SimulationClock",
    "SimulationRuntime",
    "evolve_task",
]

#: Producer identity published on every Agent 2 event.
EVENT_PRODUCER = "agent-2-safety"

_EVENT_NAMESPACE = UUID("6f2c1f4e-2a2f-5a4a-9a0f-2f3d1b7c4e55")
_DEFAULT_COMMUNICATION_TIMEOUT_S = 3.0
_DEFAULT_TICK_RATE_HZ = 10.0
#: The length of one fixed tick, used when a held route is re-timed.
_DEFAULT_TICK_S = 1.0 / _DEFAULT_TICK_RATE_HZ


def _derived_uuid(kind: str, *parts: str) -> UUID:
    """Return a deterministic UUID, so event IDs are reproducible across runs."""

    return uuid5(_EVENT_NAMESPACE, "|".join((kind, *parts)))


def _derived_id(prefix: str, kind: str, *parts: str) -> str:
    """Return a deterministic kebab-case identifier for a derived record."""

    return f"{prefix}-{_derived_uuid(kind, *parts).hex[:8]}"


def evolve_task(task: Task, **updates: object) -> Task:
    """Return a revalidated ``Task`` with ``updates`` applied."""

    data = task.model_dump()
    data.update(updates)
    return Task.model_validate(data)


class InMemoryEventStream:
    """Bounded, monotonically sequenced in-process event stream.

    Implements the canonical ``EventStream`` protocol. The dashboard reads it as
    a projection; only the runtime publishes.
    """

    __slots__ = ("_events", "_max_events", "_sequence")

    def __init__(self, max_events: int = 10_000) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: list[EventEnvelope[EventPayload]] = []
        self._max_events = max_events
        self._sequence = 0

    @property
    def last_sequence(self) -> int:
        return self._sequence

    def __len__(self) -> int:
        return len(self._events)

    def next_sequence(self) -> int:
        """Reserve and return the next monotonically increasing sequence."""

        self._sequence += 1
        return self._sequence

    async def publish(self, event: EventEnvelope[object]) -> None:
        self.publish_nowait(event)

    def publish_nowait(self, event: EventEnvelope[EventPayload]) -> None:
        """Append an externally produced event, e.g. an Agent 1 decision event.

        The stream is the single sequence authority for the whole system, so
        publishing an event also advances the counter the runtime allocates
        from. That keeps Agent 1 and Agent 2 events on one monotonic numbering.
        """

        if event.sequence <= self._last_retained_sequence():
            raise ValueError("events must be published in increasing sequence order")
        self._events.append(event)
        if event.sequence > self._sequence:
            self._sequence = event.sequence
        overflow = len(self._events) - self._max_events
        if overflow > 0:
            del self._events[:overflow]

    def _last_retained_sequence(self) -> int:
        return self._events[0].sequence - 1 if self._events else 0

    def subscribe(
        self, after_sequence: int = 0
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Return every retained event after ``after_sequence``."""

        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        return tuple(event for event in self._events if event.sequence > after_sequence)

    def clear(self) -> None:
        self._events.clear()
        self._sequence = 0


class SimulationClock:
    """Simulated time with pause, resume, and a speed multiplier."""

    __slots__ = ("_now_s", "_paused", "_speed_multiplier")

    def __init__(self, start_time_s: float = 0.0) -> None:
        if not isfinite(start_time_s) or start_time_s < 0:
            raise ValueError("start_time_s must be a non-negative finite number")
        self._now_s = start_time_s
        self._paused = False
        self._speed_multiplier = 1.0

    @property
    def now_s(self) -> float:
        return self._now_s

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed_multiplier(self) -> float:
        return self._speed_multiplier

    def advance(self, delta_s: float) -> float:
        """Advance simulated time and return the amount actually applied."""

        if not isfinite(delta_s) or delta_s < 0:
            raise ValueError("delta_s must be a non-negative finite number")
        if self._paused:
            return 0.0
        applied = delta_s * self._speed_multiplier
        self._now_s += applied
        return applied

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_speed(self, multiplier: float) -> None:
        if not isfinite(multiplier) or multiplier <= 0:
            raise ValueError("multiplier must be a positive finite number")
        self._speed_multiplier = multiplier

    def reset(self, start_time_s: float = 0.0) -> None:
        self._now_s = start_time_s
        self._paused = False
        self._speed_multiplier = 1.0


@dataclass(frozen=True, slots=True)
class RobotState:
    """Agent 2's per-robot simulation state.

    The canonical ``Robot`` is never used as a simulation scratchpad: movement,
    progress, and route bookkeeping live here, so the shared contract does not
    grow simulation-only fields.
    """

    robot: Robot
    profile: RobotProfile
    current_cell: Cell
    route: RoutePlan | None = None
    trajectory: Trajectory = ()
    task_id: str | None = None
    task_started_at_s: float | None = None
    blocked_since_s: float | None = None
    resume_after_s: float | None = None
    cells_travelled: int = 0
    charged_cells: int = 0
    last_contact_at_s: float = 0.0
    #: Set while the robot is backing off a one-cell passage to let another
    #: robot past. The retreat is a real planned route, not a pause: the robot
    #: moves aside and then resumes its own task.
    retreating_since_s: float | None = None

    @property
    def robot_id(self) -> str:
        return self.robot.robot_id

    @property
    def has_trajectory(self) -> bool:
        return bool(self.trajectory)

    @property
    def is_parked(self) -> bool:
        """Return whether the robot finished its route and holds its cell."""

        return bool(self.trajectory) and not self.task_id


@dataclass(frozen=True, slots=True)
class YieldContext:
    """Why a robot is currently yielding, and to whom.

    The runtime already decides right-of-way; this records the reason alongside
    the decision so a consumer can explain it without re-deriving anything. It
    is cleared as soon as the release time passes.
    """

    robot_id: str
    yields_to_robot_id: str
    conflict_id: str
    started_at_s: float
    release_at_s: float
    reason: str


@dataclass(frozen=True, slots=True)
class SafetyStep:
    """One conflict found and the resolution chosen for it."""

    conflict: FleetConflict
    outcome: str
    resolution: YieldResolution | None = None

    @property
    def is_yield(self) -> bool:
        return self.outcome == "yield"

    @property
    def is_blocked(self) -> bool:
        return self.outcome == "blocked"


@dataclass(frozen=True, slots=True)
class SafetyEvaluation:
    """Outcome of one fleet-wide safety pass."""

    evaluated_at_s: float
    steps: tuple[SafetyStep, ...]
    resolved: bool
    open_conflicts: tuple[Conflict, ...]
    reason: str

    @property
    def resolutions(self) -> int:
        return sum(1 for step in self.steps if step.is_yield)

    @property
    def unresolved(self) -> FleetConflict | None:
        for step in self.steps:
            if step.is_blocked:
                return step.conflict
        return None


class FleetSafetyEngine:
    """Fleet-aware adapter implementing the canonical ``SafetyEngine`` protocol.

    The contract's ``evaluate_motion(route, observed_at_s)`` only receives one
    route, which answers "may this route move?" but not "is the fleet
    consistent?". Rather than widening the protected interface, this adapter is
    constructed with the runtime's live trajectory registry and exposes both:

    * :meth:`evaluate_motion` -- the protocol method, scoped to one route.
    * :meth:`evaluate_fleet` -- the fleet-wide pass the runtime drives.

    There is exactly one ``SafetyEngine`` implementation; consumers only ever see
    the protocol.
    """

    __slots__ = ("_max_resolutions", "_runtime", "_safety_margin_s")

    def __init__(
        self,
        runtime: SimulationRuntime,
        *,
        safety_margin_s: float = DEFAULT_SAFETY_MARGIN_S,
        max_resolutions: int = 16,
    ) -> None:
        if not isfinite(safety_margin_s) or safety_margin_s < 0:
            raise ValueError("safety_margin_s must be a non-negative finite number")
        if max_resolutions < 1:
            raise ValueError("max_resolutions must be positive")
        self._runtime = runtime
        self._safety_margin_s = safety_margin_s
        self._max_resolutions = max_resolutions

    @property
    def safety_margin_s(self) -> float:
        return self._safety_margin_s

    async def evaluate_motion(
        self,
        route: RoutePlan,
        observed_at_s: float,
    ) -> SafetyDecision:
        """Implement the canonical ``SafetyEngine`` protocol for one route."""

        if not isinstance(route, RoutePlan):
            raise TypeError("route must be a RoutePlan contract instance")
        if not isfinite(observed_at_s) or observed_at_s < 0:
            raise ValueError("observed_at_s must be a non-negative finite number")

        trajectories = self._runtime.active_trajectories()
        if route.robot_id not in trajectories:
            return SafetyDecision(
                allowed=True,
                reason=f"robot {route.robot_id} has no committed trajectory to evaluate",
                route=route,
            )

        conflict = find_earliest_conflict(trajectories)
        if conflict is None:
            return SafetyDecision(
                allowed=True,
                reason="no space-time conflict in the fleet",
                route=route,
            )
        if not conflict.involves(route.robot_id):
            return SafetyDecision(
                allowed=True,
                reason=(
                    f"the earliest conflict is between {conflict.robot1} and "
                    f"{conflict.robot2}, which does not involve {route.robot_id}"
                ),
                route=route,
            )

        resolution = try_timing_resolution(
            conflict,
            trajectories,
            self._runtime.priorities(),
            safety_margin_s=self._safety_margin_s,
        )
        if not resolution.resolved or resolution.yield_robot is None:
            return SafetyDecision(
                allowed=False,
                reason=resolution.reason,
                route=route,
                recovery_action=self._runtime.build_blocked_recovery(
                    conflict, observed_at_s, reason=resolution.reason
                ),
            )
        if resolution.yield_robot == route.robot_id:
            return SafetyDecision(
                allowed=False,
                reason=resolution.reason,
                route=route,
                recovery_action=self._runtime.build_yield_recovery(
                    conflict, resolution, observed_at_s
                ),
            )
        return SafetyDecision(
            allowed=True,
            reason=f"route keeps right of way: {resolution.decision.reason}"
            if resolution.decision is not None
            else "route keeps right of way",
            route=route,
        )

    def evaluate_fleet(self, observed_at_s: float) -> SafetyEvaluation:
        """Run one fleet-wide pass, resolving what a timing delay can fix.

        Only the *earliest* conflict is worked on: resolving it rewrites
        timestamps and therefore changes which conflict comes next, so the loop
        is inherently event-driven rather than a per-tick full sweep. It is also
        bounded, so pathological fleets terminate instead of spinning.
        """

        steps: list[SafetyStep] = []
        for _ in range(self._max_resolutions):
            conflict = find_earliest_conflict(self._runtime.active_trajectories())
            if conflict is None:
                return SafetyEvaluation(
                    evaluated_at_s=observed_at_s,
                    steps=tuple(steps),
                    resolved=True,
                    open_conflicts=(),
                    reason=(
                        "fleet is conflict free"
                        if not steps
                        else f"fleet is conflict free after {len(steps)} yield(s)"
                    ),
                )
            resolution = try_timing_resolution(
                conflict,
                self._runtime.active_trajectories(),
                self._runtime.priorities(),
                safety_margin_s=self._safety_margin_s,
            )
            if not resolution.resolved or resolution.yield_robot is None:
                steps.append(
                    SafetyStep(conflict=conflict, outcome="blocked", resolution=resolution)
                )
                return SafetyEvaluation(
                    evaluated_at_s=observed_at_s,
                    steps=tuple(steps),
                    resolved=False,
                    open_conflicts=(),
                    reason=resolution.reason,
                )
            steps.append(
                SafetyStep(conflict=conflict, outcome="yield", resolution=resolution)
            )
            self._runtime.apply_yield(resolution, observed_at_s)

        remaining = find_earliest_conflict(self._runtime.active_trajectories())
        if remaining is not None:
            steps.append(SafetyStep(conflict=remaining, outcome="blocked"))
        return SafetyEvaluation(
            evaluated_at_s=observed_at_s,
            steps=tuple(steps),
            resolved=remaining is None,
            open_conflicts=(),
            reason=(
                f"stopped after {self._max_resolutions} yield attempt(s)"
                if remaining is not None
                else "fleet is conflict free"
            ),
        )


class SimulationRuntime:
    """Authoritative Agent 2 simulation state.

    The composition root drives this object; it never imports Agent 1.
    """

    def __init__(
        self,
        blueprint: FleetBlueprint,
        *,
        event_stream: InMemoryEventStream | None = None,
        battery_policy: BatteryPolicy | None = None,
        communication_timeout_s: float = _DEFAULT_COMMUNICATION_TIMEOUT_S,
        tick_rate_hz: float = _DEFAULT_TICK_RATE_HZ,
        controller_available: bool = True,
    ) -> None:
        if tick_rate_hz <= 0:
            raise ValueError("tick_rate_hz must be positive")
        if communication_timeout_s <= 0:
            raise ValueError("communication_timeout_s must be positive")
        self._world: WorldState = blueprint.world
        self._grid = GridIndex(self._world)
        self._profiles: RobotProfileRegistry = blueprint.profiles
        self._battery = BatteryManager(battery_policy)
        self._communication_timeout_s = communication_timeout_s
        self._clock = SimulationClock()
        self._stream = event_stream or InMemoryEventStream()
        self._planner = AStarPathPlanner(self._profiles)
        self._engine = FleetSafetyEngine(self)
        self._controller_available = controller_available
        self._initial_states = {
            robot.robot_id: RobotState(
                robot=robot,
                profile=self._profiles.get(robot.robot_id),
                current_cell=cell,
            )
            for robot, cell in zip(blueprint.robots, blueprint.start_cells)
        }
        self._tick_rate_hz = tick_rate_hz
        self._robots: dict[str, RobotState] = {}
        self._tasks: dict[str, Task] = {}
        self._conflicts: dict[str, Conflict] = {}
        self._battery_notified: dict[str, str] = {}
        self._reported_deadlocks: set[str] = set()
        self._yield_context: dict[str, YieldContext] = {}
        self._open_conflict_by_pair: dict[tuple[str, str], str] = {}
        self._allocation_latencies_ms: list[float] = []
        self._task_reassignments = 0
        self._detected_deadlocks = 0
        self._revision = 0
        self._safety_dirty = True
        self._reset_state()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def world(self) -> WorldState:
        return self._world

    @property
    def grid(self) -> GridIndex:
        return self._grid

    @property
    def profiles(self) -> RobotProfileRegistry:
        return self._profiles

    @property
    def planner(self) -> AStarPathPlanner:
        return self._planner

    @property
    def safety_engine(self) -> SafetyEngine:
        """The canonical ``SafetyEngine`` protocol view of this runtime."""

        return self._engine

    @property
    def event_stream(self) -> InMemoryEventStream:
        return self._stream

    @property
    def battery_manager(self) -> BatteryManager:
        return self._battery

    @property
    def clock(self) -> SimulationClock:
        return self._clock

    @property
    def now_s(self) -> float:
        return self._clock.now_s

    @property
    def tick_rate_hz(self) -> float:
        return self._tick_rate_hz

    @property
    def communication_timeout_s(self) -> float:
        """Seconds of missed contact before a link is treated as lost."""

        return self._communication_timeout_s

    @property
    def tick_dt_s(self) -> float:
        return 1.0 / self._tick_rate_hz

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def controller_available(self) -> bool:
        return self._controller_available

    def set_controller_available(self, available: bool) -> None:
        """Represent a coordination outage separately from robot execution."""

        self._controller_available = available

    def robot_states(self) -> tuple[RobotState, ...]:
        return tuple(self._robots[robot_id] for robot_id in sorted(self._robots))

    def robots(self) -> tuple[Robot, ...]:
        return tuple(state.robot for state in self.robot_states())

    def robot(self, robot_id: str) -> Robot:
        return self._require_state(robot_id).robot

    def tasks(self) -> tuple[Task, ...]:
        return tuple(self._tasks[task_id] for task_id in sorted(self._tasks))

    def task(self, task_id: str) -> Task:
        return self._tasks[task_id]

    def routes(self) -> tuple[RoutePlan, ...]:
        return tuple(
            state.route for state in self.robot_states() if state.route is not None
        )

    def open_conflicts(self) -> dict[str, Conflict]:
        return {
            conflict_id: self._conflicts[conflict_id]
            for conflict_id in sorted(self._conflicts)
        }

    def _require_state(self, robot_id: str) -> RobotState:
        try:
            return self._robots[robot_id]
        except KeyError:
            raise KeyError(f"unknown robot {robot_id!r}") from None

    # ------------------------------------------------------------------
    # Safety inputs
    # ------------------------------------------------------------------

    def active_trajectories(self) -> dict[str, Trajectory]:
        """Return every trajectory that still claims space.

        A robot whose route has finished keeps its final placement as a
        one-point trajectory that extends forever, because a parked robot is
        still a physical obstacle. Failed robots have their trajectory cleared
        by the fault path and therefore drop out of the analysis, which is a
        documented MVP simplification: a failed robot is treated as removed
        from the coordination world rather than as a static obstacle.
        """

        now_s = self._clock.now_s
        trajectories: dict[str, Trajectory] = {}
        for state in self.robot_states():
            if not state.trajectory:
                continue
            if state.trajectory[-1].timestamp_s <= now_s:
                trajectories[state.robot_id] = (
                    TrajectoryPoint(
                        cell=state.current_cell,
                        timestamp_s=now_s,
                        occupied_cells=footprint_cells(
                            state.current_cell,
                            state.profile.width_cells,
                            state.profile.height_cells,
                        ),
                    ),
                )
                continue
            trajectories[state.robot_id] = state.trajectory
        return trajectories

    def priorities(self) -> dict[str, RobotPriority]:
        """Return the deterministic right-of-way inputs for every robot."""

        now_s = self._clock.now_s
        priorities: dict[str, RobotPriority] = {}
        for state in self.robot_states():
            task = self._tasks.get(state.task_id) if state.task_id else None
            waiting_time = 0.0
            if state.blocked_since_s is not None:
                waiting_time = max(0.0, now_s - state.blocked_since_s)
            priorities[state.robot_id] = RobotPriority(
                robot_id=state.robot_id,
                task_priority=task.priority if task is not None else 1,
                battery_percent=state.robot.battery_percent,
                waiting_time_s=waiting_time,
            )
        return priorities

    def wait_graph(self) -> dict[str, tuple[str, ...]]:
        """Return the wait graph derived from currently blocked robots."""

        edges: dict[str, set[str]] = {}
        for conflict in self._conflicts.values():
            if conflict.status is not ResolutionStatus.OPEN:
                continue
            for robot_id in conflict.robot_ids:
                others = {
                    other for other in conflict.robot_ids if other != robot_id
                }
                if others:
                    edges.setdefault(robot_id, set()).update(others)
        graph: dict[str, tuple[str, ...]] = {}
        for robot_id in sorted(edges):
            state = self._robots.get(robot_id)
            if state is None or state.robot.status is not RobotStatus.BLOCKED:
                continue
            graph[robot_id] = tuple(sorted(edges[robot_id]))
        return graph

    # ------------------------------------------------------------------
    # Task intake: the Agent 1 -> Agent 2 boundary
    # ------------------------------------------------------------------

    def handle_event(
        self, event: EventEnvelope[EventPayload]
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Consume a canonical coordination event.

        This is the only way Agent 1 reaches Agent 2: the composition root
        forwards ``TASK_ASSIGNED`` and ``TASK_REASSIGNED`` and the runtime
        reacts by planning a route for the new owner. Anything that is not a
        recognised coordination event is ignored.
        """

        payload = getattr(event, "payload", None)
        if isinstance(payload, TaskAssignedPayload):
            return self.assign_task(payload.assignment.task_id, payload.assignment.robot_id)
        if isinstance(payload, TaskReassignedPayload):
            self._task_reassignments += 1
            self._detach_task(payload.task_id, payload.previous_robot_id)
            return self.assign_task(payload.task_id, payload.new_robot_id)
        if isinstance(payload, TaskCreatedPayload):
            self._tasks.setdefault(payload.task.task_id, payload.task)
            return ()
        return ()

    def submit_task(self, task: Task) -> tuple[EventEnvelope[EventPayload], ...]:
        """Register a task and publish ``TASK_CREATED``."""

        if not isinstance(task, Task):
            raise TypeError("submit_task requires a Task contract instance")
        if task.task_id in self._tasks:
            raise ValueError(f"task {task.task_id!r} already exists")
        self._tasks[task.task_id] = task
        self._mark_safety_dirty()
        created = self._emit(
            TaskCreatedPayload(task=task),
            self._clock.now_s,
            correlation_id=task.task_id,
        )
        return created + self._commit(self._clock.now_s)

    def set_battery_percent(
        self, robot_id: str, percent: float, at_s: float | None = None
    ) -> None:
        """Set a robot's charge directly, as if it had drained on the way.

        This is a fault-injection surface for the console, in the same family as
        the failure and communication-loss injections. It does not publish
        ``BATTERY_LOW`` itself: the next movement tick observes the new level and
        the normal threshold crossing emits the event, so the coordinator's
        existing battery trigger fires exactly as it would in a real drain.
        """

        if not isfinite(percent) or percent < 0 or percent > 100:
            raise ValueError("percent must be a finite number between 0 and 100")
        state = self._require_state(robot_id)
        when = self._clock.now_s if at_s is None else at_s
        self._robots[robot_id] = replace(
            state,
            robot=state.robot.model_copy(
                update={
                    "battery_percent": percent,
                    "last_updated_at_s": when,
                }
            ),
        )
        # A fresh observation is due, so the crossing is not suppressed as a
        # repeat of one already reported.
        self._battery_notified.pop(robot_id, None)
        self._mark_safety_dirty()

    def require_task(self, task_id: str) -> Task:
        """Return a registered task, or raise ``KeyError``."""

        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id!r}")
        return task

    def release_task_from_robot(self, task_id: str, robot_id: str) -> None:
        """Free one robot from one task, leaving the task itself registered.

        Used when work is migrated away from a robot by hand or by recovery, so
        the robot stops being eligible-for-nothing and becomes bid-eligible
        again.
        """

        state = self._robots.get(robot_id)
        if state is None or state.task_id != task_id:
            return
        self._robots[robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                current_task_id=None,
                workload=0,
                last_updated_at_s=self._clock.now_s,
            ),
            task_id=None,
            route=None,
            trajectory=(),
            cells_travelled=0,
            blocked_since_s=None,
            resume_after_s=None,
        )
        self._mark_safety_dirty()

    def release_open_assignments(self) -> tuple[EventEnvelope[EventPayload], ...]:
        """Return every unfinished task to ``pending`` and clear its route.

        A task that already completed, or that was cancelled, is committed
        history and is left untouched. Everything else goes back to the pool:
        the owner loses its route and trajectory, so a fresh negotiation round
        can bind it to a different robot. This publishes no event of its own --
        it is a precondition for another round, and the events that follow
        describe the new owner.
        """

        released: list[EventEnvelope[EventPayload]] = []
        for task_id, task in list(self._tasks.items()):
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                continue
            self._tasks[task_id] = task.model_copy(
                update={
                    "status": TaskStatus.PENDING,
                    "assigned_robot_id": None,
                }
            )
            for robot_id in list(self._robots):
                state = self._robots[robot_id]
                if state.task_id != task_id:
                    continue
                # The canonical `Robot` has to be released too: a robot is only
                # eligible for a new bid when it is idle and holds no task.
                if state.robot.current_task_id == task_id:
                    self._robots[robot_id] = replace(
                        state,
                        robot=state.robot.model_copy(
                            update={
                                "current_task_id": None,
                                "status": RobotStatus.IDLE,
                                "workload": 0,
                            }
                        ),
                        task_id=None,
                        route=None,
                        trajectory=(),
                        cells_travelled=0,
                        resume_after_s=None,
                    )
                    continue
                self._robots[robot_id] = replace(
                    state,
                    task_id=None,
                    route=None,
                    trajectory=(),
                    cells_travelled=0,
                    resume_after_s=None,
                )
        self._mark_safety_dirty()
        self._commit(self._clock.now_s)
        return tuple(released)

    def assign_task(
        self,
        task_id: str,
        robot_id: str,
        *,
        commit: bool = True,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Bind a task to a robot and plan its route.

        Publishes ``ROUTE_REQUESTED`` then ``ROUTE_PLANNED`` (or
        ``ROUTE_REPLANNED`` when the pair already had a route). If no route
        exists the plan is published as ``INVALID`` and the task is reported as
        blocked instead of silently stalling.

        ``commit=False`` binds and plans without running a tick. A caller that is
        already inside a commit -- finishing a retreat, for instance -- uses it
        so movement for that tick is not evaluated twice against a half-updated
        fleet.
        """

        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id!r}")
        state = self._require_state(robot_id)
        if state.robot.status is RobotStatus.FAILED:
            raise ValueError(
                f"robot {robot_id!r} is failed and cannot be assigned work"
            )

        requested = self._emit(
            RouteRequestedPayload(
                task_id=task_id,
                robot_id=robot_id,
                origin=state.robot.position,
                target=task.target,
            ),
            self._clock.now_s,
            correlation_id=task_id,
        )

        previous_route = state.route
        route = self._planner.plan_sync(
            robot_id,
            task_id,
            state.robot.position,
            task.target,
            self._world,
            self._clock.now_s,
        )
        trajectory = self._trajectory_for(state, route)
        # Bind the task to the robot immediately, not on the first movement:
        # Agent 1's eligibility rules exclude a robot that already holds a
        # current task, so leaving this unset would let the same robot win a
        # second task before it has moved.
        bound_robot = (
            evolve_robot(
                state.robot,
                status=RobotStatus.ACTIVE if trajectory else state.robot.status,
                current_task_id=task_id if trajectory else None,
                workload=1 if trajectory else 0,
                last_updated_at_s=self._clock.now_s,
            )
            if trajectory
            else state.robot
        )
        self._robots[robot_id] = replace(
            state,
            robot=bound_robot,
            route=route,
            trajectory=trajectory,
            task_id=task_id if trajectory else None,
            task_started_at_s=self._clock.now_s if trajectory else None,
            blocked_since_s=None,
            resume_after_s=None,
        )
        self._tasks[task_id] = evolve_task(
            task, assigned_robot_id=robot_id, status=TaskStatus.ASSIGNED
        )
        self._mark_safety_dirty()

        if not trajectory:
            # No route exists. The task is reported as blocked rather than left
            # to stall silently. A `Conflict` needs at least two robots, so this
            # is surfaced through task and robot status instead of the conflict
            # list, keeping `open_conflicts` an accurate count.
            self._mark_blocked(robot_id, self._clock.now_s)
            self._tasks[task_id] = evolve_task(
                self._tasks[task_id], status=TaskStatus.BLOCKED
            )
            if not commit:
                return requested
            return requested + self._commit(self._clock.now_s)

        if previous_route is None:
            planned = self._emit(
                RoutePlannedPayload(route=route),
                self._clock.now_s,
                correlation_id=task_id,
            )
        else:
            planned = self._emit(
                RouteReplannedPayload(
                    route=route, reason="task ownership changed, route replanned"
                ),
                self._clock.now_s,
                correlation_id=task_id,
            )
        return requested + planned + self._commit(self._clock.now_s)

    def _detach_task(self, task_id: str, robot_id: str) -> None:
        state = self._require_state(robot_id)
        if state.task_id == task_id:
            self._robots[robot_id] = replace(
                state,
                robot=evolve_robot(
                    state.robot,
                    status=RobotStatus.IDLE,
                    current_task_id=None,
                    workload=0,
                    last_updated_at_s=self._clock.now_s,
                ),
                route=None,
                trajectory=(),
                task_id=None,
                task_started_at_s=None,
            )
        task = self._tasks.get(task_id)
        if task is not None and task.assigned_robot_id == robot_id:
            self._tasks[task_id] = evolve_task(
                task, assigned_robot_id=None, status=TaskStatus.RECOVERY
            )

    # ------------------------------------------------------------------
    # Trajectory construction
    # ------------------------------------------------------------------

    def _trajectory_for(self, state: RobotState, route: RoutePlan) -> Trajectory:
        """Rebuild the timed trajectory that matches a planned route exactly.

        The path comes from the route's own waypoints, so a trajectory can never
        describe a different path than the ``RoutePlan`` the dashboard shows.
        """

        if route.status is RouteStatus.INVALID:
            return ()
        width = state.profile.width_cells
        height = state.profile.height_cells
        cells = tuple(
            self._grid.cell_for_position(waypoint) for waypoint in route.waypoints
        )
        path: tuple[PathStep, ...] = tuple(
            PathStep(
                cell=cell,
                occupied_cells=footprint_cells(cell, width, height),
            )
            for cell in cells
        )
        return create_trajectory(
            path,
            state.profile.speed_mps,
            self._grid.cell_size_m,
            self._clock.now_s,
        )

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def step(self, delta_s: float | None = None) -> tuple[EventEnvelope[EventPayload], ...]:
        """Advance one simulation tick and return the events it produced.

        ``delta_s`` defaults to one fixed tick and is scaled by the speed
        multiplier. An empty result means the tick changed no observable state.
        """

        applied = self._clock.advance(self.tick_dt_s if delta_s is None else delta_s)
        if applied == 0.0:
            return ()
        return self._commit(self._clock.now_s)

    def run_ticks(self, ticks: int) -> tuple[EventEnvelope[EventPayload], ...]:
        """Advance ``ticks`` fixed simulation ticks and return all events."""

        if ticks < 0:
            raise ValueError("ticks must not be negative")
        events: list[EventEnvelope[EventPayload]] = []
        for _ in range(ticks):
            events.extend(self.step())
        return tuple(events)

    def _commit(self, observed_at_s: float) -> tuple[EventEnvelope[EventPayload], ...]:
        events: list[EventEnvelope[EventPayload]] = []
        events.extend(self._move_robots(observed_at_s))
        events.extend(self._update_battery(observed_at_s))
        events.extend(self._finish_retreats(observed_at_s))
        self._close_finished_conflicts()
        if self._safety_dirty:
            events.extend(self.evaluate_safety(observed_at_s))
            events.extend(self._check_deadlock(observed_at_s))
            self._promote_planned_routes()
        if events:
            # Revision only advances when a revision was actually published, so
            # ``SimulationSnapshot``'s ``last_event_sequence >= revision``
            # invariant holds by construction.
            self._revision += 1
        return tuple(events)

    def _promote_planned_routes(self) -> None:
        """Mark safety-approved routes as active once the fleet is consistent."""

        for state in self.robot_states():
            route = state.route
            if route is None or route.status is not RouteStatus.PROPOSED:
                continue
            self._robots[state.robot_id] = replace(
                state,
                route=route.model_copy(update={"status": RouteStatus.ACTIVE}),
            )

    def _move_robots(self, observed_at_s: float) -> list[EventEnvelope[EventPayload]]:
        events: list[EventEnvelope[EventPayload]] = []
        for state in self.robot_states():
            if not state.trajectory or state.is_parked:
                continue
            if self._is_held(state, observed_at_s):
                # The safety layer is holding this robot, so it does not move at
                # all. Without this a blocked robot kept consuming its original
                # trajectory timestamps and drove straight through whatever it
                # was blocked by, while only its status said otherwise.
                self._hold_state(state, observed_at_s)
                continue
            point = point_at_time(state.trajectory, observed_at_s)
            if point is None or point.cell == state.current_cell:
                continue
            if not self._grid.footprint_is_free(
                point.cell, state.profile.width_cells, state.profile.height_cells
            ):
                events.extend(self._fail_unsafe_placement(state, point.cell, observed_at_s))
                continue
            self._advance_state(state, point, observed_at_s)
        events.extend(self._complete_arrivals(observed_at_s))
        return events

    @staticmethod
    def _is_held(state: RobotState, observed_at_s: float) -> bool:
        """Whether the safety layer is currently holding this robot still.

        A hold with no release time lasts until the conflict is resolved; a hold
        with one lasts until that time.
        """

        if state.blocked_since_s is None:
            return False
        if state.resume_after_s is None:
            return True
        return observed_at_s < state.resume_after_s

    def _hold_state(self, state: RobotState, observed_at_s: float) -> None:
        """Keep a held robot exactly where it is, and say so."""

        if state.robot.status is RobotStatus.FAILED:
            return
        self._robots[state.robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                status=RobotStatus.BLOCKED,
                last_updated_at_s=observed_at_s,
            ),
        )

    def _release_hold(self, robot_id: str, observed_at_s: float) -> None:
        """End a hold and re-time the rest of the route from where it stopped.

        The robot stopped at ``current_cell``, so the remaining placements are
        pushed forward by the length of the hold and a stationary point is put
        at the cell it waited on. That makes it resume from a standstill instead
        of teleporting to wherever the original timestamps now point.
        """

        state = self._require_state(robot_id)
        if state.blocked_since_s is None and state.resume_after_s is None:
            return
        remaining = [point for point in state.trajectory if point.cell != state.current_cell]
        held_for = max(0.0, observed_at_s - (state.blocked_since_s or observed_at_s))
        if not remaining or held_for <= 0.0:
            self._robots[robot_id] = replace(
                state, blocked_since_s=None, resume_after_s=None
            )
            return
        step = self._nominal_step(state)
        first = remaining[0]
        delay = (observed_at_s + step) - first.timestamp_s
        held = TrajectoryPoint(
            cell=state.current_cell,
            timestamp_s=observed_at_s,
            occupied_cells=self._grid.footprint_cells(
                state.current_cell,
                state.profile.width_cells,
                state.profile.height_cells,
            ),
        )
        self._robots[robot_id] = replace(
            state,
            trajectory=(
                held,
                *(
                    replace(point, timestamp_s=point.timestamp_s + delay)
                    for point in remaining
                ),
            ),
            blocked_since_s=None,
            resume_after_s=None,
        )

    @staticmethod
    def _nominal_step(state: RobotState) -> float:
        """The time one cell normally takes, used when re-timing a route."""

        if len(state.trajectory) >= 2:
            gap = state.trajectory[1].timestamp_s - state.trajectory[0].timestamp_s
            if gap > 0:
                return gap
        return _DEFAULT_TICK_S

    def _advance_state(
        self,
        state: RobotState,
        point: TrajectoryPoint,
        observed_at_s: float,
    ) -> None:
        resumed = (
            state.resume_after_s is not None
            and point.timestamp_s >= state.resume_after_s
        )
        still_blocked = state.blocked_since_s is not None and not resumed
        self._robots[state.robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                position=self._grid.position_for_cell(point.cell),
                status=RobotStatus.BLOCKED if still_blocked else RobotStatus.ACTIVE,
                workload=1 if state.task_id else 0,
                current_task_id=state.task_id,
                last_updated_at_s=observed_at_s,
            ),
            current_cell=point.cell,
            cells_travelled=state.cells_travelled + 1,
            blocked_since_s=None if resumed else state.blocked_since_s,
            resume_after_s=None if resumed else state.resume_after_s,
        )

    def _fail_unsafe_placement(
        self,
        state: RobotState,
        cell: Cell,
        observed_at_s: float,
    ) -> list[EventEnvelope[EventPayload]]:
        """Fail a robot whose trajectory reached an illegal placement.

        A* never emits such a placement, so this is an invariant guard against an
        upstream bug rather than a normal simulation condition. Failing loudly is
        better than letting a robot sit inside an obstacle.
        """

        failure = FailureInfo(
            kind=FailureKind.OTHER,
            code="unsafe-placement",
            detected_at_s=observed_at_s,
            detail=f"trajectory entered blocked or out-of-bounds cell {cell}",
        )
        self._robots[state.robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                status=RobotStatus.FAILED,
                failure=failure,
                communication_state=CommunicationState.LOST,
                last_updated_at_s=observed_at_s,
            ),
            route=None,
            trajectory=(),
            task_id=None,
            task_started_at_s=None,
            blocked_since_s=None,
            resume_after_s=None,
        )
        self._mark_safety_dirty()
        return list(
            self._emit(
                RobotFailedPayload(robot_id=state.robot_id, failure=failure),
                observed_at_s,
                correlation_id=state.task_id or state.robot_id,
            )
        )

    def _complete_arrivals(
        self, observed_at_s: float
    ) -> list[EventEnvelope[EventPayload]]:
        events: list[EventEnvelope[EventPayload]] = []
        for state in self.robot_states():
            if state.is_parked or not state.task_id:
                continue
            if state.trajectory[-1].timestamp_s > observed_at_s:
                continue
            task = self._tasks.get(state.task_id)
            if task is None or task.status is TaskStatus.COMPLETED:
                continue
            started_at_s = (
                state.task_started_at_s
                if state.task_started_at_s is not None
                else observed_at_s
            )
            self._tasks[task.task_id] = evolve_task(task, status=TaskStatus.COMPLETED)
            self._robots[state.robot_id] = replace(
                state,
                robot=evolve_robot(
                    state.robot,
                    status=RobotStatus.IDLE,
                    current_task_id=None,
                    workload=0,
                    last_updated_at_s=observed_at_s,
                ),
                route=state.route.model_copy(update={"status": RouteStatus.COMPLETED})
                if state.route is not None
                else None,
                task_id=None,
                task_started_at_s=None,
                blocked_since_s=None,
                resume_after_s=None,
            )
            self._mark_safety_dirty()
            events.extend(
                self._emit(
                    TaskCompletedPayload(
                        task_id=task.task_id,
                        robot_id=state.robot_id,
                        started_at_s=started_at_s,
                        completed_at_s=observed_at_s,
                    ),
                    observed_at_s,
                    correlation_id=task.task_id,
                )
            )
        return events

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------

    def _update_battery(self, observed_at_s: float) -> list[EventEnvelope[EventPayload]]:
        events: list[EventEnvelope[EventPayload]] = []
        for state in self.robot_states():
            # Charge only the cells travelled since the last update, otherwise
            # re-applying the cumulative distance would drain quadratically.
            new_cells = state.cells_travelled
            if new_cells <= state.charged_cells:
                continue
            consumed = self._battery.consumption_for(
                float(new_cells - state.charged_cells), state.profile
            )
            robot = self._battery.apply_consumption(
                state.robot, consumed, observed_at_s
            )
            updated = replace(state, robot=robot, charged_cells=new_cells)
            self._robots[state.robot_id] = updated
            events.extend(self._battery_events(updated, observed_at_s))
        return events

    def _battery_observation(self, state: RobotState) -> BatteryObservation:
        return self._battery.observe(
            state.robot,
            state.profile,
            remaining_cells=self._remaining_cells(state),
            cell_size_m=self._grid.cell_size_m,
        )

    def _remaining_cells(self, state: RobotState) -> int:
        if not state.trajectory:
            return 0
        now_s = self._clock.now_s
        ahead = sum(
            1 for point in state.trajectory if point.timestamp_s > now_s
        )
        return max(0, ahead - 1)

    def _battery_events(
        self,
        state: RobotState,
        observed_at_s: float,
    ) -> list[EventEnvelope[EventPayload]]:
        """Emit ``BATTERY_LOW`` once per threshold crossing, not every tick."""

        observation = self._battery_observation(state)
        if not observation.is_low:
            self._battery_notified.pop(state.robot_id, None)
            return []
        level = "critical" if observation.is_critical else "low"
        if self._battery_notified.get(state.robot_id) == level:
            return []
        self._battery_notified[state.robot_id] = level
        return list(
            self._emit(
                BatteryLowPayload(
                    robot_id=state.robot_id,
                    battery_percent=observation.battery_percent,
                    threshold_percent=observation.threshold_percent,
                    estimated_range_m=observation.estimated_range_m,
                ),
                observed_at_s,
                correlation_id=state.task_id or state.robot_id,
            )
        )

    # ------------------------------------------------------------------
    # Faults
    # ------------------------------------------------------------------

    def inject_failure(
        self,
        robot_id: str,
        failure: FailureInfo,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Apply an injected robot failure and publish ``ROBOT_FAILED``."""

        state = self._require_state(robot_id)
        at_s = max(failure.detected_at_s, self._clock.now_s)
        self._robots[robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                status=RobotStatus.FAILED,
                failure=failure.model_copy(update={"detected_at_s": at_s}),
                communication_state=CommunicationState.LOST,
                last_updated_at_s=at_s,
            ),
            route=None,
            trajectory=(),
            task_id=None,
            task_started_at_s=None,
            blocked_since_s=None,
            resume_after_s=None,
        )
        self._battery_notified.pop(robot_id, None)
        self._mark_safety_dirty()
        events = list(
            self._emit(
                RobotFailedPayload(
                    robot_id=robot_id,
                    failure=self._robots[robot_id].robot.failure or failure,
                ),
                self._clock.now_s,
                correlation_id=state.task_id or robot_id,
            )
        )
        return tuple(events) + self._commit(self._clock.now_s)

    def inject_communication_loss(
        self,
        robot_id: str,
        *,
        timeout_s: float | None = None,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Apply an injected communication loss and publish ``COMMUNICATION_LOST``.

        The robot stays physically present: position, status, task, and battery
        are untouched, only coordination is lost. Whether the work is moved is
        Agent 1's decision, reached through this event.
        """

        state = self._require_state(robot_id)
        if state.robot.status is RobotStatus.FAILED:
            raise ValueError(
                "a failed robot is not a communication-loss case; restore it first"
            )
        effective_timeout = timeout_s or self._communication_timeout_s
        robot = mark_communication_lost(state.robot, observed_at_s=self._clock.now_s)
        self._robots[robot_id] = replace(
            state, robot=robot, last_contact_at_s=self._clock.now_s
        )
        self._mark_safety_dirty()
        events = list(
            self._emit(
                communication_lost_payload(
                    robot,
                    last_contact_at_s=self._clock.now_s,
                    timeout_s=effective_timeout,
                ),
                self._clock.now_s,
                correlation_id=state.task_id or robot_id,
            )
        )
        return tuple(events) + self._commit(self._clock.now_s)

    def restore(self, robot_id: str) -> tuple[EventEnvelope[EventPayload], ...]:
        """Restore a failed robot or a lost communication link."""

        state = self._require_state(robot_id)
        if state.robot.status is RobotStatus.FAILED or state.robot.failure is not None:
            robot = restore_robot(state.robot, restored_at_s=self._clock.now_s)
        elif state.robot.communication_state is not CommunicationState.ONLINE:
            robot = restore_communication(state.robot, observed_at_s=self._clock.now_s)
        else:
            return ()
        self._robots[robot_id] = replace(state, robot=robot)
        self._battery_notified.pop(robot_id, None)
        self._mark_safety_dirty()
        return self._commit(self._clock.now_s)

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def _mark_safety_dirty(self) -> None:
        self._safety_dirty = True

    def mark_safety_dirty(self) -> None:
        """Request a fleet safety re-evaluation on the next commit."""

        self._mark_safety_dirty()

    def evaluate_safety(
        self,
        observed_at_s: float | None = None,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Run one fleet safety pass now and publish its canonical events."""

        at_s = self._clock.now_s if observed_at_s is None else observed_at_s
        evaluation = self._engine.evaluate_fleet(at_s)
        self._safety_dirty = not evaluation.resolved
        events: list[EventEnvelope[EventPayload]] = []
        for step in evaluation.steps:
            record = self.record_conflict(step.conflict, at_s)
            events.extend(
                self._emit(
                    ConflictDetectedPayload(conflict=record),
                    at_s,
                    correlation_id=record.conflict_id,
                )
            )
            if step.is_blocked:
                for robot_id in step.conflict.involved_robot_ids:
                    self._mark_blocked(robot_id, at_s)
                    self._mark_safety_dirty()
                continue
            if step.resolution is None or step.resolution.yield_robot is None:
                continue
            action = self.build_yield_recovery(step.conflict, step.resolution, at_s)
            self._record_yield_context(
                step.conflict, step.resolution, action, at_s
            )
            events.extend(
                self._emit(
                    RecoveryStartedPayload(action=action),
                    at_s,
                    correlation_id=action.action_id,
                )
            )
            self._resolve_conflict(record)
        return tuple(events)

    def _record_yield_context(
        self,
        conflict: FleetConflict,
        resolution,
        action: RecoveryAction,
        observed_at_s: float,
    ) -> None:
        """Remember why a robot is yielding, and until when."""

        robot_id = resolution.yield_robot
        if robot_id is None:
            return
        others = [
            other for other in conflict.involved_robot_ids if other != robot_id
        ]
        state = self._require_state(robot_id)
        self._yield_context[robot_id] = YieldContext(
            robot_id=robot_id,
            yields_to_robot_id=others[0] if others else "",
            conflict_id=action.action_id,
            started_at_s=observed_at_s,
            release_at_s=(
                state.resume_after_s
                if state.resume_after_s is not None
                else observed_at_s
            ),
            reason=resolution.reason[:500],
        )

    def yield_context(self) -> dict[str, YieldContext]:
        """Return the current yield reasons, dropping expired holds.

        A hold is only interesting until the robot is released again, so an
        expired entry is removed instead of being reported as a stale reason.
        """

        now_s = self._clock.now_s
        expired = [
            robot_id
            for robot_id, context in self._yield_context.items()
            if context.release_at_s <= now_s
        ]
        for robot_id in expired:
            del self._yield_context[robot_id]
        return {
            robot_id: self._yield_context[robot_id]
            for robot_id in sorted(self._yield_context)
        }

    def apply_yield(self, resolution: YieldResolution, observed_at_s: float) -> None:
        """Commit a fleet-validated delay trajectory to a yielding robot."""

        robot_id = resolution.yield_robot
        if robot_id is None or resolution.trajectory is None:
            return
        state = self._require_state(robot_id)
        if not state.trajectory:
            return
        release_s = self._delay_release_time(state, resolution)
        self._robots[robot_id] = replace(
            state,
            trajectory=resolution.trajectory,
            blocked_since_s=observed_at_s,
            resume_after_s=release_s,
        )
        self._mark_safety_dirty()

    def _delay_release_time(
        self,
        state: RobotState,
        resolution: YieldResolution,
    ) -> float:
        """Return when the delayed robot is allowed to restart moving."""

        if resolution.trajectory is None or not state.trajectory:
            return self._clock.now_s
        original_start = state.trajectory[0].timestamp_s
        for point in resolution.trajectory:
            if point.cell != state.current_cell:
                return point.timestamp_s
        if resolution.trajectory[0].timestamp_s > original_start:
            return resolution.trajectory[0].timestamp_s
        return self._clock.now_s

    def _mark_blocked(self, robot_id: str, observed_at_s: float) -> None:
        state = self._require_state(robot_id)
        if state.robot.status is RobotStatus.FAILED:
            return
        self._robots[robot_id] = replace(
            state,
            robot=evolve_robot(
                state.robot,
                status=RobotStatus.BLOCKED,
                last_updated_at_s=observed_at_s,
            ),
            blocked_since_s=(
                state.blocked_since_s
                if state.blocked_since_s is not None
                else observed_at_s
            ),
        )
        task = self._tasks.get(state.task_id) if state.task_id else None
        if task is not None and task.status is not TaskStatus.BLOCKED:
            self._tasks[task.task_id] = evolve_task(task, status=TaskStatus.BLOCKED)

    def conflict_id_for(self, conflict: FleetConflict) -> str:
        return _derived_id(
            "conflict",
            "fleet",
            min(conflict.robot1, conflict.robot2),
            max(conflict.robot1, conflict.robot2),
            f"{conflict.collision.start_time_s:.6f}",
        )

    def record_conflict(self, conflict: FleetConflict, detected_at_s: float) -> Conflict:
        """Create or update the canonical ``Conflict`` record for a pair.

        A pair holds at most one *open* conflict record. Safety is re-evaluated
        whenever a trajectory changes, and every delay shifts the timestamps, so
        keying on the conflict time would create a new record on every pass and
        make ``open_conflicts`` grow without bound. The existing record is
        refreshed instead, which keeps the metric an accurate count of genuinely
        stuck pairs.
        """

        pair = (min(conflict.robot1, conflict.robot2), max(conflict.robot1, conflict.robot2))
        open_id = self._open_conflict_by_pair.get(pair)
        if open_id is not None and open_id in self._conflicts:
            existing = self._conflicts[open_id]
            refreshed = existing.model_copy(
                update={
                    "position": self._conflict_position(conflict),
                    "task_ids": self._task_ids_for(conflict.involved_robot_ids),
                    "detected_at_s": detected_at_s,
                }
            )
            self._conflicts[open_id] = refreshed
            return refreshed

        conflict_id = self.conflict_id_for(conflict)
        record = Conflict(
            conflict_id=conflict_id,
            kind=ConflictKind.RIGHT_OF_WAY,
            severity=ConflictSeverity.WARNING,
            robot_ids=pair,
            task_ids=self._task_ids_for(conflict.involved_robot_ids),
            position=self._conflict_position(conflict),
            status=ResolutionStatus.OPEN,
            detected_at_s=detected_at_s,
        )
        self._conflicts[conflict_id] = record
        self._open_conflict_by_pair[pair] = conflict_id
        return record

    def _task_ids_for(self, robot_ids: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    task_id
                    for robot_id in robot_ids
                    if (task_id := self._robots[robot_id].task_id) is not None
                }
            )
        )

    def _conflict_position(self, conflict: FleetConflict) -> Position2D:
        return self._grid.position_for_cell(conflict.cells[0])

    def _resolve_conflict(self, record: Conflict) -> None:
        stored = self._conflicts.get(record.conflict_id)
        if stored is None or stored.status is ResolutionStatus.RESOLVED:
            return
        self._conflicts[record.conflict_id] = stored.model_copy(
            update={"status": ResolutionStatus.RESOLVED}
        )
        pair = (
            min(record.robot_ids),
            max(record.robot_ids),
        )
        if self._open_conflict_by_pair.get(pair) == record.conflict_id:
            del self._open_conflict_by_pair[pair]
        for robot_id in record.robot_ids:
            state = self._require_state(robot_id)
            if state.blocked_since_s is not None or state.resume_after_s is not None:
                # The way is clear, so the held robot is released and resumes
                # from the cell it waited on.
                self._release_hold(robot_id, self._clock.now_s)

    def build_yield_recovery(
        self,
        conflict: FleetConflict,
        resolution: YieldResolution,
        started_at_s: float,
    ) -> RecoveryAction:
        return build_recovery_action(
            action_id=_derived_id(
                "recovery",
                "yield",
                resolution.yield_robot or "",
                f"{conflict.collision.start_time_s:.6f}",
            ),
            action_type=RecoveryActionType.YIELD,
            target_robot_ids=(resolution.yield_robot or "",),
            affected_task_ids=self._task_ids_for(conflict.involved_robot_ids),
            reason=resolution.reason[:500],
            started_at_s=started_at_s,
        )

    def build_blocked_recovery(
        self,
        conflict: FleetConflict,
        started_at_s: float,
        *,
        reason: str,
    ) -> RecoveryAction:
        return build_recovery_action(
            action_id=_derived_id(
                "recovery",
                "blocked",
                min(conflict.robot1, conflict.robot2),
                max(conflict.robot1, conflict.robot2),
            ),
            action_type=RecoveryActionType.REPLAN,
            target_robot_ids=tuple(sorted(conflict.involved_robot_ids)),
            affected_task_ids=self._task_ids_for(conflict.involved_robot_ids),
            reason=reason[:500],
            started_at_s=started_at_s,
        )

    def _check_deadlock(
        self, observed_at_s: float
    ) -> list[EventEnvelope[EventPayload]]:
        """Detect cyclic waiting and propose a yield for each new cycle.

        The report is honest about what the MVP can do: the wait graph is
        detected and a ``YIELD`` recovery action is published, but the
        underlying space-time conflict stays **open** because resolving a head-on
        needs the holding-position or replanning strategies, which are still
        experimental. The involved robots therefore stay visibly ``BLOCKED`` and
        the conflict keeps counting towards ``open_conflicts``.

        Each distinct cycle is reported once, so repeated safety passes do not
        spam the event stream.
        """

        wait_graph = self.wait_graph()
        cycles = find_deadlock_cycles(wait_graph)
        if not cycles:
            return []

        events: list[EventEnvelope[EventPayload]] = []
        for cycle in cycles:
            report = build_deadlock_report(
                deadlock_id=_derived_id("deadlock", *cycle.robot_ids),
                cycle=cycle,
                blocked_task_ids=self._task_ids_for(cycle.robot_ids),
                detected_at_s=observed_at_s,
            )
            already_reported = report.deadlock_id in self._reported_deadlocks
            if not already_reported:
                self._reported_deadlocks.add(report.deadlock_id)
                self._detected_deadlocks += 1
                events.extend(
                    self._emit(
                        DeadlockDetectedPayload(report=report),
                        observed_at_s,
                        correlation_id=report.deadlock_id,
                    )
                )
            recovery = recover_deadlock(wait_graph, self.priorities())
            if not recovery.recovered or recovery.robot_id is None:
                # Nothing to try. Retrying every pass would spin, so a cycle
                # with no usable victim is left for a later pass to reconsider.
                if already_reported:
                    continue
                events.extend(
                    self._emit(
                        DeadlockDetectedPayload(report=report),
                        observed_at_s,
                        correlation_id=report.deadlock_id,
                    )
                )
                continue
            if not already_reported:
                events.extend(
                    self._emit(
                        RecoveryStartedPayload(
                            action=build_recovery_action(
                                action_id=_derived_id(
                                    "recovery", "deadlock", recovery.robot_id
                                ),
                                action_type=RecoveryActionType.YIELD,
                                target_robot_ids=(recovery.robot_id,),
                                affected_task_ids=report.blocked_task_ids,
                                reason=recovery.reason[:500],
                                started_at_s=observed_at_s,
                            )
                        ),
                        observed_at_s,
                        correlation_id=report.deadlock_id,
                    )
                )
            # Apply the recovery. Publishing the action on its own left the
            # cycle in place forever: the victim kept waiting and the conflict
            # stayed open, so a detected deadlock never actually cleared.
            # The canonical `RECOVERY_STARTED` event above is the report; the
            # state change below is the fix, which needs no new event type.
            #
            # This runs again for a cycle that was already reported, because the
            # first attempt can fail -- a retreat route that the planner rejects,
            # for instance. Without the retry the fleet reported a resolved
            # deadlock while the robots stayed exactly where they were.
            self._apply_deadlock_recovery(recovery.robot_id, observed_at_s)
        return events

    def _close_finished_conflicts(self) -> None:
        """Resolve conflicts whose robots have nothing left to do.

        A robot that has finished its route and holds no task is parked. A
        conflict that still names it can never be resolved by movement, so
        leaving it open would keep counting towards ``open_conflicts`` and keep
        a finished run looking deadlocked.
        """

        for conflict in list(self._conflicts.values()):
            if conflict.status is not ResolutionStatus.OPEN:
                continue
            involved = [
                self._robots[robot_id] for robot_id in conflict.robot_ids if robot_id in self._robots
            ]
            if not involved or len(involved) != len(conflict.robot_ids):
                continue
            # A conflict is unresolvable by movement once none of the robots in
            # it can still move: they have either finished their route or have
            # nothing left to drive. Leaving it open would keep a finished run
            # looking deadlocked forever.
            if any(not state.is_parked and state.trajectory for state in involved):
                continue
            self._resolve_conflict(conflict)

    def _apply_deadlock_recovery(self, robot_id: str, observed_at_s: float) -> None:
        """Break the cycle by backing one robot out of the way.

        Releasing the chosen robot is not enough on its own. In a one-cell
        passage the released robot is still standing in the only cell both
        robots want, so the next safety pass finds the same conflict and the
        cycle never clears. The chosen robot is therefore given a real retreat
        route to the nearest place wide enough for two robots to pass, and its
        own task route is replanned once it is there. The other robots in the
        cycle are released immediately, because the space they were waiting for
        is about to be free.
        """

        self._retreat_robot(robot_id, observed_at_s)
        for conflict in list(self._conflicts.values()):
            if conflict.status is not ResolutionStatus.OPEN:
                continue
            if robot_id in conflict.robot_ids:
                self._resolve_conflict(conflict)
        for other_id, state in list(self._robots.items()):
            if other_id == robot_id or state.blocked_since_s is None:
                continue
            still_conflicted = any(
                conflict_id in self._conflicts
                and self._conflicts[conflict_id].status is ResolutionStatus.OPEN
                and other_id in self._conflicts[conflict_id].robot_ids
                for conflict_id in self._open_conflict_by_pair.values()
            )
            if not still_conflicted:
                self._release_hold(other_id, observed_at_s)
        self._mark_safety_dirty()

    def _retreat_robot(self, robot_id: str, observed_at_s: float) -> None:
        """Plan and commit a real move-aside for a robot that is in the way."""

        state = self._require_state(robot_id)
        if state.retreating_since_s is not None:
            return
        # Several places are tried, not just the closest one: the nearest cell
        # with room to pass is often unreachable because something else is
        # standing there, and accepting the first plan failure left the robot
        # stuck with the deadlock reported as resolved.
        for spot in self._passing_places(state):
            route = self._planner.plan_sync(
                robot_id,
                state.task_id or f"retreat-{robot_id}",
                self._grid.position_for_cell(state.current_cell),
                self._grid.position_for_cell(spot),
                self._world,
                observed_at_s,
            )
            if route.status is RouteStatus.INVALID:
                continue
            trajectory = self._trajectory_for(state, route)
            if not trajectory:
                continue
            self._robots[robot_id] = replace(
                state,
                route=route,
                trajectory=trajectory,
                task_id=state.task_id,
                blocked_since_s=None,
                resume_after_s=None,
                retreating_since_s=observed_at_s,
                robot=evolve_robot(
                    state.robot,
                    status=RobotStatus.ACTIVE,
                    last_updated_at_s=observed_at_s,
                ),
            )
            return
        # Nowhere to go: keep the existing hold rather than inventing a route
        # the planner cannot produce. The next safety pass will try again.
        self._release_hold(robot_id, observed_at_s)

    def _passing_places(self, state: RobotState) -> tuple[Cell, ...]:
        """Free cells near the robot that two robots can share, nearest first."""

        width = state.profile.width_cells
        height = state.profile.height_cells
        origin = state.current_cell
        occupied = {
            other.current_cell for other in self._robots.values() if other.robot_id != state.robot_id
        }
        found: list[Cell] = []
        for radius in range(1, 14):
            for x in range(
                max(0, origin[0] - radius), min(self._world.columns, origin[0] + radius + 1)
            ):
                for y in range(
                    max(0, origin[1] - radius), min(self._world.rows, origin[1] + radius + 1)
                ):
                    if max(abs(x - origin[0]), abs(y - origin[1])) != radius:
                        continue
                    if (x, y) in occupied:
                        continue
                    if not self._grid.footprint_is_free((x, y), width, height):
                        continue
                    if not self._has_passing_room((x, y), width, height):
                        continue
                    found.append((x, y))
            if len(found) >= 4:
                break
        return tuple(found)

    def _has_passing_room(self, cell: Cell, width: int, height: int) -> bool:
        """Whether any orthogonal neighbour is free for this robot's footprint."""

        x, y = cell
        for offset_x, offset_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbour = (x + offset_x, y + offset_y)
            if not (
                0 <= neighbour[0] < self._world.columns
                and 0 <= neighbour[1] < self._world.rows
            ):
                continue
            if self._grid.footprint_is_free(neighbour, width, height):
                return True
        return False

    def _finish_retreats(self, observed_at_s: float) -> list[EventEnvelope[EventPayload]]:
        """Hand a robot that has backed off its own task route again."""

        events: list[EventEnvelope[EventPayload]] = []
        for robot_id, state in list(self._robots.items()):
            if state.retreating_since_s is None:
                continue
            if not state.trajectory:
                continue
            last = state.trajectory[-1]
            # Only resume once the robot has actually arrived. Clearing the
            # retreat on elapsed time alone rebound the task while the machine
            # was still standing in the passage, which put it straight back into
            # the same standoff.
            if observed_at_s < last.timestamp_s or state.current_cell != last.cell:
                continue
            task = self._tasks.get(state.task_id) if state.task_id else None
            cleared = replace(state, retreating_since_s=None, route=None, trajectory=())
            self._robots[robot_id] = cleared
            if task is None:
                continue
            # The robot is out of the way, so its task route is planned again
            # from where it now stands. ``commit=False`` because this already
            # runs inside a commit.
            events.extend(self.assign_task(task.task_id, robot_id, commit=False))
        return events

    # ------------------------------------------------------------------
    # Metrics and projection
    # ------------------------------------------------------------------

    def record_allocation_latency_ms(self, latency_ms: float) -> None:
        """Record one Agent 1 allocation latency for ``SystemMetrics``."""

        if not isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative finite number")
        self._allocation_latencies_ms.append(latency_ms)

    def metrics(self) -> SystemMetrics:
        robots = self.robots()
        tasks = self.tasks()
        batteries = [robot.battery_percent for robot in robots]
        latencies = self._allocation_latencies_ms
        now_s = self._clock.now_s
        return SystemMetrics(
            active_robots=sum(
                1 for robot in robots if robot.status is RobotStatus.ACTIVE
            ),
            failed_robots=sum(
                1 for robot in robots if robot.status is RobotStatus.FAILED
            ),
            communication_lost_robots=sum(
                1
                for robot in robots
                if robot.communication_state is CommunicationState.LOST
                and robot.status is not RobotStatus.FAILED
            ),
            pending_tasks=sum(
                1
                for task in tasks
                if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            ),
            completed_tasks=sum(
                1 for task in tasks if task.status is TaskStatus.COMPLETED
            ),
            open_conflicts=sum(
                1
                for conflict in self._conflicts.values()
                if conflict.status is ResolutionStatus.OPEN
            ),
            detected_deadlocks=self._detected_deadlocks,
            task_reassignments=self._task_reassignments,
            average_battery_percent=(
                sum(batteries) / len(batteries) if batteries else 0.0
            ),
            average_allocation_latency_ms=(
                sum(latencies) / len(latencies) if latencies else 0.0
            ),
            event_throughput_per_s=self._stream.last_sequence / max(now_s, 1.0),
            controller_available=self._controller_available,
            extra_metrics={
                "planned_routes": float(len(self.routes())),
                "claimants": float(len(self.active_trajectories())),
                "emitted_events": float(self._stream.last_sequence),
                "simulation_speed": self._clock.speed_multiplier,
            },
        )

    def snapshot(self) -> SimulationSnapshot:
        """Return the dashboard's authoritative point-in-time projection."""

        return SimulationSnapshot(
            simulation_time_s=self._clock.now_s,
            revision=self._revision,
            last_event_sequence=self._stream.last_sequence,
            controller_available=self._controller_available,
            world=self._world,
            robots=self.robots(),
            tasks=self.tasks(),
            routes=self.routes(),
            conflicts=tuple(self.open_conflicts().values()),
            metrics=self.metrics(),
        )

    # ------------------------------------------------------------------
    # Commands and lifecycle
    # ------------------------------------------------------------------

    def apply_command(
        self, command: ControlCommand
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Apply a canonical control command."""

        if isinstance(command, CreateTaskCommand):
            return self.submit_task(command.task)
        if isinstance(command, InjectRobotFailureCommand):
            return self.inject_failure(command.robot_id, command.failure)
        if isinstance(command, InjectCommunicationLossCommand):
            return self.inject_communication_loss(
                command.robot_id, timeout_s=command.timeout_s
            )
        if isinstance(command, RestoreRobotCommand):
            return self.restore(command.robot_id)
        if isinstance(command, PauseSimulationCommand):
            self._clock.pause()
            return ()
        if isinstance(command, ResumeSimulationCommand):
            self._clock.resume()
            return ()
        if isinstance(command, SetSimulationSpeedCommand):
            self._clock.set_speed(command.multiplier)
            return ()
        if isinstance(command, ResetSimulationCommand):
            self.reset()
            return ()
        raise TypeError(f"unsupported command {type(command).__name__}")

    def reset(self) -> None:
        """Return to the initial fleet state, keeping the same world."""

        self._reset_state()

    def _reset_state(self) -> None:
        self._clock.reset()
        self._stream.clear()
        self._robots = {
            robot_id: RobotState(
                robot=state.robot.model_copy(
                    update={
                        "status": RobotStatus.IDLE,
                        "current_task_id": None,
                        "workload": 0,
                        "failure": None,
                        "communication_state": CommunicationState.ONLINE,
                        "last_updated_at_s": 0.0,
                    }
                ),
                profile=state.profile,
                current_cell=state.current_cell,
            )
            for robot_id, state in self._initial_states.items()
        }
        self._tasks.clear()
        self._conflicts.clear()
        self._open_conflict_by_pair.clear()
        self._battery_notified.clear()
        self._reported_deadlocks.clear()
        self._yield_context.clear()
        self._allocation_latencies_ms.clear()
        self._task_reassignments = 0
        self._detected_deadlocks = 0
        self._revision = 0
        self._safety_dirty = True
        self._planner.reset_versions()

    # ------------------------------------------------------------------
    # Event production
    # ------------------------------------------------------------------

    def _emit(
        self,
        payload: EventPayload,
        occurred_at_s: float,
        *,
        correlation_id: str,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Create, publish, and return one canonical event.

        Event IDs are derived from the type, correlation, timestamp, and
        sequence rather than randomly, so a deterministic run produces
        reproducible envelopes.
        """

        sequence = self._stream.next_sequence()
        event = EventEnvelope(
            event_id=_derived_uuid(
                payload.event_type.value,
                correlation_id,
                f"{occurred_at_s:.6f}",
                str(sequence),
            ),
            sequence=sequence,
            producer=EVENT_PRODUCER,
            correlation_id=correlation_id[:128],
            occurred_at_s=occurred_at_s,
            event_type=payload.event_type,
            payload=payload,
        )
        self._stream.publish_nowait(event)
        return (event,)


def fleets_conflict_free(runtime: SimulationRuntime) -> bool:
    """Return whether the committed fleet currently has no space-time conflict."""

    return find_earliest_conflict(runtime.active_trajectories()) is None


def active_trajectory_map(runtime: SimulationRuntime) -> dict[str, Trajectory]:
    """Return the fleet's claimed trajectories as a plain sorted mapping."""

    trajectories = runtime.active_trajectories()
    return {robot_id: trajectories[robot_id] for robot_id in sorted(trajectories)}
