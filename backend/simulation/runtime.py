"""Authoritative simulation runtime for Agent 2.

This is the composition boundary: it owns the world, the fleet, the task queue,
and the event log, and it drives Agent 1's negotiation engine and the safety
subsystems in a fixed per-tick order. The order matters and is documented
because it is what makes a seeded run reproducible:

1. health      — failures and communication loss are applied first
2. charge      — robots on a pad top up or are released
3. allocate    — pending tasks go through peer negotiation, or a local
                 fallback when the coordinator is unavailable
4. plan        — assignments and recoveries become versioned routes
5. reserve     — cell reservations are refreshed from current intents
6. move        — robots advance along their routes
7. energy      — drain, low-battery announcements, return-to-charger
8. collide     — predictive conflicts and right-of-way yields
9. deadlock    — wait-for cycles and their recovery
10. metrics    — counters and the snapshot revision are finalised

The dashboard never receives anything but the canonical snapshot and the
canonical event stream, so this module is the single place where the answer to
"what is true right now" is decided.
"""

from __future__ import annotations

import random
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import count
from math import hypot
from time import perf_counter
from uuid import UUID

from backend.allocation.allocator import select_winning_bid
from backend.contracts.commands import (
    CommandType,
    ControlCommandUnion,
)
from backend.contracts.events import (
    BatteryLowPayload,
    CommunicationLostPayload,
    ConflictDetectedPayload,
    DeadlockDetectedPayload,
    EventEnvelope,
    EventPayload,
    EventType,
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
from backend.contracts.models import (
    CommunicationState,
    Conflict,
    ConflictKind,
    GridCellType,
    NegotiationStatus,
    Position2D,
    RecoveryAction,
    RecoveryActionType,
    ResolutionStatus,
    RoutePlan,
    RouteStatus,
    Robot,
    RobotCapability,
    RobotStatus,
    SimulationSnapshot,
    SystemMetrics,
    Task,
    TaskStatus,
    WorldState,
)
from backend.negotiation.engine import DefaultNegotiationEngine
from backend.negotiation.events import DecisionEventContext, DecisionEventFactory
from backend.simulation.grid import Cell, OccupancyGrid, WorldIndex
from backend.simulation.state import FleetProfile, RobotState
from backend.simulation.world import WorldSpec, build_world
from safety.battery import BatteryManager
from safety.collision import CollisionDetector
from safety.deadlock import DeadlockDetector, build_recovery_actions
from safety.failure import FailureRegistry
from safety.movement import advance_along_route
from safety.pathfinding import (
    build_distance_field,
    plan_route,
    route_length_m,
    straight_line_distance_m,
)

MAX_EVENTS_IN_MEMORY = 10_000
ALLOCATION_BATCH_PER_TICK = 40

# How many task allocations a single tick may perform. Without a cap, a backlog
# coming due at once pays for every candidate scan and every route plan in one
# tick, which is what produced a 150 ms allocation stage. The remainder is
# allocated on the following ticks, which is also how an auction behaves when a
# burst of work arrives together.
MAX_ALLOCATIONS_PER_TICK = 6

# Roughly how long one robot stays busy with a job, travel included. Used to
# size the task arrival rate against the fleet so the workload stays saturated
# instead of oscillating between a rush and a standstill.
SECONDS_OF_WORK_PER_TASK = 45.0

# How long a robot holds position after yielding right of way before it is
# replanned. Long enough for the other robot to clear the aisle, and it bounds
# replanning to once per robot per window instead of once per tick.
YIELD_HOLD_S = 2.0

# How many replans a single tick may perform. Caps the worst-case tick so an
# unusually busy one cannot blow the simulation's real-time budget.
MAX_REPLANS_PER_TICK = 6

# Simulation rate by fleet size. A small fleet gets the full 10 Hz. A large one
# runs at 5 Hz, which is still five coordination decisions a second per robot —
# ample for right of way at walking-pace speeds — and it keeps the tick inside
# its budget. The trade-off is stated here rather than discovered as an
# overage, and the scalability test measures against this interval.
TICK_RATES_BY_FLEET = ((200, 5.0), (0, 10.0))

# Target density: traversable cells per robot. Below about ten the floor is
# packed tighter than the robots are wide and conflict detection saturates.
TRAVERSABLE_CELLS_PER_ROBOT = 34.0

# A distance field is one float per cell, so only a handful are kept.
MAX_CACHED_DISTANCE_FIELDS = 12

DEFAULT_TICK_RATE_HZ = 10.0

# The generated floor leaves roughly this fraction of its cells traversable.
TRAVERSABLE_FRACTION = 0.37

# The default floor keeps a 5:3 footprint so the world is a hall, not a square.
WORLD_ASPECT = 100.0 / 60.0


def _world_shape_for(fleet_size: int) -> tuple[int, int]:
    """Return the grid dimensions that give a fleet a workable density."""

    target_cells = max(100 * 60, fleet_size * TRAVERSABLE_CELLS_PER_ROBOT / TRAVERSABLE_FRACTION)
    rows = int((target_cells / WORLD_ASPECT) ** 0.5)
    columns = int(rows * WORLD_ASPECT)
    return max(20, columns - columns % 10), max(12, rows - rows % 10)


def _tick_rate_for(fleet_size: int) -> float:
    """Return the simulation rate a fleet can sustain inside its tick budget."""

    for threshold, rate in TICK_RATES_BY_FLEET:
        if fleet_size >= threshold:
            return rate
    return DEFAULT_TICK_RATE_HZ

# Statuses a robot can hold while nothing can change it without intervention.
SETTLED_STATUSES = frozenset(
    {RobotStatus.IDLE, RobotStatus.ACTIVE, RobotStatus.BLOCKED, RobotStatus.CHARGING}
)
# Statuses a robot must always be re-evaluated to escape, because a cleared
# fault should bring it back into the fleet.
UNHEALTHY_STATUSES = frozenset(
    {RobotStatus.FAILED, RobotStatus.OFFLINE, RobotStatus.DEGRADED}
)

ROUTE_VERSIONS: dict[str, int] = {}


@dataclass(slots=True)
class RuntimeConfig:
    """Tunable simulation parameters, all documented and deterministic.

    World geometry is expressed as plain fields rather than a nested
    ``WorldSpec`` so that ``with_seed`` can rebuild a whole run from a new seed
    with one ``dataclasses.replace`` call and cannot drift out of sync.
    """

    seed: int = 2026
    fleet_size: int = 50
    tick_rate_hz: float = 10.0
    initial_task_count: int = 24
    task_arrival_interval_s: float = 1.5
    max_active_tasks: int = 400
    max_bid_candidates: int = 8
    controller_outage_at_s: float | None = 45.0
    controller_outage_duration_s: float = 12.0
    controller_outage_repeat_s: float = 90.0
    world_width_m: float | None = None
    world_height_m: float | None = None
    world_cell_size_m: float = 2.0
    world_columns: int = 100
    world_rows: int = 60
    aisle_count: int = 5
    rack_rows: int = 3
    workstation_count: int = 6
    charger_count: int = 8
    resource_count: int = 10
    dead_zone_count: int = 2
    fleet: FleetProfile = field(default_factory=FleetProfile)

    def world_spec(self) -> WorldSpec:
        """Build the seeded world specification for this configuration."""

        width_m = self.world_width_m or self.world_columns * self.world_cell_size_m
        height_m = self.world_height_m or self.world_rows * self.world_cell_size_m
        # Feature counts scale with the floor so a large warehouse is not a big
        # room with a handful of depots in it.
        area = self.world_columns * self.world_rows
        scale = max(1.0, area / (100 * 60))
        return WorldSpec(
            seed=self.seed,
            width_m=width_m,
            height_m=height_m,
            cell_size_m=self.world_cell_size_m,
            aisle_count=max(2, int(round(self.aisle_count * scale))),
            rack_rows=self.rack_rows,
            workstation_count=max(4, int(round(self.workstation_count * scale))),
            charger_count=max(4, int(round(self.charger_count * scale))),
            resource_count=max(6, int(round(self.resource_count * scale))),
            dead_zone_count=max(2, int(round(self.dead_zone_count * scale))),
        )

    def with_seed(self, seed: int) -> RuntimeConfig:
        return replace(self, seed=seed)

    @classmethod
    def for_fleet(cls, fleet_size: int, **overrides) -> RuntimeConfig:
        """Build a configuration whose workload and floor suit the fleet.

        Two things scale with fleet size.

        **Task supply.** A robot is busy for roughly ``SECONDS_OF_WORK_PER_TASK``
        on a job, so the arrival interval that keeps a fleet saturated falls as
        the fleet grows. A fixed interval starves a large fleet within a minute
        and floods a small one.

        **Floor size.** The world grows to hold a target density of roughly
        ``TRAVERSABLE_CELLS_PER_ROBOT`` cells per robot. This is not cosmetic.
        The generated floor is about a third traversable, so 500 robots on the
        200x120 m default floor is one robot per four open cells — denser than
        the robots are wide. At that spacing every pair trips the safety margin,
        conflict detection saturates, and the fleet spends its time yielding
        instead of working. Sizing the floor to the fleet keeps the density in
        a range where right-of-way is a real decision.
        """

        columns, rows = _world_shape_for(fleet_size)
        base = {
            "fleet_size": fleet_size,
            "tick_rate_hz": _tick_rate_for(fleet_size),
            "initial_task_count": max(8, int(fleet_size * 0.7)),
            "task_arrival_interval_s": max(
                0.05, SECONDS_OF_WORK_PER_TASK / max(1, fleet_size)
            ),
            "max_active_tasks": max(64, fleet_size * 2),
            "world_columns": columns,
            "world_rows": rows,
        }
        base.update(overrides)
        return cls(**base)


@dataclass(slots=True)
class RuntimeCounters:
    """Cumulative counters behind the canonical ``SystemMetrics`` values."""

    completed_tasks: int = 0
    task_reassignments: int = 0
    detected_deadlocks: int = 0
    resolved_deadlocks: int = 0
    conflicts_detected: int = 0
    yields: int = 0
    allocation_latency_ms_total: float = 0.0
    allocation_rounds: int = 0
    route_efficiency_total: float = 0.0
    route_efficiency_samples: int = 0
    planner_latency_ms_total: float = 0.0
    plans: int = 0
    battery_low_events: int = 0
    returns_to_charger: int = 0
    events_emitted: int = 0
    controller_outages: int = 0
    controller_outage_s: float = 0.0
    tick_ms_total: float = 0.0
    ticks: int = 0


class SimulationRuntime:
    """Owns world, fleet, tasks, routes, conflicts, events, and metrics."""

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self.lock = threading.RLock()
        self._listeners: list = []
        self._build(self.config)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build(self, config: RuntimeConfig, *, sequence_floor: int = 0) -> None:
        ROUTE_VERSIONS.clear()
        self.simulation_time_s = 0.0
        self.revision = 0
        # The sequence never rewinds, even across a reset: a consumer holding a
        # cursor from the previous run would otherwise stop receiving events
        # until the new run overtook it.
        self.sequence = sequence_floor
        self._run_id = f"{config.seed}:{sequence_floor}"
        self.paused = False
        self.speed_multiplier = 1.0
        self.controller_available = True
        self._next_outage_start_s = config.controller_outage_at_s
        self._outage_ends_at_s: float | None = None
        self._next_task_arrival_s = 0.0
        self._task_serial = 0

        self.world = build_world(config.world_spec())
        self.index = WorldIndex.from_world(self.world)
        self.occupancy = OccupancyGrid(columns=self.index.columns)
        self.robots: dict[str, RobotState] = {}
        self.tasks: dict[str, Task] = {}
        self.routes: dict[str, RoutePlan] = {}
        self.conflicts: dict[str, Conflict] = {}
        self.events: deque[EventEnvelope[EventPayload]] = deque(maxlen=MAX_EVENTS_IN_MEMORY)
        self.counters = RuntimeCounters()

        self.negotiation = DefaultNegotiationEngine()
        self.collision = CollisionDetector()
        self.deadlock = DeadlockDetector()
        self.battery = BatteryManager()
        self.failures = FailureRegistry()
        self._distance_fields: dict[Cell, list[float]] = {}
        self._goal_requests: dict[Cell, int] = {}
        self._charger_cells: tuple[Cell, ...] = self.index.stations(GridCellType.CHARGING)
        self._travelled_this_tick: dict[str, float] = {}
        self._robots_by_id: dict[str, Robot] = {}
        self.stage_ms: dict[str, float] = {}
        self._seed_fleet()
        self._seed_tasks()

    def _seed_fleet(self) -> None:
        """Place heterogeneous robots on free cells with varied capabilities."""

        rng = random.Random(self.config.seed)
        profile = self.config.fleet
        free_cells = [
            cell
            for cell in (
                Cell(x, y)
                for x in range(1, self.index.columns - 1)
                for y in range(1, self.index.rows - 1)
            )
            if not self.index.is_blocked(cell)
        ]
        rng.shuffle(free_cells)
        for ordinal in range(self.config.fleet_size):
            robot_id = f"robot-{ordinal + 1:04d}"
            cell = free_cells[ordinal % len(free_cells)]
            center_x, center_y = self.index.center_of(cell)
            capability_count = rng.randint(profile.min_capabilities, profile.max_capabilities)
            capabilities = tuple(
                rng.sample(list(profile.capability_pool), capability_count)
            )
            self.robots[robot_id] = RobotState(
                robot_id=robot_id,
                position=Position2D(x=center_x, y=center_y),
                battery_percent=round(
                    rng.uniform(profile.min_battery_percent, profile.max_battery_percent), 2
                ),
                capabilities=capabilities,
                status=RobotStatus.IDLE,
                speed_mps=round(rng.uniform(profile.min_speed_mps, profile.max_speed_mps), 2),
            )

    def _seed_tasks(self) -> None:
        for _ in range(self.config.initial_task_count):
            self._spawn_task()

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def _emit(
        self,
        payload: EventPayload,
        *,
        correlation_id: str,
        producer: str,
    ) -> EventEnvelope[EventPayload]:
        sequence = self._next_sequence()
        envelope = EventEnvelope(
            event_id=self._event_id(sequence),
            sequence=sequence,
            schema_version=1,
            producer=producer,
            correlation_id=correlation_id,
            occurred_at_s=round(self.simulation_time_s, 3),
            event_type=payload.event_type,
            payload=payload,
        )
        self.events.append(envelope)
        self.counters.events_emitted += 1
        return envelope

    def _event_id(self, sequence: int) -> UUID:
        """Derive an event identifier from the run and its sequence number.

        A random UUID would make two runs of the same seed differ in their event
        identifiers even when every decision is identical, which weakens the
        reproducibility the seeded runs exist to provide.
        """

        digest = sha256(f"{self._run_id}:{sequence}".encode("utf-8")).digest()
        return UUID(bytes=digest[:16], version=4)

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def events_after(self, sequence: int, limit: int = 500) -> list[EventEnvelope[EventPayload]]:
        with self.lock:
            return [
                event for event in self.events if event.sequence > sequence
            ][:limit]

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> SimulationSnapshot:
        with self.lock:
            return SimulationSnapshot(
                simulation_time_s=round(self.simulation_time_s, 3),
                revision=self.revision,
                last_event_sequence=max(self.sequence, self.revision),
                controller_available=self.controller_available,
                world=self.world,
                robots=tuple(
                    state.to_contract() for state in self.robots.values()
                ),
                tasks=tuple(self.tasks.values()),
                routes=tuple(self.routes.values()),
                conflicts=tuple(
                    conflict
                    for conflict in self.conflicts.values()
                    if conflict.status is not ResolutionStatus.RESOLVED
                ),
                metrics=self.metrics(),
            )

    def metrics(self) -> SystemMetrics:
        robots = tuple(state.to_contract() for state in self.robots.values())
        active = sum(
            1
            for robot in robots
            if robot.status in {RobotStatus.ACTIVE, RobotStatus.IDLE}
        )
        failed = sum(1 for robot in robots if robot.status is RobotStatus.FAILED)
        lost = sum(
            1 for robot in robots if robot.communication_state.value == "lost"
        )
        pending = sum(
            1
            for task in self.tasks.values()
            if task.status
            in {TaskStatus.PENDING, TaskStatus.NEGOTIATING, TaskStatus.ASSIGNED}
        )
        open_conflicts = sum(
            1
            for conflict in self.conflicts.values()
            if conflict.status is not ResolutionStatus.RESOLVED
        )
        average_battery = (
            sum(robot.battery_percent for robot in robots) / len(robots) if robots else 0.0
        )
        counters = self.counters
        average_allocation_latency = (
            counters.allocation_latency_ms_total / counters.allocation_rounds
            if counters.allocation_rounds
            else 0.0
        )
        average_tick_ms = (
            counters.tick_ms_total / counters.ticks if counters.ticks else 0.0
        )
        average_plan_ms = (
            counters.planner_latency_ms_total / counters.plans if counters.plans else 0.0
        )
        route_efficiency = (
            counters.route_efficiency_total / counters.route_efficiency_samples
            if counters.route_efficiency_samples
            else 0.0
        )
        return SystemMetrics(
            active_robots=active,
            failed_robots=failed,
            communication_lost_robots=lost,
            pending_tasks=pending,
            completed_tasks=counters.completed_tasks,
            open_conflicts=open_conflicts,
            detected_deadlocks=counters.detected_deadlocks,
            task_reassignments=counters.task_reassignments,
            average_battery_percent=round(average_battery, 2),
            average_allocation_latency_ms=round(average_allocation_latency, 3),
            event_throughput_per_s=round(
                counters.events_emitted / self.simulation_time_s
                if self.simulation_time_s > 0
                else 0.0,
                2,
            ),
            controller_available=self.controller_available,
            extra_metrics={
                "fleet_size": float(len(robots)),
                "simulation_speed_multiplier": self.speed_multiplier,
                "paused": 1.0 if self.paused else 0.0,
                "controller_outages": float(counters.controller_outages),
                "controller_outage_seconds": round(counters.controller_outage_s, 2),
                "robots_charging": float(
                    sum(1 for robot in robots if robot.status is RobotStatus.CHARGING)
                ),
                "robots_blocked": float(
                    sum(1 for robot in robots if robot.status is RobotStatus.BLOCKED)
                ),
                "robots_degraded": float(
                    sum(1 for robot in robots if robot.status is RobotStatus.DEGRADED)
                ),
                "robots_offline": float(
                    sum(1 for robot in robots if robot.status is RobotStatus.OFFLINE)
                ),
                "active_routes": float(len(self.routes)),
                "reserved_cells": float(self.occupancy.reserved_count()),
                "conflict_yields": float(counters.yields),
                "deadlocks_resolved": float(counters.resolved_deadlocks),
                "battery_low_events": float(counters.battery_low_events),
                "returns_to_charger": float(counters.returns_to_charger),
                "route_efficiency_ratio": round(route_efficiency, 4),
                "planner_latency_ms": round(average_plan_ms, 3),
                "average_tick_ms": round(average_tick_ms, 3),
                "tasks_tracked": float(len(self.tasks)),
                "events_retained": float(len(self.events)),
                **{
                    f"stage_{name}_ms": round(value, 3)
                    for name, value in self.stage_ms.items()
                },
            },
        )

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------
    def tick(self, dt_s: float | None = None) -> None:
        with self.lock:
            started = perf_counter()
            if self.paused:
                return
            dt = dt_s if dt_s is not None else 1.0 / max(self.config.tick_rate_hz, 1.0)
            # Round the clock every tick. Without this, accumulated float error
            # makes ``simulation_time_s`` drift a hair behind the rounded
            # timestamps written into tasks and events, which then look like
            # they were created in the future.
            self.simulation_time_s = round(self.simulation_time_s + dt, 6)
            self.revision += 1

            self._update_controller_availability()
            self._stage("health", self._step_health, dt)
            self._stage("charging", self._step_charging, dt)
            self._stage("task_arrivals", self._task_arrivals)
            self._stage("allocation", self._step_allocation)
            self._stage("reservations", self._step_reservations)
            self._travelled_this_tick.clear()
            self._stage("movement", self._step_movement, dt)
            # One contract projection per robot per tick, shared by the energy,
            # collision, and deadlock steps. Projecting per step cost a quarter
            # of the tick budget at 500 robots for identical results.
            self._stage(
                "projection",
                lambda: self._robots_by_id.update(
                    (robot_id, state.to_contract())
                    for robot_id, state in self.robots.items()
                ),
            )
            self._stage("energy", self._step_energy, dt)
            self._stage("collision", self._step_collisions)
            self._stage("yield_release", self._release_yields)
            self._stage("deadlock", self._step_deadlock)

            self.counters.ticks += 1
            self.counters.tick_ms_total += (perf_counter() - started) * 1000
            self.sequence = max(self.sequence, self.revision)
        self._notify()

    def _stage(self, name: str, step, *args) -> None:
        """Run one tick stage and record how long it took.

        Stage timings are published in ``extra_metrics`` so the dashboard can
        show where simulation time actually goes instead of guessing, and so a
        performance regression is attributable to a specific subsystem.
        """

        started = perf_counter()
        step(*args)
        elapsed = (perf_counter() - started) * 1000
        previous = self.stage_ms.get(name, 0.0)
        self.stage_ms[name] = previous * 0.9 + elapsed * 0.1

    def run_ticks(self, count: int, dt_s: float | None = None) -> None:
        """Advance the simulation deterministically without wall-clock time."""

        dt = dt_s if dt_s is not None else 1.0 / max(self.config.tick_rate_hz, 1.0)
        for _ in range(count):
            self.tick(dt)

    def _update_controller_availability(self) -> None:
        """Toggle the coordination service on a documented, seeded schedule.

        There is no command in the canonical contract for a coordinator outage,
        so the fault is scheduled in configuration rather than invented as a
        new command. During an outage robots keep executing their current work
        and fall back to local claiming for new tasks, which is exactly the
        behaviour the problem statement asks the demo to show.
        """

        config = self.config
        if config.controller_outage_at_s is None:
            return
        now = self.simulation_time_s
        if self._outage_ends_at_s is not None and now >= self._outage_ends_at_s:
            self.controller_available = True
            self._outage_ends_at_s = None
            self._next_outage_start_s = (
                now + config.controller_outage_repeat_s
                if config.controller_outage_repeat_s > 0
                else None
            )
            return
        if self._next_outage_start_s is not None and now >= self._next_outage_start_s:
            self.controller_available = False
            self._outage_ends_at_s = now + config.controller_outage_duration_s
            self._next_outage_start_s = None
            self.counters.controller_outages += 1

    def _step_health(self, dt: float) -> None:
        for state in self.robots.values():
            if state.status in SETTLED_STATUSES and self.failures.is_settled(
                state.robot_id, state.battery_percent
            ):
                # Nothing about this robot can have changed: no injected fault,
                # no silence window, energy left, and it is not sitting in a
                # failure state waiting to be revived. Skipping here is what
                # keeps the health stage under a millisecond at 500 robots.
                continue
            previous_status = state.status
            previous_communication = state.communication_state
            verdict = self.failures.evaluate(
                state.robot_id,
                state.battery_percent,
                state.status,
                state.communication_state,
                now_s=self.simulation_time_s,
            )

            if verdict.usable:
                state.communication_state = CommunicationState.ONLINE
                if state.status in UNHEALTHY_STATUSES:
                    # The fault is gone, so the robot rejoins the fleet instead
                    # of staying stuck in the state the fault left it in.
                    state.failure = None
                    state.status = (
                        RobotStatus.ACTIVE if state.current_task_id else RobotStatus.IDLE
                    )
                    self.occupancy.clear_robot(state.robot_id)
            else:
                state.communication_state = verdict.communication_state
                state.failure = verdict.failure
                if verdict.status is RobotStatus.FAILED:
                    state.status = RobotStatus.FAILED
                elif state.status is not RobotStatus.CHARGING:
                    state.status = verdict.status
            state.last_updated_at_s = self.simulation_time_s

            if previous_status is not RobotStatus.FAILED and state.status is RobotStatus.FAILED:
                orphaned_task = (
                    self.tasks.get(state.current_task_id) if state.current_task_id else None
                )
                self._emit(
                    RobotFailedPayload(robot_id=state.robot_id, failure=verdict.failure),
                    correlation_id=state.current_task_id or state.robot_id,
                    producer="agent-2-safety",
                )
                if orphaned_task is not None:
                    # A failure is a reassignment, not a cancellation: the work
                    # migrates to another robot and the event says so.
                    self._reassign(orphaned_task, state.robot_id, "robot failure")
                else:
                    self._release_task(state, reason="robot failure")
            if (
                previous_communication is not CommunicationState.LOST
                and state.communication_state is CommunicationState.LOST
            ):
                self._emit(
                    CommunicationLostPayload(
                        robot_id=state.robot_id,
                        last_contact_at_s=round(
                            self.simulation_time_s
                            - self.failures.contact_lost_for_s(
                                state.robot_id, now_s=self.simulation_time_s
                            ),
                            3,
                        ),
                        timeout_s=self.failures.communication_timeout_s,
                    ),
                    correlation_id=state.current_task_id or state.robot_id,
                    producer="agent-2-safety",
                )

    def _step_charging(self, dt: float) -> None:
        for state in self.robots.values():
            if state.status is not RobotStatus.CHARGING:
                continue
            if not self.battery.is_satisfied(state.battery_percent):
                continue
            # Leaving the pad has to retire the trip, not just the status. A
            # robot that kept its charge task would hold it forever, making it
            # permanently ineligible for real work while the task queue counted
            # a phantom entry against its live budget — arrivals would stop and
            # the whole fleet would starve.
            self._retire_charge_task(state)
            state.charge_target_cell = None
            state.status = RobotStatus.IDLE
            self.occupancy.clear_robot(state.robot_id)

    def _retire_charge_task(self, state: RobotState) -> None:
        """Complete the robot's trip to a pad and release its claim on the pad."""

        task_id = state.current_task_id
        state.current_task_id = None
        state.task_started_at_s = None
        state.service_remaining_s = 0.0
        state.service_total_s = 0.0
        if task_id is None or not task_id.startswith("charge-"):
            return
        task = self.tasks.get(task_id)
        if task is not None:
            self.tasks[task_id] = task.model_copy(update={"status": TaskStatus.COMPLETED})

    def _task_arrivals(self) -> None:
        if self.simulation_time_s < self._next_task_arrival_s:
            return
        self._next_task_arrival_s = self.simulation_time_s + self.config.task_arrival_interval_s
        if self._live_task_count() >= self.config.max_active_tasks:
            return
        self._spawn_task()

    def _live_task_count(self) -> int:
        """Count queued work still competing for a robot.

        Charge trips are excluded: a trip to a pad is a robot's own errand, not
        work from the queue, and counting it here would let a fleet rotating
        through chargers throttle its own arrivals to nothing.
        """

        return sum(
            1
            for task in self.tasks.values()
            if task.status is not TaskStatus.COMPLETED
            and not task.task_id.startswith("charge-")
        )

    def _spawn_task(self) -> Task:
        rng = random.Random(f"{self.config.seed}:{self._task_serial}")
        self._task_serial += 1
        target_cell = self._pick_task_target(rng)
        center_x, center_y = self.index.center_of(target_cell)
        capability = rng.choice(
            [
                RobotCapability.TRANSPORT,
                RobotCapability.PICK,
                RobotCapability.TUG,
                RobotCapability.INSPECT,
                RobotCapability.DELIVER,
            ]
        )
        task = Task(
            task_id=f"task-{self._task_serial:05d}",
            target=Position2D(x=center_x, y=center_y),
            priority=rng.randint(1, 5),
            required_capabilities=(capability,),
            estimated_duration_s=round(rng.uniform(15.0, 90.0), 1),
            status=TaskStatus.PENDING,
            assigned_robot_id=None,
            created_at_s=round(self.simulation_time_s, 3),
        )
        self.tasks[task.task_id] = task
        self._emit(
            TaskCreatedPayload(task=task),
            correlation_id=task.task_id,
            producer="runtime",
        )
        return task

    def _pick_task_target(self, rng: random.Random) -> Cell:
        """Choose where the work is.

        Targets are spread across the floor instead of being concentrated on the
        depots. Pointing every job at a handful of cells made robots migrate to
        those cells and left each later job a few metres away, which is not a
        workload worth simulating. Most work now lands on open floor, with
        deliberate trips to depots and workstations so those stay worth visiting.
        """

        roll = rng.random()
        if roll < 0.2:
            return self._random_feature_cell(GridCellType.RESOURCE) or self._random_free_cell(rng)
        if roll < 0.3:
            return (
                self._random_feature_cell(GridCellType.WORKSTATION)
                or self._random_free_cell(rng)
            )
        return self._random_free_cell(rng)

    def _random_feature_cell(self, cell_type: GridCellType) -> Cell | None:
        options = self.index.stations(cell_type)
        if not options:
            return None
        return options[random.Random(f"{self.config.seed}:{cell_type}:{self._task_serial}").randrange(len(options))]

    def _random_free_cell(self, rng: random.Random | None = None) -> Cell:
        generator = rng or random.Random(f"{self.config.seed}:free:{self._task_serial}")
        for _ in range(64):
            cell = Cell(
                generator.randrange(1, self.index.columns - 1),
                generator.randrange(1, self.index.rows - 1),
            )
            if not self.index.is_blocked(cell):
                return cell
        return Cell(1, 1)

    # ------------------------------------------------------------------
    # allocation and planning
    # ------------------------------------------------------------------
    def _step_allocation(self) -> None:
        """Assign pending tasks, negotiating or falling back to local claiming."""

        pending = [
            task
            for task in self.tasks.values()
            if task.status is TaskStatus.PENDING
        ]
        pending.sort(key=lambda task: (-task.priority, task.created_at_s, task.task_id))
        allocated = 0
        for task in pending:
            if allocated >= MAX_ALLOCATIONS_PER_TICK:
                break
            before = self.sequence
            if self.controller_available:
                self._negotiate_and_assign(task)
            else:
                self._locally_claim(task)
            if self.sequence > before:
                allocated += 1

    def _negotiate_and_assign(self, task: Task) -> None:
        """Run one peer negotiation round and apply the outcome.

        The engine's ``decide`` coroutine is composed here from its synchronous
        parts instead of being awaited: the tick loop is synchronous, and
        spinning an event loop per allocation would dominate the tick budget.
        """

        candidates = self._eligible_robots(task)
        if not candidates:
            return
        started = perf_counter()
        negotiation_round = self.negotiation.prepare(
            task, candidates, self.simulation_time_s
        )
        # The factory allocates sequences from this runtime's counter; mirror it
        # so the deterministic event identifiers line up with those numbers.
        identifier_counter = count(self.sequence + 1)
        factory = DecisionEventFactory(
            DecisionEventContext(
                correlation_id=task.task_id,
                first_sequence=self.sequence + 1,
                event_id_factory=lambda: self._event_id(next(identifier_counter)),
            )
        )
        events = self.negotiation.round_events(negotiation_round, factory)
        outcome = self.negotiation.finish(negotiation_round)
        if outcome.assignment is not None:
            events.append(
                factory.create(
                    TaskAssignedPayload(assignment=outcome.assignment),
                    self.simulation_time_s,
                )
            )
        elapsed_ms = (perf_counter() - started) * 1000
        self.counters.allocation_latency_ms_total += elapsed_ms
        self.counters.allocation_rounds += 1

        for event in events:
            self._append(event)

        if outcome.assignment is None:
            self.tasks[task.task_id] = task.model_copy(
                update={"status": TaskStatus.NEGOTIATING}
            )
            return

        assignment = outcome.assignment
        self.tasks[task.task_id] = task.model_copy(
            update={
                "status": TaskStatus.ASSIGNED,
                "assigned_robot_id": assignment.robot_id,
            }
        )
        state = self.robots.get(assignment.robot_id)
        if state is not None:
            state.current_task_id = task.task_id
            state.workload += 1
            state.task_started_at_s = self.simulation_time_s
            state.status = RobotStatus.ACTIVE
        self._request_route(state, task)

    def _locally_claim(self, task: Task) -> None:
        """Assign a task with no coordinator by local best-fit claiming.

        This is the behaviour the outage requirement is really about: with the
        coordination service down, each robot decides for itself using the same
        cost inputs the auction would have used, and the task still gets done.
        """

        candidates = self._eligible_robots(task)
        if not candidates:
            return
        best = min(
            candidates,
            key=lambda robot: (
                self.failures.contact_lost_for_s(robot.robot_id, now_s=self.simulation_time_s) > 0,
                hypot(task.target.x - robot.position.x, task.target.y - robot.position.y)
                + (100.0 - robot.battery_percent)
                + robot.workload,
                robot.robot_id,
            ),
        )
        self.tasks[task.task_id] = task.model_copy(
            update={"status": TaskStatus.ASSIGNED, "assigned_robot_id": best.robot_id}
        )
        state = self.robots[best.robot_id]
        state.current_task_id = task.task_id
        state.workload += 1
        state.task_started_at_s = self.simulation_time_s
        state.status = RobotStatus.ACTIVE
        self._emit(
            TaskAssignedPayload(
                assignment=_local_assignment(task.task_id, best.robot_id, self.simulation_time_s)
            ),
            correlation_id=task.task_id,
            producer="agent-2-local-claim",
        )
        self._request_route(state, self.tasks[task.task_id])

    def _eligible_robots(self, task: Task) -> list[Robot]:
        """Robots that could take the task right now, as contract values.

        The result is capped to the ``max_bid_candidates`` nearest robots. With
        500 robots a single task can match hundreds of candidates, and letting
        every one of them bid would emit thousands of ``BID_SUBMITTED`` events
        per allocation and drown the event stream. Peer-to-peer auctions
        normally bound participation the same way: only the closest plausible
        bidders bid, because a robot that is further away and no less loaded
        cannot win on cost. Agent 1 still runs its full scoring and allocation
        over the candidates it receives.
        """

        available: list[tuple[float, int, str, RobotState]] = []
        required = set(task.required_capabilities)
        for state in self.robots.values():
            if state.current_task_id is not None:
                continue
            if state.status in {
                RobotStatus.FAILED,
                RobotStatus.OFFLINE,
                RobotStatus.CHARGING,
                RobotStatus.BLOCKED,
            }:
                continue
            if self.failures.is_silenced(state.robot_id, now_s=self.simulation_time_s):
                continue
            if state.battery_percent <= self.battery.low_threshold_percent:
                continue
            if not required.issubset(set(state.capabilities)):
                continue
            # Filtering and ranking happen on the mutable state; the canonical
            # ``Robot`` is only built for the handful that actually bid, which
            # keeps validation cost off the hot path at 500 robots.
            available.append(
                (
                    hypot(
                        task.target.x - state.position.x,
                        task.target.y - state.position.y,
                    ),
                    state.workload,
                    state.robot_id,
                    state,
                )
            )

        if len(available) > self.config.max_bid_candidates:
            available.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
            available = available[: self.config.max_bid_candidates]
        return [state.to_contract() for _, _, _, state in available]

    def _request_route(self, state: RobotState | None, task: Task) -> None:
        if state is None:
            return
        self._emit(
            RouteRequestedPayload(
                task_id=task.task_id,
                robot_id=state.robot_id,
                origin=state.position,
                target=task.target,
            ),
            correlation_id=task.task_id,
            producer="agent-2-safety",
        )
        self._plan_for(state, task, reason=None)

    def _distance_field(self, goal: Cell) -> list[float] | None:
        """Return a shared cost-to-go field for a destination, if one is worth it.

        A field costs a Dijkstra sweep over every cell, which is cheap on a
        small floor and expensive on a large one. It only pays off when several
        robots are routed to the same destination, so the first plan for a goal
        runs without one and the field is built only once that goal is seen
        again. The cache is capped because a field is one float per cell.
        """

        field = self._distance_fields.get(goal)
        if field is not None:
            return field
        seen = self._goal_requests.get(goal, 0) + 1
        self._goal_requests[goal] = seen
        if seen < 2 or len(self._distance_fields) >= MAX_CACHED_DISTANCE_FIELDS:
            return None
        field = build_distance_field(self.index, goal)
        self._distance_fields[goal] = field
        return field

    def _plan_for(
        self,
        state: RobotState,
        task: Task,
        *,
        reason: str | None,
        target: Position2D | None = None,
    ) -> RoutePlan | None:
        """Plan or replan a route, emitting the matching canonical event."""

        destination = target or task.target
        goal = self.index.cell_of(destination)
        started = perf_counter()
        try:
            route = plan_route(
                robot_id=state.robot_id,
                task_id=task.task_id,
                origin=state.position,
                target=destination,
                world=self.world,
                occupancy=self.occupancy,
                planned_at_s=self.simulation_time_s,
                route_id=f"route-{state.robot_id}-{task.task_id}",
                version=ROUTE_VERSIONS.get(state.robot_id, 0) + 1,
                index=self.index,
                distance_field=self._distance_field(goal),
            )
        except ValueError as error:
            self._isolate(state, str(error))
            return None

        ROUTE_VERSIONS[state.robot_id] = route.version
        self.counters.plans += 1
        self.counters.planner_latency_ms_total += (perf_counter() - started) * 1000
        ideal = straight_line_distance_m(state.position, destination)
        if ideal > 1e-6:
            self.counters.route_efficiency_total += route_length_m(route.waypoints) / ideal
            self.counters.route_efficiency_samples += 1

        previous = self.routes.get(state.robot_id)
        if previous is None and reason is None:
            self._emit(
                RoutePlannedPayload(route=route),
                correlation_id=task.task_id,
                producer="agent-2-safety",
            )
        else:
            self._emit(
                RouteReplannedPayload(
                    route=route,
                    reason=(reason or "replan requested")[:500],
                ),
                correlation_id=task.task_id,
                producer="agent-2-safety",
            )
        self.routes[state.robot_id] = route
        if state.status in {RobotStatus.IDLE, RobotStatus.BLOCKED}:
            state.status = RobotStatus.ACTIVE
            state.blocked_since_s = None
        return route

    def _isolate(self, state: RobotState, reason: str) -> None:
        """No route exists: hold the robot and record why, without a crash."""

        state.status = RobotStatus.BLOCKED
        self._emit(
            RecoveryStartedPayload(
                action=RecoveryAction(
                    action_id=f"recovery-isolate-{state.robot_id}-{int(self.simulation_time_s * 1000)}",
                    action_type=RecoveryActionType.ISOLATE,
                    target_robot_ids=(state.robot_id,),
                    affected_task_ids=(state.current_task_id,) if state.current_task_id else (),
                    reason=f"route planning failed: {reason}"[:500],
                    started_at_s=round(self.simulation_time_s, 3),
                    status="active",
                )
            ),
            correlation_id=state.current_task_id or state.robot_id,
            producer="agent-2-safety",
        )

    # ------------------------------------------------------------------
    # movement
    # ------------------------------------------------------------------
    def _step_reservations(self) -> None:
        """Refresh cell reservations from each moving robot's near-term intent."""

        self.occupancy.by_robot.clear()
        self.occupancy.reservations.clear()
        self.occupancy.flat_owners.clear()
        for robot_id, route in self.routes.items():
            state = self.robots.get(robot_id)
            if state is None or state.status in {
                RobotStatus.FAILED,
                RobotStatus.OFFLINE,
                RobotStatus.IDLE,
            }:
                continue
            ahead = _ahead_cell(state.position, route, self.index)
            self.occupancy.reserve(robot_id, (ahead, self.index.cell_of(state.position)))

    def _step_movement(self, dt: float) -> None:
        for robot_id, route in list(self.routes.items()):
            state = self.robots.get(robot_id)
            if state is None:
                continue
            if state.status in {RobotStatus.FAILED, RobotStatus.OFFLINE, RobotStatus.CHARGING}:
                continue
            if state.status is RobotStatus.DEGRADED:
                # A robot that cannot be reached keeps its last known state
                # instead of guessing where it is.
                continue
            if state.status is RobotStatus.BLOCKED:
                # A blocked robot is holding position for right of way. It
                # resumes when a replan clears the conflict, not by driving
                # through the robot it was told to yield to.
                self._travelled_this_tick[state.robot_id] = 0.0
                continue
            task = self.tasks.get(route.task_id)
            if task is None:
                self.routes.pop(robot_id, None)
                continue
            if state.current_task_id != task.task_id:
                self.routes.pop(robot_id, None)
                continue

            result = advance_along_route(
                route, state.position, speed_mps=state.speed_mps, max_step_m=state.speed_mps * dt
            )
            state.position = result.position
            self._travelled_this_tick[state.robot_id] = result.distance_travelled_m
            if result.arrived:
                self._begin_or_finish_service(state, task, dt)

    def _begin_or_finish_service(self, state: RobotState, task: Task, dt: float) -> None:
        """Start the work at the destination and finish the task when it is done.

        A task is travel plus service. Without the service phase a task ends the
        instant a robot touches its target, so in a dense floor nearly every job
        is a few metres long, every robot is free again immediately, and the
        fleet looks busy for a moment and then starves. ``estimated_duration_s``
        is a contract field; this is what it means.
        """

        if state.service_total_s <= 0.0:
            state.service_total_s = task.estimated_duration_s
            state.service_remaining_s = task.estimated_duration_s

        state.service_remaining_s -= dt
        if state.service_remaining_s > 0.0:
            return
        state.service_remaining_s = 0.0
        state.service_total_s = 0.0
        self._complete_task(state, task)

    def _complete_task(self, state: RobotState, task: Task) -> None:
        started = state.task_started_at_s or task.created_at_s
        self.tasks[task.task_id] = task.model_copy(
            update={"status": TaskStatus.COMPLETED, "assigned_robot_id": state.robot_id}
        )
        self.counters.completed_tasks += 1
        state.current_task_id = None
        state.task_started_at_s = None
        state.service_remaining_s = 0.0
        state.service_total_s = 0.0
        state.workload = max(0, state.workload - 1)
        state.status = RobotStatus.IDLE
        self.routes.pop(state.robot_id, None)
        self.occupancy.clear_robot(state.robot_id)
        self._emit(
            TaskCompletedPayload(
                task_id=task.task_id,
                robot_id=state.robot_id,
                started_at_s=round(started, 3),
                completed_at_s=round(self.simulation_time_s, 3),
            ),
            correlation_id=task.task_id,
            producer="agent-2-safety",
        )

    # ------------------------------------------------------------------
    # energy
    # ------------------------------------------------------------------
    def _step_energy(self, dt: float) -> None:
        # Pads already claimed this tick, gathered once. Rebuilding the claimed
        # set per robot turns a fleet-wide charging wave into quadratic work.
        claimed_pads: set[Cell] = {
            Cell(*cell)
            for cell in (
                other.charge_target_cell
                for other in self.robots.values()
                if other.charge_target_cell is not None
            )
        }
        for state in self.robots.values():
            if state.status in {RobotStatus.FAILED, RobotStatus.OFFLINE}:
                continue
            # Energy follows the distance actually covered this tick, recorded
            # by the movement step, and starts from the raw state value. Using
            # the remaining route length would charge the robot for the whole
            # journey every tick; reading a projected contract would feed back a
            # rounded battery and freeze it.
            travelled = self._travelled_this_tick.get(state.robot_id, 0.0)
            state.battery_percent = self.battery.drain(
                state.battery_percent,
                state.status,
                distance_travelled_m=travelled,
                elapsed_s=dt,
                carrying=state.current_task_id is not None,
            )
            if state.status is RobotStatus.CHARGING:
                continue
            task = self.tasks.get(state.current_task_id) if state.current_task_id else None
            decision = self.battery.assess(
                state.robot_id,
                state.battery_percent,
                state.status,
                state.position,
                task,
                index=self.index,
                now_s=self.simulation_time_s,
            )
            if decision is None:
                continue
            self.counters.battery_low_events += 1
            self._emit(
                BatteryLowPayload(
                    robot_id=state.robot_id,
                    battery_percent=decision.battery_percent,
                    threshold_percent=decision.threshold_percent,
                    estimated_range_m=decision.estimated_range_m,
                ),
                correlation_id=state.current_task_id or state.robot_id,
                producer="agent-2-safety",
            )
            if decision.should_return_to_charger:
                self._send_to_charger(state, claimed_pads)
                if state.charge_target_cell is not None:
                    claimed_pads.add(Cell(*state.charge_target_cell))

    def _send_to_charger(self, state: RobotState, claimed_pads: set[Cell]) -> None:
        """Route a low-energy robot to a pad and hand its task back."""

        task = self.tasks.get(state.current_task_id) if state.current_task_id else None
        if task is not None:
            # Hand the work back before leaving for the pad, so the mission
            # keeps making progress while this robot is off the floor.
            self._reassign(task, state.robot_id, "battery below the reserve")

        if state.charge_target_cell is None:
            charger = self.battery.pick_charger(
                state.position,
                self.index,
                reserved=frozenset(claimed_pads),
                candidates=self._charger_cells,
            )
            if charger is None:
                return
            state.charge_target_cell = (charger.x, charger.y)
        center_x, center_y = self.index.center_of(Cell(*state.charge_target_cell))
        destination = Position2D(x=center_x, y=center_y)
        self.counters.returns_to_charger += 1
        state.status = RobotStatus.CHARGING
        self._emit(
            RecoveryStartedPayload(
                action=self.battery.build_return_action(
                    state.robot_id,
                    state.battery_percent,
                    (destination.x, destination.y),
                    now_s=self.simulation_time_s,
                )
            ),
            correlation_id=state.robot_id,
            producer="agent-2-safety",
        )
        charge_task = Task(
            task_id=f"charge-{state.robot_id}",
            target=destination,
            priority=1,
            required_capabilities=(),
            estimated_duration_s=1.0,
            status=TaskStatus.ASSIGNED,
            assigned_robot_id=state.robot_id,
            created_at_s=round(self.simulation_time_s, 3),
        )
        self.tasks[charge_task.task_id] = charge_task
        state.current_task_id = charge_task.task_id
        state.task_started_at_s = self.simulation_time_s
        self._plan_for(state, charge_task, reason="returning to a charger", target=destination)

    # ------------------------------------------------------------------
    # conflicts
    # ------------------------------------------------------------------
    def _step_collisions(self) -> None:
        moving = {
            robot_id: route
            for robot_id, route in self.routes.items()
            if (state := self.robots.get(robot_id)) is not None
            and state.status in {RobotStatus.ACTIVE, RobotStatus.BLOCKED}
        }
        if len(moving) < 2:
            return
        robots_by_id = self._robots_by_id
        priorities = {
            robot_id: self._task_priority(state) for robot_id, state in self.robots.items()
        }
        intents = [
            self.collision.build_intent(
                robots_by_id[robot_id],
                route,
                # A blocked robot projects a stationary path: it is holding
                # position, so it occupies its current cell and nothing else.
                speed_mps=(
                    0.0
                    if self.robots[robot_id].status is RobotStatus.BLOCKED
                    else self.robots[robot_id].speed_mps
                ),
                index=self.index,
            )
            for robot_id, route in moving.items()
        ]
        conflicts, decisions = self.collision.detect(
            intents,
            robots_by_id=robots_by_id,
            task_priority_by_robot=priorities,
            cell_size_m=self.index.cell_size_m,
            now_s=self.simulation_time_s,
        )
        for conflict in conflicts:
            self.counters.conflicts_detected += 1
            self.conflicts[conflict.conflict_id] = conflict
            self._emit(
                ConflictDetectedPayload(conflict=conflict),
                correlation_id=conflict.conflict_id,
                producer="agent-2-safety",
            )
        for decision in decisions:
            self._apply_yield(decision)

    def _apply_yield(self, decision) -> None:
        """Hold the losing robot so the right-of-way robot can pass.

        A yield is a bounded hold, not an immediate replan. Replanning on every
        conflict is both wasteful and wrong: in a dense fleet dozens of robots
        yield every tick, and replanning each one immediately cost more than the
        rest of the simulation combined while producing routes that were
        obsolete before the next tick. The robot holds, and
        ``_release_yields`` gives it a fresh route once the conflict has had a
        moment to clear.
        """

        yielder = self.robots.get(decision.yielding_robot_id)
        if yielder is None or yielder.status in {RobotStatus.FAILED, RobotStatus.OFFLINE}:
            return
        task = self.tasks.get(yielder.current_task_id) if yielder.current_task_id else None
        self.counters.yields += 1
        yielder.yield_count += 1
        if yielder.status is RobotStatus.BLOCKED and yielder.blocked_since_s is not None:
            return
        yielder.status = RobotStatus.BLOCKED
        yielder.blocked_since_s = self.simulation_time_s
        self._emit(
            RecoveryStartedPayload(
                action=RecoveryAction(
                    action_id=f"recovery-yield-{yielder.robot_id}-{int(self.simulation_time_s * 1000)}",
                    action_type=RecoveryActionType.YIELD,
                    target_robot_ids=(yielder.robot_id,),
                    affected_task_ids=(task.task_id,) if task else (),
                    reason=decision.reason[:500],
                    started_at_s=round(self.simulation_time_s, 3),
                    status="active",
                )
            ),
            correlation_id=task.task_id if task else yielder.robot_id,
            producer="agent-2-safety",
        )
        self._resolve_conflicts_for(yielder.robot_id)

    def _release_yields(self) -> None:
        """Replan robots that have held long enough for the aisle to clear.

        This is what turns a yield back into progress. Waiting before replanning
        bounds the cost, and the per-tick cap bounds the worst case: without it
        a single tick in which many robots come due at once pays for every plan
        at once, which is what makes an occasional tick four times the average.
        Robots that miss the cap are simply replanned on the next tick.
        """

        replanned = 0
        for state in self.robots.values():
            if replanned >= MAX_REPLANS_PER_TICK:
                break
            if state.status is not RobotStatus.BLOCKED or state.blocked_since_s is None:
                continue
            held_for = self.simulation_time_s - state.blocked_since_s
            if held_for < YIELD_HOLD_S:
                continue
            task = self.tasks.get(state.current_task_id) if state.current_task_id else None
            if task is None or task.status is TaskStatus.COMPLETED:
                state.status = RobotStatus.IDLE
                state.blocked_since_s = None
                self.routes.pop(state.robot_id, None)
                continue
            replanned += 1
            route = self._plan_for(
                state,
                task,
                reason=f"resuming after yielding right of way for {held_for:.1f}s",
            )
            if route is not None:
                state.status = RobotStatus.ACTIVE
                state.blocked_since_s = None

    def _resolve_conflicts_for(self, robot_id: str) -> None:
        for conflict in self.conflicts.values():
            if robot_id in conflict.robot_ids and conflict.status is not ResolutionStatus.RESOLVED:
                self.conflicts[conflict.conflict_id] = conflict.model_copy(
                    update={"status": ResolutionStatus.RESOLVED}
                )

    # ------------------------------------------------------------------
    # deadlock
    # ------------------------------------------------------------------
    def _step_deadlock(self) -> None:
        moving = {
            robot_id: route
            for robot_id, route in self.routes.items()
            if (state := self.robots.get(robot_id)) is not None
            and state.status in {RobotStatus.ACTIVE, RobotStatus.BLOCKED}
        }
        if len(moving) < 2:
            return
        robots_by_id = self._robots_by_id
        edges = self.deadlock.build_edges(
            robots=robots_by_id,
            routes=moving,
            reservation_owner=self.occupancy.reservations,
            index=self.index,
            cell_size_m=self.index.cell_size_m,
        )
        if not edges:
            return
        blocked_tasks = {
            robot_id: (state.current_task_id or None)
            for robot_id, state in self.robots.items()
        }
        reports = self.deadlock.detect(
            edges,
            robots=robots_by_id,
            blocked_task_by_robot=blocked_tasks,
            now_s=self.simulation_time_s,
        )
        if not reports:
            return
        actions = build_recovery_actions(
            reports, robots=robots_by_id, index=self.index, now_s=self.simulation_time_s
        )
        for report, _ in reports:
            self.counters.detected_deadlocks += 1
            self._emit(
                DeadlockDetectedPayload(report=report),
                correlation_id=report.deadlock_id,
                producer="agent-2-safety",
            )
        for action in actions:
            self._emit(
                RecoveryStartedPayload(action=action),
                correlation_id=action.action_id,
                producer="agent-2-safety",
            )
            self._apply_recovery(action)

    def _apply_recovery(self, action: RecoveryAction) -> None:
        if action.action_type is RecoveryActionType.TASK_MIGRATION:
            task_id = action.affected_task_ids[0] if action.affected_task_ids else None
            task = self.tasks.get(task_id) if task_id else None
            if task is not None:
                self._reassign(task, action.target_robot_ids[0], action.reason)
                self.counters.resolved_deadlocks += 1
            return
        if action.action_type is RecoveryActionType.REPLAN:
            robot_id = action.target_robot_ids[0]
            state = self.robots.get(robot_id)
            if state is None or state.current_task_id is None:
                return
            task = self.tasks.get(state.current_task_id)
            if task is None or task.status is TaskStatus.COMPLETED:
                return
            route = self._plan_for(state, task, reason=action.reason)
            if route is not None:
                self.counters.resolved_deadlocks += 1

    # ------------------------------------------------------------------
    # task lifecycle
    # ------------------------------------------------------------------
    def _reassign(self, task: Task, previous_robot_id: str, reason: str) -> None:
        """Move a task to the best remaining candidate robot.

        The task's own record is treated as authoritative about who holds it
        rather than trusting the caller. A task can be migrated twice in one
        tick — a deadlock recovery and a low-battery hand-off, say — and the
        second caller would otherwise release the robot it thinks held the work
        while the real holder kept it, leaving two robots on one task.

        Works with or without the coordinator: with it, the migration runs
        through the same peer negotiation as a fresh assignment; without it, the
        remaining robots claim the work locally.
        """

        holder = task.assigned_robot_id or previous_robot_id
        previous = self.robots.get(holder)
        if previous is not None:
            previous.current_task_id = None
            previous.task_started_at_s = None
            previous.service_remaining_s = 0.0
            previous.service_total_s = 0.0
            previous.workload = max(0, previous.workload - 1)
            if previous.status is RobotStatus.ACTIVE:
                previous.status = RobotStatus.IDLE
            self.routes.pop(holder, None)
            self.occupancy.clear_robot(holder)
        recovering = task.model_copy(
            update={"status": TaskStatus.RECOVERY, "assigned_robot_id": None}
        )
        self.tasks[task.task_id] = recovering
        if self.controller_available:
            self._negotiate_and_assign(recovering)
        else:
            self._locally_claim(recovering)

        new_robot_id = self.tasks[task.task_id].assigned_robot_id
        if new_robot_id is None:
            self.tasks[task.task_id] = self.tasks[task.task_id].model_copy(
                update={"status": TaskStatus.PENDING}
            )
            return
        self._release_task_holder(task.task_id, keep_robot_id=new_robot_id)
        self.counters.task_reassignments += 1
        self._emit(
            TaskReassignedPayload(
                task_id=task.task_id,
                previous_robot_id=holder,
                new_robot_id=new_robot_id,
                reason=reason[:500],
            ),
            correlation_id=task.task_id,
            producer="agent-2-runtime",
        )

    def _release_task_holder(self, task_id: str, *, keep_robot_id: str | None) -> None:
        """Guarantee exactly one robot holds a task.

        Cheap enough to run on every reassignment and it makes the invariant
        hold by construction rather than by every caller remembering to clear
        the right robot.
        """

        for state in self.robots.values():
            if state.current_task_id == task_id and state.robot_id != keep_robot_id:
                state.current_task_id = None
                state.task_started_at_s = None
                state.service_remaining_s = 0.0
                state.service_total_s = 0.0
                state.workload = max(0, state.workload - 1)
                if state.status is RobotStatus.ACTIVE:
                    state.status = RobotStatus.IDLE
                self.routes.pop(state.robot_id, None)
                self.occupancy.clear_robot(state.robot_id)

    def _release_task(self, state: RobotState, *, reason: str) -> None:
        task = self.tasks.get(state.current_task_id) if state.current_task_id else None
        state.current_task_id = None
        state.task_started_at_s = None
        state.service_remaining_s = 0.0
        state.service_total_s = 0.0
        state.workload = max(0, state.workload - 1)
        self.routes.pop(state.robot_id, None)
        self.occupancy.clear_robot(state.robot_id)
        if task is not None and task.status is not TaskStatus.COMPLETED:
            self.tasks[task.task_id] = task.model_copy(
                update={"status": TaskStatus.PENDING, "assigned_robot_id": None}
            )

    def _task_priority(self, state: RobotState) -> int:
        task = self.tasks.get(state.current_task_id) if state.current_task_id else None
        return task.priority if task else 1

    def _append(self, event: EventEnvelope[EventPayload]) -> None:
        """Append an event produced elsewhere, keeping the sequence monotonic.

        Agent 1's event factory allocates sequences from this runtime's counter
        (it is seeded with ``sequence + 1``), so the envelope already carries
        the right number and must not be renumbered here.
        """

        self.events.append(event)
        self.counters.events_emitted += 1
        if event.sequence > self.sequence:
            self.sequence = event.sequence

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def apply_command(self, command: ControlCommandUnion) -> list[EventEnvelope[EventPayload]]:
        """Validate-and-apply one canonical command, returning any new events."""

        with self.lock:
            start = len(self.events)
            if command.command_type is CommandType.CREATE_TASK:
                self.tasks[command.task.task_id] = command.task
                self._emit(
                    TaskCreatedPayload(task=command.task),
                    correlation_id=command.task.task_id,
                    producer="command-adapter",
                )
            elif command.command_type is CommandType.PAUSE_SIMULATION:
                self.paused = True
            elif command.command_type is CommandType.RESUME_SIMULATION:
                self.paused = False
            elif command.command_type is CommandType.SET_SIMULATION_SPEED:
                self.speed_multiplier = command.multiplier
            elif command.command_type is CommandType.RESET_SIMULATION:
                self._build(self.config.with_seed(command.seed), sequence_floor=self.sequence)
            elif command.command_type is CommandType.INJECT_ROBOT_FAILURE:
                self.failures.inject_failure(
                    command.robot_id, command.failure, now_s=self.simulation_time_s
                )
            elif command.command_type is CommandType.INJECT_COMMUNICATION_LOSS:
                self.failures.inject_communication_loss(
                    command.robot_id,
                    now_s=self.simulation_time_s,
                    timeout_s=command.timeout_s,
                )
            elif command.command_type is CommandType.RESTORE_ROBOT:
                self.failures.restore(command.robot_id)
            created = list(self.events)[start:]
        self._notify()
        return created

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def robot_count(self) -> int:
        return len(self.robots)

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for state in self.robots.values():
            counts[state.status.value] = counts.get(state.status.value, 0) + 1
        return counts


def _local_assignment(task_id: str, robot_id: str, now_s: float):
    from backend.contracts.models import TaskAssignment

    return TaskAssignment(
        task_id=task_id,
        robot_id=robot_id,
        bid_id=f"local-claim-{task_id}-{robot_id}",
        assigned_at_s=round(now_s, 3),
        reason="claimed locally while the coordination service was unavailable",
    )


def _ahead_cell(position: Position2D, route: RoutePlan, index: WorldIndex) -> Cell:
    """The next cell along the route, used for reservation and wait edges."""

    for waypoint in route.waypoints[1:]:
        if hypot(waypoint.x - position.x, waypoint.y - position.y) > 1e-6:
            return index.cell_of(waypoint)
    return index.cell_of(position)


def _remaining_distance(route: RoutePlan, position: Position2D) -> float:
    return route_length_m((position, *route.waypoints))
