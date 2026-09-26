"""Composition root: the only place Agent 1 and Agent 2 are wired together.

Subsystems never import each other's internals. ``backend.negotiation`` does not
know that ``backend.simulation`` exists, and ``backend.simulation`` does not know
that negotiation exists. This module owns the wiring, and it talks to both sides
only through canonical contracts and events.

How the two agents talk
-----------------------

```text
CREATE_TASK
    -> the runtime registers the task and publishes TASK_CREATED
    -> the coordinator runs an Agent 1 negotiation over the runtime's robots
    -> Agent 1 publishes NEGOTIATION_STARTED / BID_SUBMITTED / TASK_ASSIGNED
    -> the coordinator hands TASK_ASSIGNED to the runtime
    -> the runtime plans an Agent 2 route and runs the fleet safety pass
    -> the runtime publishes ROUTE_REQUESTED / ROUTE_PLANNED /
       CONFLICT_DETECTED / RECOVERY_STARTED / DEADLOCK_DETECTED /
       BATTERY_LOW / ROBOT_FAILED / COMMUNICATION_LOST / TASK_COMPLETED
    -> the coordinator hands BATTERY_LOW / ROBOT_FAILED / COMMUNICATION_LOST to
       Agent 1's ReassignmentService
    -> Agent 1 publishes TASK_REASSIGNED, which the runtime turns into a new
       route for the replacement robot
```

Neither side calls the other's decision methods. The coordinator reacts to
events, and both sides exchange only frozen contract objects. The runtime's
``InMemoryEventStream`` is the single sequence authority for the whole system,
so Agent 1 and Agent 2 events share one monotonic numbering.

The coordinator contains no negotiation, planning, or safety algorithm. It owns
sequencing, correlation IDs, and this event pump.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

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
    CommunicationLostPayload,
    EventEnvelope,
    EventPayload,
    RobotFailedPayload,
)
from backend.contracts.models import (
    NegotiationOutcome,
    NegotiationStatus,
    SimulationSnapshot,
    SystemMetrics,
    Task,
    TaskStatus,
)
from backend.negotiation.engine import DefaultNegotiationEngine
from backend.negotiation.events import DecisionEventContext
from backend.negotiation.reassignment import ReassignmentService
from backend.safety.battery import BatteryPolicy
from backend.simulation.runtime import InMemoryEventStream, SimulationRuntime
from backend.simulation.scenarios import build_scenario_fleet, spec_for as scenario_spec
from backend.simulation.world import FleetBlueprint, build_fleet

__all__ = [
    "REASSIGNMENT_TRIGGER_TYPES",
    "Agent1Decision",
    "FleetCoordinator",
    "ReassignmentOutcome",
    "build_coordinator",
    "build_demo_coordinator",
    "empty_outcome",
    "get_coordinator",
    "reset_coordinator",
]

#: Agent 2 event payloads that Agent 1's ReassignmentService consumes.
REASSIGNMENT_TRIGGER_TYPES: tuple[type[EventPayload], ...] = (
    BatteryLowPayload,
    CommunicationLostPayload,
    RobotFailedPayload,
)


@dataclass(frozen=True, slots=True)
class Agent1Decision:
    """One Agent 1 negotiation round and its canonical events."""

    events: tuple[EventEnvelope[EventPayload], ...]
    outcome: NegotiationOutcome
    latency_ms: float

    @property
    def assigned_robot_id(self) -> str | None:
        if self.outcome.assignment is None:
            return None
        return self.outcome.assignment.robot_id

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(event.event_type.value for event in self.events)


@dataclass(frozen=True, slots=True)
class ReassignmentOutcome:
    """Agent 1 reassignment result, empty when no replacement was found."""

    events: tuple[EventEnvelope[EventPayload], ...]
    outcome: NegotiationOutcome | None = None

    @property
    def new_robot_id(self) -> str | None:
        if self.outcome is None or self.outcome.assignment is None:
            return None
        return self.outcome.assignment.robot_id

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(event.event_type.value for event in self.events)


class FleetCoordinator:
    """Wire Agent 1 negotiation/allocation to Agent 2 world/movement/safety.

    Deterministic for a deterministic runtime: every event timestamp is
    simulation time. Wall-clock is measured only for the informational
    ``average_allocation_latency_ms`` metric.
    """

    def __init__(
        self,
        runtime: SimulationRuntime,
        *,
        engine: DefaultNegotiationEngine | None = None,
        reassignment: ReassignmentService | None = None,
        bid_validity_s: float = 5.0,
        enable_reassignment: bool = True,
        producer: str = "agent-1-negotiation",
    ) -> None:
        self._runtime = runtime
        self._engine = engine or DefaultNegotiationEngine(bid_validity_s=bid_validity_s)
        self._reassignment = reassignment or ReassignmentService(self._engine)
        self._enable_reassignment = enable_reassignment
        self._producer = producer
        self._handled_triggers: set[object] = set()
        self._correlation_counter = 0
        self._scenario_name = ""

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------

    @property
    def scenario_name(self) -> str:
        """Name of the loaded scenario, or an empty string for the default fleet."""

        return self._scenario_name

    def load_scenario(
        self,
        name: str,
        *,
        robot_count: int | None = None,
        columns: int | None = None,
        rows: int | None = None,
        seed: int | None = None,
        run_initial_tasks: bool = True,
    ) -> tuple[Task, ...]:
        """Rebuild the simulation from a named preset and return its tasks.

        Loading replaces the runtime wholesale, which is what makes a preset
        reproducible: the same preset with the same seed always produces the same
        world, the same fleet, and the same tasks. The previous run's events are
        discarded with the old stream, so the dashboard's cursor stays valid.
        """

        spec = scenario_spec(
            name,
            robot_count=robot_count,
            columns=columns,
            rows=rows,
            seed=seed,
        )
        blueprint, tasks = build_scenario_fleet(spec)
        self._runtime = SimulationRuntime(
            blueprint,
            battery_policy=self._runtime.battery_manager.policy,
            communication_timeout_s=self._runtime.communication_timeout_s,
            tick_rate_hz=self._runtime.tick_rate_hz,
        )
        self._scenario_name = spec.name
        self.reset_counters()
        if spec.speed_multiplier != 1.0:
            self._runtime.clock.set_speed(spec.speed_multiplier)
        if run_initial_tasks:
            for task in tasks:
                self._runtime.submit_task(task)
        return tasks

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def runtime(self) -> SimulationRuntime:
        return self._runtime

    @property
    def event_stream(self) -> InMemoryEventStream:
        return self._runtime.event_stream

    def snapshot(self) -> SimulationSnapshot:
        return self._runtime.snapshot()

    def metrics(self) -> SystemMetrics:
        return self._runtime.metrics()

    def events_after(self, sequence: int = 0) -> tuple[EventEnvelope[EventPayload], ...]:
        """Return every canonical event produced so far, in sequence order."""

        return self._runtime.event_stream.subscribe(sequence)

    def event_types_after(self, sequence: int = 0) -> tuple[str, ...]:
        return tuple(
            event.event_type.value for event in self.events_after(sequence)
        )

    # ------------------------------------------------------------------
    # Command intake
    # ------------------------------------------------------------------

    def submit_command(
        self, command: ControlCommand
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Apply a canonical command and run the resulting event cascade.

        A ``CREATE_TASK`` therefore does more than register a task: it also
        triggers negotiation, allocation, and -- through the assignment -- Agent 2
        route planning and a fleet safety pass. Events are returned in sequence
        order.
        """

        if not isinstance(command, ControlCommand):
            raise TypeError("submit_command requires a ControlCommand instance")

        if isinstance(command, CreateTaskCommand):
            return self.create_task(command.task)
        if isinstance(
            command,
            (
                InjectRobotFailureCommand,
                InjectCommunicationLossCommand,
                RestoreRobotCommand,
            ),
        ):
            return self._react(self._runtime.apply_command(command))
        if isinstance(
            command,
            (
                PauseSimulationCommand,
                ResumeSimulationCommand,
                SetSimulationSpeedCommand,
                ResetSimulationCommand,
            ),
        ):
            if isinstance(command, ResetSimulationCommand):
                self.reset_counters()
            return self._runtime.apply_command(command)
        raise TypeError(f"unsupported command {type(command).__name__}")

    def reset_counters(self) -> None:
        """Forget processed triggers and correlation counters after a reset."""

        self._handled_triggers.clear()
        self._correlation_counter = 0

    def create_task(self, task: Task) -> tuple[EventEnvelope[EventPayload], ...]:
        """Create a task, then negotiate, assign, and plan for it."""

        created = self._runtime.submit_task(task)
        decision = self._negotiate(task)
        planned = self._absorb(decision.events)
        return (*created, *decision.events, *planned)

    def negotiate_task(self, task: Task) -> Agent1Decision:
        """Run an Agent 1 negotiation for an already registered task."""

        return self._negotiate(task)

    def dispatch_pending_tasks(self) -> tuple[EventEnvelope[EventPayload], ...]:
        """Negotiate and assign every task that is still waiting for an owner.

        Tasks are registered up front by a scenario, so something has to kick
        the round off. Running them together is also how the demo gets the whole
        fleet moving in one action, and it is the same code path a single task
        takes, so nothing is special-cased for the UI.
        """

        produced: list[EventEnvelope[EventPayload]] = []
        for task in self._runtime.tasks():
            if task.status is not TaskStatus.PENDING:
                continue
            decision = self._negotiate(task)
            produced.extend(decision.events)
            produced.extend(self._absorb(decision.events))
        return tuple(produced)

    def randomize_assignments(
        self, jitter: float = 0.6, seed: int | None = None
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Re-run every open task's negotiation with randomised bid costs.

        This is the console's random-allocation control, and it is a real
        allocation round rather than a UI shuffle: bid randomisation is enabled
        on the negotiation engine, every task that is not finished has its
        current owner released, and each one is then negotiated again through
        the normal path. Allocation still picks the cheapest bid, so a
        different robot wins because it genuinely bid less this time.

        Completed and cancelled tasks are left alone: their outcome is already
        committed history. ``jitter`` goes straight to the scorer, which keeps
        every cost a valid lower-is-better value. ``seed`` makes the round
        reproducible, so the same seed yields the same winners.
        """

        self._engine.set_bid_jitter(jitter, seed)
        released = self._runtime.release_open_assignments()
        produced: list[EventEnvelope[EventPayload]] = list(released)
        for event in released:
            produced.extend(self._absorb((event,)))
        for task in self._runtime.tasks():
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                continue
            decision = self._negotiate(task)
            produced.extend(decision.events)
            produced.extend(self._absorb(decision.events))
        return tuple(produced)

    def set_bid_randomisation(self, enabled: bool, jitter: float = 0.6) -> None:
        """Turn bid randomisation on or off for later rounds."""

        self._engine.set_bid_jitter(jitter if enabled else 0.0)

    def assign_task_to_robot(
        self, task_id: str, robot_id: str
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Bind one task to one chosen robot.

        This is the console's manual assignment control. It runs the same
        planning path an allocated task takes -- route request, route plan or
        replan, then the safety pass -- so a hand-picked owner is held to the
        same collision and battery rules as an allocated one. It only overrides
        *who* takes the task, never whether the move is safe.

        A task that already has an owner is migrated: the previous robot loses
        its route and goes back to being idle, and the canonical
        ``TASK_REASSIGNED`` event records the handover.
        """

        task = self._runtime.require_task(task_id)
        previous = task.assigned_robot_id
        if previous is not None and previous != robot_id:
            self._runtime.release_task_from_robot(task_id, previous)
        events = list(self._runtime.assign_task(task_id, robot_id))
        if previous is not None and previous != robot_id:
            # Reassignment is a coordination decision, so the canonical
            # ``TASK_REASSIGNED`` event is built by the decision layer. Neither
            # this composition root nor the Agent 2 runtime constructs payloads.
            handover = self._reassignment.publish_manual_handover(
                task_id=task_id,
                previous_robot_id=previous,
                new_robot_id=robot_id,
                reason="operator assigned this task by hand",
                observed_at_s=self._runtime.now_s,
                event_context=self._event_context(task_id),
            )
            # The handover is published into the stream by `_absorb`; it is also
            # returned so the caller sees which events the command caused.
            events.extend(self._absorb((handover,)))
            events.append(handover)
        return tuple(events)

    # ------------------------------------------------------------------
    # Simulation driving
    # ------------------------------------------------------------------

    def advance(self, ticks: int = 1) -> tuple[EventEnvelope[EventPayload], ...]:
        """Advance the simulation by ``ticks`` and process every event."""

        return self._react(self._runtime.run_ticks(ticks))

    def step(self, delta_s: float | None = None) -> tuple[EventEnvelope[EventPayload], ...]:
        """Advance one tick (or ``delta_s``) and process every event."""

        return self._react(self._runtime.step(delta_s))

    # ------------------------------------------------------------------
    # Agent 1 <-> Agent 2 event pump
    # ------------------------------------------------------------------

    def _react(
        self, events: Sequence[EventEnvelope[EventPayload]]
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Handle Agent 2 events and run any reassignment they trigger."""

        produced: list[EventEnvelope[EventPayload]] = list(events)
        if not self._enable_reassignment:
            return tuple(produced)
        trigger = self._next_reassignment_trigger()
        if trigger is None:
            return tuple(produced)
        outcome = self._reassign(trigger)
        produced.extend(outcome.events)
        produced.extend(self._absorb(outcome.events))
        return tuple(produced)

    def _absorb(
        self, events: Sequence[EventEnvelope[EventPayload]]
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        """Publish Agent 1 events into the stream and feed them to the runtime."""

        produced: list[EventEnvelope[EventPayload]] = []
        for event in events:
            self._runtime.event_stream.publish_nowait(event)
            produced.extend(self._runtime.handle_event(event))
        return tuple(produced)

    def _next_reassignment_trigger(
        self,
    ) -> EventEnvelope[EventPayload] | None:
        """Return the oldest unhandled Agent 2 safety trigger, if any."""

        for event in self._runtime.event_stream.subscribe(0):
            if not isinstance(event.payload, REASSIGNMENT_TRIGGER_TYPES):
                continue
            if event.event_id in self._handled_triggers:
                continue
            self._handled_triggers.add(event.event_id)
            return event
        return None

    def _reassign(
        self, trigger: EventEnvelope[EventPayload]
    ) -> ReassignmentOutcome:
        """Ask Agent 1 to find a replacement for a faulted or drained robot.

        When the faulted robot holds no live task there is nothing to reassign,
        and the safety observation stands on its own.
        """

        robot_id = trigger.payload.robot_id
        task = self._assigned_task_for(robot_id)
        if task is None:
            return ReassignmentOutcome(())
        decision = self._reassignment.reassign_sync(
            task=task,
            robots=self._runtime.robots(),
            observed_at_s=max(self._runtime.now_s, trigger.occurred_at_s),
            trigger=trigger,
            event_context=self._event_context(task.task_id),
        )
        return ReassignmentOutcome(events=decision.events, outcome=decision.outcome)

    def _assigned_task_for(self, robot_id: str) -> Task | None:
        for task in self._runtime.tasks():
            if task.assigned_robot_id != robot_id:
                continue
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                continue
            return task
        return None

    def _negotiate(self, task: Task) -> Agent1Decision:
        """Run one Agent 1 negotiation round for a task."""

        started = perf_counter()
        decision = self._engine.decide_sync(
            task,
            self._runtime.robots(),
            self._runtime.now_s,
            self._event_context(task.task_id),
        )
        self._runtime.record_allocation_latency_ms(
            (perf_counter() - started) * 1000.0
        )
        return Agent1Decision(
            events=decision.events,
            outcome=decision.outcome,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    def _event_context(self, task_id: str) -> DecisionEventContext:
        self._correlation_counter += 1
        return DecisionEventContext(
            correlation_id=f"task-{task_id}",
            first_sequence=self._runtime.event_stream.last_sequence + 1,
            producer=self._producer,
            event_id_factory=lambda: uuid4(),
        )


def build_coordinator(
    blueprint: FleetBlueprint | None = None,
    *,
    robot_count: int = 5,
    battery_policy: BatteryPolicy | None = None,
    tick_rate_hz: float = 10.0,
    enable_reassignment: bool = True,
) -> FleetCoordinator:
    """Build a coordinator over a deterministic fleet.

    Pass a ``blueprint`` to control the world and fleet exactly (used by the
    integration and stress tests); otherwise a default demo fleet is built.
    """

    if blueprint is None:
        blueprint = build_fleet(robot_count=robot_count)
    runtime = SimulationRuntime(
        blueprint,
        battery_policy=battery_policy,
        tick_rate_hz=tick_rate_hz,
    )
    return FleetCoordinator(runtime, enable_reassignment=enable_reassignment)


def build_demo_coordinator() -> FleetCoordinator:
    """Build the coordinator used by the HTTP layer and the demo.

    The demo starts on the default ``normal`` preset: 10 mixed-size robots with
    four different speeds on the designed 40x25 warehouse, with its tasks
    already registered. That means opening the dashboard shows a running fleet
    immediately, rather than an empty world someone has to configure first.
    """

    coordinator = build_coordinator(robot_count=10)
    coordinator.load_scenario("normal")
    return coordinator


_COORDINATOR: FleetCoordinator | None = None


def get_coordinator() -> FleetCoordinator:
    """Return the process-wide coordinator, creating it on first use.

    The HTTP layer resolves its dependency through this function so the whole
    process shares one simulation. Tests that need an isolated simulation should
    call :func:`build_coordinator` directly and override the dependency instead
    of mutating this global.
    """

    global _COORDINATOR
    if _COORDINATOR is None:
        _COORDINATOR = build_demo_coordinator()
    return _COORDINATOR


def reset_coordinator() -> None:
    """Discard the process-wide coordinator, e.g. between test cases."""

    global _COORDINATOR
    _COORDINATOR = None


def empty_outcome(task_id: str, observed_at_s: float) -> NegotiationOutcome:
    """Return a canonical unassigned outcome for reporting."""

    return NegotiationOutcome(
        task_id=task_id,
        status=NegotiationStatus.UNASSIGNED,
        started_at_s=observed_at_s,
        completed_at_s=observed_at_s,
    )
