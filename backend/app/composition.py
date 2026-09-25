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
from backend.simulation.world import FleetBlueprint, build_fleet

__all__ = [
    "REASSIGNMENT_TRIGGER_TYPES",
    "Agent1Decision",
    "FleetCoordinator",
    "ReassignmentOutcome",
    "build_coordinator",
    "build_demo_coordinator",
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
    """Build the coordinator used by the HTTP layer and the demo."""

    return build_coordinator(robot_count=5)


def empty_outcome(task_id: str, observed_at_s: float) -> NegotiationOutcome:
    """Return a canonical unassigned outcome for reporting."""

    return NegotiationOutcome(
        task_id=task_id,
        status=NegotiationStatus.UNASSIGNED,
        started_at_s=observed_at_s,
        completed_at_s=observed_at_s,
    )
