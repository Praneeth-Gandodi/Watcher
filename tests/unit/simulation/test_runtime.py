"""Simulation clock, runtime movement, event stream, and projections."""

from __future__ import annotations

import pytest
from backend.contracts.commands import (
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
    EventType,
    RoutePlannedPayload,
    TaskAssignedPayload,
    TaskCreatedPayload,
    TaskReassignedPayload,
)
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    Position2D,
    RobotStatus,
    RouteStatus,
    TaskStatus,
)
from backend.safety.right_of_way import find_earliest_conflict
from backend.simulation.grid import cell_to_world_position
from backend.simulation.runtime import (
    EVENT_PRODUCER,
    InMemoryEventStream,
    SimulationClock,
    SimulationRuntime,
    fleets_conflict_free,
)
from backend.simulation.world import build_fleet
from tests.unit.safety.conftest import make_blueprint

from uuid import uuid4


def task(
    task_id: str = "task-001",
    cell: tuple[int, int] = (8, 2),
    priority: int = 3,
) -> object:
    from backend.contracts.models import Task

    return Task(
        task_id=task_id,
        target=cell_to_world_position(cell, 1.0),
        priority=priority,
        required_capabilities=(),
        estimated_duration_s=20.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def crossing_runtime(
    battery_percent: float = 80.0,
) -> SimulationRuntime:
    """Two 1x1 robots on a 10x8 world whose routes cross at (2, 2)."""

    obstacles = tuple((5, row) for row in range(8) if row != 2)
    blueprint = make_blueprint(
        obstacles=obstacles,
        robots=(
            ("robot-001", (1, 2), battery_percent),
            ("robot-002", (2, 0), battery_percent),
        ),
        profiles=(("robot-001", 1, 1, 1.0), ("robot-002", 1, 1, 1.0)),
        columns=10,
        rows=8,
    )
    return SimulationRuntime(blueprint, tick_rate_hz=10.0)


# ----------------------------------------------------------------------
# clock
# ----------------------------------------------------------------------


def test_the_clock_only_moves_with_simulation_time() -> None:
    clock = SimulationClock()

    assert clock.now_s == 0.0
    assert clock.advance(0.5) == 0.5
    assert clock.now_s == 0.5
    clock.pause()
    assert clock.paused is True
    assert clock.advance(10.0) == 0.0, "a paused clock does not advance"
    clock.resume()
    clock.set_speed(2.0)
    assert clock.advance(1.0) == 2.0
    clock.reset()
    assert (clock.now_s, clock.paused, clock.speed_multiplier) == (0.0, False, 1.0)


def test_the_clock_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        SimulationClock(-1.0)
    clock = SimulationClock()
    with pytest.raises(ValueError):
        clock.advance(-1.0)
    with pytest.raises(ValueError):
        clock.set_speed(0.0)
    with pytest.raises(ValueError):
        clock.set_speed(float("nan"))


# ----------------------------------------------------------------------
# event stream
# ----------------------------------------------------------------------


def event(sequence: int) -> object:
    from backend.contracts.events import EventEnvelope
    from backend.contracts.events import TaskCreatedPayload
    from backend.contracts.models import Task

    payload = TaskCreatedPayload(
        task=Task(
            task_id=f"task-{sequence:03d}",
            target=Position2D(x=1.0, y=1.0),
            priority=1,
            required_capabilities=(),
            estimated_duration_s=1.0,
            status=TaskStatus.PENDING,
            assigned_robot_id=None,
            created_at_s=0.0,
        )
    )
    return EventEnvelope(
        event_id=uuid4(),
        sequence=sequence,
        producer="test",
        correlation_id="test",
        occurred_at_s=0.0,
        event_type=EventType.TASK_CREATED,
        payload=payload,
    )


def test_the_event_stream_sequences_and_caps_itself() -> None:
    stream = InMemoryEventStream(max_events=3)

    assert stream.next_sequence() == 1
    for sequence in (1, 2, 3):
        stream.publish_nowait(event(sequence))

    assert stream.last_sequence == 3
    assert len(stream) == 3
    assert [item.sequence for item in stream.subscribe(0)] == [1, 2, 3]
    assert [item.sequence for item in stream.subscribe(2)] == [3]

    stream.publish_nowait(event(4))
    assert [item.sequence for item in stream.subscribe(0)] == [2, 3, 4]
    with pytest.raises(ValueError):
        stream.publish_nowait(event(1))
    with pytest.raises(ValueError):
        stream.subscribe(-1)
    stream.clear()
    assert (stream.last_sequence, len(stream)) == (0, 0)
    with pytest.raises(ValueError):
        InMemoryEventStream(max_events=0)


# ----------------------------------------------------------------------
# task intake and routing
# ----------------------------------------------------------------------


def test_submitting_a_task_publishes_task_created() -> None:
    runtime = crossing_runtime()

    events = runtime.submit_task(task())

    assert [item.event_type for item in events] == [EventType.TASK_CREATED]
    assert events[0].producer == EVENT_PRODUCER
    assert events[0].sequence == 1
    assert isinstance(events[0].payload, TaskCreatedPayload)
    with pytest.raises(ValueError, match="already exists"):
        runtime.submit_task(task())
    with pytest.raises(TypeError):
        runtime.submit_task("task-001")  # type: ignore[arg-type]


def test_assigning_a_task_plans_a_route_and_a_trajectory() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())

    events = runtime.assign_task("task-001", "robot-001")

    assert [item.event_type for item in events] == [
        EventType.ROUTE_REQUESTED,
        EventType.ROUTE_PLANNED,
    ]
    state = runtime.robot_states()[0]
    assert state.route is not None
    assert state.route.status is RouteStatus.ACTIVE
    assert state.route.waypoints[0] == cell_to_world_position((1, 2), 1.0)
    assert state.route.waypoints[-1] == cell_to_world_position((8, 2), 1.0)
    assert state.trajectory[0].cell == (1, 2)
    # The trajectory is derived from the route's own waypoints.
    assert [point.cell for point in state.trajectory] == [
        runtime.grid.cell_for_position(point) for point in state.route.waypoints
    ]
    # The robot is bound to the task immediately, so it cannot win another one.
    assert state.robot.status is RobotStatus.ACTIVE
    assert state.robot.current_task_id == "task-001"
    assert runtime.task("task-001").status is TaskStatus.ASSIGNED
    assert isinstance(events[1].payload, RoutePlannedPayload)


def test_assigning_requires_a_known_task_and_robot() -> None:
    runtime = crossing_runtime()

    with pytest.raises(KeyError):
        runtime.assign_task("task-404", "robot-001")
    with pytest.raises(KeyError):
        runtime.assign_task("task-001", "robot-999")


def test_a_failed_robot_cannot_be_assigned_work() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.inject_failure(
        "robot-001",
        FailureInfo(kind=FailureKind.ACTUATOR, code="drive-failure", detected_at_s=0.0),
    )

    with pytest.raises(ValueError, match="is failed"):
        runtime.assign_task("task-001", "robot-001")


def test_an_unroutable_task_is_reported_instead_of_stalling() -> None:
    # A 3x2 robot cannot pass the one-cell gap in the wall at column 5.
    blueprint = make_blueprint(
        obstacles=tuple((5, row) for row in range(8) if row != 2),
        robots=(("robot-001", (1, 2), 80.0),),
        profiles=(("robot-001", 3, 2, 1.0),),
        columns=10,
        rows=8,
    )
    runtime = SimulationRuntime(blueprint)
    runtime.submit_task(task())

    events = runtime.assign_task("task-001", "robot-001")

    assert [item.event_type for item in events] == [EventType.ROUTE_REQUESTED]
    state = runtime.robot_states()[0]
    assert state.trajectory == ()
    assert state.route is not None and state.route.status is RouteStatus.INVALID
    assert state.robot.status is RobotStatus.BLOCKED
    assert runtime.task("task-001").status is TaskStatus.BLOCKED
    # An unroutable task is not a two-robot conflict, so the conflict list and
    # its metric stay an accurate count.
    assert runtime.snapshot().conflicts == ()
    assert runtime.snapshot().metrics.open_conflicts == 0


# ----------------------------------------------------------------------
# movement, battery, task completion
# ----------------------------------------------------------------------


def test_a_robot_moves_along_its_trajectory_and_completes_its_task() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")

    events = runtime.run_ticks(200)

    assert EventType.TASK_COMPLETED in {item.event_type for item in events}
    state = runtime.robot_states()[0]
    assert state.current_cell == (8, 2)
    assert runtime.grid.cell_for_position(state.robot.position) == (8, 2)
    assert state.robot.status is RobotStatus.IDLE
    assert state.robot.current_task_id is None
    assert runtime.task("task-001").status is TaskStatus.COMPLETED
    assert state.route is not None and state.route.status is RouteStatus.COMPLETED
    assert state.is_parked is True


def test_movement_lowers_battery_once_per_cell() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    start_battery = runtime.robot("robot-001").battery_percent

    runtime.run_ticks(30)  # 3.0 s at 1 s per cell

    state = runtime.robot_states()[0]
    assert state.cells_travelled == 3
    assert state.robot.battery_percent == start_battery - 3.0


def test_a_low_battery_event_is_published_once_per_threshold_crossing() -> None:
    # Start just above the low threshold so the first cell tips the robot over.
    runtime = crossing_runtime(battery_percent=21.0)
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")

    first = runtime.run_ticks(10)
    second = runtime.run_ticks(10)

    low_events = [
        item
        for item in (*first, *second)
        if item.event_type is EventType.BATTERY_LOW
    ]
    assert len(low_events) == 1
    assert low_events[0].payload.robot_id == "robot-001"
    assert low_events[0].payload.battery_percent <= 20.0
    assert low_events[0].payload.threshold_percent == 20.0
    assert low_events[0].payload.estimated_range_m >= 0.0


def test_a_robot_never_leaves_the_world_or_enters_an_obstacle() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")

    for _ in range(300):
        runtime.step()
        for state in runtime.robot_states():
            cell = runtime.grid.cell_for_position(state.robot.position)
            assert cell == state.current_cell
            assert runtime.grid.contains(*cell)
            assert runtime.grid.footprint_is_free(
                cell, state.profile.width_cells, state.profile.height_cells
            )


# ----------------------------------------------------------------------
# safety
# ----------------------------------------------------------------------


def test_a_crossing_pair_is_detected_and_resolved_by_a_timing_delay() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task("task-001", (8, 2), priority=4))
    runtime.assign_task("task-001", "robot-001")
    # The target is past the crossing cell, so the yielding robot can clear the
    # shared cell instead of parking on it forever.
    runtime.submit_task(task("task-002", (2, 4), priority=2))
    runtime.assign_task("task-002", "robot-002")

    events = runtime.event_stream.subscribe(0)
    conflict_events = [
        item for item in events if item.event_type is EventType.CONFLICT_DETECTED
    ]
    recovery_events = [
        item for item in events if item.event_type is EventType.RECOVERY_STARTED
    ]
    assert len(conflict_events) == 1
    # The canonical Conflict reports a world position, not raw cells.
    assert conflict_events[0].payload.conflict.position.x == 2.5
    assert conflict_events[0].payload.conflict.position.y == 2.5
    assert conflict_events[0].payload.conflict.robot_ids == (
        "robot-001",
        "robot-002",
    )
    assert len(recovery_events) == 1
    assert recovery_events[0].payload.action.action_type.value == "yield"
    assert recovery_events[0].payload.action.target_robot_ids == ("robot-002",)

    # The delay must outlast the leader's whole occupancy of the shared cell.
    yielding = runtime.robot_states()[1].trajectory
    assert yielding[1].cell == yielding[2].cell == (2, 1)
    assert yielding[2].timestamp_s > 2.0

    # The resolution is validated against the whole fleet.
    assert fleets_conflict_free(runtime) is True
    assert find_earliest_conflict(runtime.active_trajectories()) is None


def test_a_parked_robot_makes_a_conflict_permanent_and_blocks() -> None:
    """A finished robot holding a cell another must cross is unresolvable."""

    runtime = crossing_runtime()
    runtime.submit_task(task("task-001", (8, 2), priority=4))
    runtime.assign_task("task-001", "robot-001")
    # Targeting the crossing cell itself means robot-002 parks on (2, 2).
    runtime.submit_task(task("task-002", (2, 2), priority=1))
    events = runtime.assign_task("task-002", "robot-002")

    assert [item.event_type for item in events][:3] == [
        EventType.ROUTE_REQUESTED,
        EventType.ROUTE_PLANNED,
        EventType.CONFLICT_DETECTED,
    ]
    # No right-of-way delay was published, because no delay can clear the cell.
    assert not [
        item
        for item in events
        if item.event_type is EventType.RECOVERY_STARTED
        and "delays departure" in item.payload.action.reason
    ]
    assert fleets_conflict_free(runtime) is False
    conflict = next(
        item
        for item in runtime.event_stream.subscribe(0)
        if item.event_type is EventType.CONFLICT_DETECTED
    )
    assert conflict.payload.conflict.position.x == 2.5
    assert conflict.payload.conflict.status.value == "open"
    blocked = {
        state.robot_id
        for state in runtime.robot_states()
        if state.robot.status is RobotStatus.BLOCKED
    }
    assert blocked == {"robot-001", "robot-002"}
    assert runtime.snapshot().metrics.open_conflicts == 1


def test_safety_is_event_driven_and_not_re_evaluated_every_second() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    cursor = runtime.event_stream.last_sequence

    # 200 idle ticks with nothing to re-evaluate produce no safety events.
    runtime.run_ticks(200)

    late_events = runtime.event_stream.subscribe(cursor)
    assert [item for item in late_events if item.event_type is EventType.CONFLICT_DETECTED] == []
    assert runtime.revision >= 1


def test_a_head_on_pair_is_reported_as_unresolved_and_blocks() -> None:
    """A timing delay cannot fix two robots walking into each other."""

    blueprint = make_blueprint(
        obstacles=(),
        robots=(("robot-001", (0, 3), 80.0), ("robot-002", (3, 3), 80.0)),
        profiles=(("robot-001", 1, 1, 1.0), ("robot-002", 1, 1, 1.0)),
        columns=8,
        rows=8,
    )
    runtime = SimulationRuntime(blueprint)
    runtime.submit_task(task("task-001", (3, 3), priority=4))
    runtime.assign_task("task-001", "robot-001")
    runtime.submit_task(task("task-002", (0, 3), priority=1))
    runtime.assign_task("task-002", "robot-002")

    events = runtime.event_stream.subscribe(0)
    assert len(
        [item for item in events if item.event_type is EventType.CONFLICT_DETECTED]
    ) == 1
    assert fleets_conflict_free(runtime) is False
    blocked = {
        state.robot_id
        for state in runtime.robot_states()
        if state.robot.status is RobotStatus.BLOCKED
    }
    assert blocked == {"robot-001", "robot-002"}
    # The conflict stays honestly open: the MVP cannot resolve a head-on.
    assert runtime.snapshot().metrics.open_conflicts == 1

    # A head-on is also cyclic waiting, so a deadlock is detected and reported.
    deadlocks = [
        item
        for item in events
        if item.event_type is EventType.DEADLOCK_DETECTED
    ]
    assert len(deadlocks) == 1
    assert sorted(deadlocks[0].payload.report.cycle_robot_ids) == [
        "robot-001",
        "robot-002",
    ]
    assert runtime.snapshot().metrics.detected_deadlocks == 1

    # Repeated safety passes do not re-report the same cycle.
    runtime.mark_safety_dirty()
    later = runtime.run_ticks(10)
    assert not [
        item
        for item in later
        if item.event_type is EventType.DEADLOCK_DETECTED
    ]


def test_the_safety_engine_protocol_is_available() -> None:
    import asyncio

    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    route = runtime.robot_states()[0].route

    decision = asyncio.run(runtime.safety_engine.evaluate_motion(route, 0.0))

    assert decision.allowed is True
    assert decision.route == route
    assert "no space-time conflict" in decision.reason

    with pytest.raises(TypeError):
        asyncio.run(runtime.safety_engine.evaluate_motion("route-001", 0.0))
    with pytest.raises(ValueError):
        asyncio.run(runtime.safety_engine.evaluate_motion(route, -1.0))


# ----------------------------------------------------------------------
# faults
# ----------------------------------------------------------------------


def test_a_failure_clears_the_route_and_reports_the_robot() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")

    events = runtime.inject_failure(
        "robot-001",
        FailureInfo(kind=FailureKind.ACTUATOR, code="drive-failure", detected_at_s=1.0),
    )

    assert events[0].event_type is EventType.ROBOT_FAILED
    state = runtime.robot_states()[0]
    assert state.robot.status is RobotStatus.FAILED
    assert state.robot.failure is not None
    assert state.trajectory == ()
    assert state.route is None


def test_communication_loss_is_distinct_from_failure_and_keeps_the_robot() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    runtime.run_ticks(20)

    events = runtime.inject_communication_loss("robot-001", timeout_s=3.0)

    assert events[0].event_type is EventType.COMMUNICATION_LOST
    state = runtime.robot_states()[0]
    assert state.robot.status is RobotStatus.ACTIVE, "the robot keeps working"
    assert state.robot.communication_state is CommunicationState.LOST
    assert state.robot.failure is None
    assert state.task_id == "task-001"
    assert state.cells_travelled > 0

    with pytest.raises(ValueError, match="not a communication-loss case"):
        runtime.inject_failure(
            "robot-001",
            FailureInfo(kind=FailureKind.OTHER, code="boom", detected_at_s=1.0),
        )
        runtime.inject_communication_loss("robot-001")


def test_restore_brings_a_failed_robot_back_online() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    runtime.inject_failure(
        "robot-001",
        FailureInfo(kind=FailureKind.ACTUATOR, code="drive-failure", detected_at_s=1.0),
    )

    runtime.restore("robot-001")

    robot = runtime.robot("robot-001")
    assert robot.status is RobotStatus.IDLE
    assert robot.failure is None
    assert robot.communication_state is CommunicationState.ONLINE
    assert runtime.restore("robot-001") == (), "restoring twice is a no-op"


def test_restore_also_recovers_a_lost_communication_link() -> None:
    runtime = crossing_runtime()
    runtime.inject_communication_loss("robot-001")

    runtime.restore("robot-001")

    assert runtime.robot("robot-001").communication_state is CommunicationState.ONLINE


# ----------------------------------------------------------------------
# the Agent 1 -> Agent 2 event boundary
# ----------------------------------------------------------------------


def assignment_event(task_id: str, robot_id: str, sequence: int) -> object:
    from backend.contracts.events import EventEnvelope
    from backend.contracts.models import TaskAssignment

    return EventEnvelope(
        event_id=uuid4(),
        sequence=sequence,
        producer="agent-1-negotiation",
        correlation_id=task_id,
        occurred_at_s=0.0,
        event_type=EventType.TASK_ASSIGNED,
        payload=TaskAssignedPayload(
            assignment=TaskAssignment(
                task_id=task_id,
                robot_id=robot_id,
                bid_id="bid-001",
                assigned_at_s=0.0,
                reason="lowest bid",
            )
        ),
    )


def reassignment_event(
    task_id: str, previous: str, new: str, sequence: int
) -> object:
    from backend.contracts.events import EventEnvelope

    return EventEnvelope(
        event_id=uuid4(),
        sequence=sequence,
        producer="agent-1-negotiation",
        correlation_id=task_id,
        occurred_at_s=1.0,
        event_type=EventType.TASK_REASSIGNED,
        payload=TaskReassignedPayload(
            task_id=task_id,
            previous_robot_id=previous,
            new_robot_id=new,
            reason="assigned robot failed",
        ),
    )


def test_a_task_assigned_event_plans_a_route() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())

    events = runtime.handle_event(assignment_event("task-001", "robot-001", 2))

    assert [item.event_type for item in events] == [
        EventType.ROUTE_REQUESTED,
        EventType.ROUTE_PLANNED,
    ]
    assert runtime.robot_states()[0].route is not None


def test_a_task_reassigned_event_moves_the_work_and_replans() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.handle_event(assignment_event("task-001", "robot-001", 2))

    events = runtime.handle_event(
        reassignment_event("task-001", "robot-001", "robot-002", 4)
    )

    # robot-002 never had a route, so this is its first plan, not a replan.
    assert [item.event_type for item in events] == [
        EventType.ROUTE_REQUESTED,
        EventType.ROUTE_PLANNED,
    ]
    assert runtime.robot("robot-001").current_task_id is None
    assert runtime.robot("robot-002").current_task_id == "task-001"
    assert runtime.routes()[0].robot_id == "robot-002"
    assert runtime.snapshot().metrics.task_reassignments == 1


def test_a_replan_by_the_same_robot_publishes_route_replanned() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    first_route = runtime.robot_states()[0].route

    # Re-plan the same pair: the route version must advance and the event type
    # must be ROUTE_REPLANNED, never a second ROUTE_PLANNED.
    events = runtime.assign_task("task-001", "robot-001")

    assert [item.event_type for item in events] == [
        EventType.ROUTE_REQUESTED,
        EventType.ROUTE_REPLANNED,
    ]
    assert runtime.robot_states()[0].route.version == first_route.version + 1


def test_an_unknown_event_type_is_ignored() -> None:
    runtime = crossing_runtime()

    assert runtime.handle_event(object()) == ()


# ----------------------------------------------------------------------
# commands and lifecycle
# ----------------------------------------------------------------------


def test_commands_are_applied() -> None:
    runtime = crossing_runtime()

    create = CreateTaskCommand(command_id=uuid4(), issued_at_s=0.0, task=task())
    assert runtime.apply_command(create)[0].event_type is EventType.TASK_CREATED

    assert runtime.apply_command(PauseSimulationCommand(command_id=uuid4(), issued_at_s=0.0)) == ()
    assert runtime.run_ticks(5) == ()
    runtime.apply_command(ResumeSimulationCommand(command_id=uuid4(), issued_at_s=0.0))
    assert runtime.clock.paused is False

    runtime.apply_command(SetSimulationSpeedCommand(command_id=uuid4(), issued_at_s=0.0, multiplier=4.0))
    assert runtime.clock.speed_multiplier == 4.0

    runtime.inject_failure(
        "robot-001",
        FailureInfo(kind=FailureKind.OTHER, code="boom", detected_at_s=0.0),
    )
    runtime.apply_command(RestoreRobotCommand(command_id=uuid4(), issued_at_s=0.0, robot_id="robot-001"))
    assert runtime.robot("robot-001").status is RobotStatus.IDLE

    runtime.inject_communication_loss(
        "robot-001",
    )
    loss = InjectCommunicationLossCommand(
        command_id=uuid4(), issued_at_s=0.0, robot_id="robot-001", timeout_s=5.0
    )
    assert runtime.apply_command(loss)[0].event_type is EventType.COMMUNICATION_LOST

    failure = InjectRobotFailureCommand(
        command_id=uuid4(),
        issued_at_s=0.0,
        robot_id="robot-001",
        failure=FailureInfo(kind=FailureKind.OTHER, code="boom", detected_at_s=0.0),
    )
    assert runtime.apply_command(failure)[0].event_type is EventType.ROBOT_FAILED

    runtime.apply_command(ResetSimulationCommand(command_id=uuid4(), issued_at_s=0.0, seed=1))
    assert runtime.now_s == 0.0
    assert runtime.tasks() == ()
    assert runtime.revision == 0
    assert runtime.event_stream.last_sequence == 0
    assert runtime.clock.speed_multiplier == 1.0

    with pytest.raises(TypeError):
        runtime.apply_command("pause")  # type: ignore[arg-type]


def test_reset_restores_the_initial_fleet() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    runtime.run_ticks(50)

    runtime.reset()

    for state in runtime.robot_states():
        assert state.robot.status is RobotStatus.IDLE
        assert state.robot.current_task_id is None
        assert state.route is None
        assert state.trajectory == ()
        assert state.cells_travelled == 0
        assert state.robot.position.x == 0.5 or state.robot.position.x > 0
    assert runtime.snapshot().metrics.open_conflicts == 0


def test_run_ticks_rejects_a_negative_count() -> None:
    runtime = crossing_runtime()

    with pytest.raises(ValueError):
        runtime.run_ticks(-1)


# ----------------------------------------------------------------------
# projections
# ----------------------------------------------------------------------


def test_the_snapshot_projects_the_whole_simulation_state() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    runtime.run_ticks(50)

    snapshot = runtime.snapshot()

    assert snapshot.world is runtime.world
    assert len(snapshot.robots) == 2
    assert len(snapshot.tasks) == 1
    assert len(snapshot.routes) == 1
    assert snapshot.controller_available is True
    assert snapshot.simulation_time_s == pytest.approx(5.0)
    assert snapshot.last_event_sequence >= snapshot.revision
    assert snapshot.last_event_sequence == runtime.event_stream.last_sequence


def test_metrics_are_valid_and_reflect_the_state() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")
    runtime.run_ticks(50)
    runtime.record_allocation_latency_ms(4.0)
    runtime.record_allocation_latency_ms(8.0)

    metrics = runtime.metrics()

    assert 0.0 <= metrics.average_battery_percent <= 100.0
    assert metrics.average_allocation_latency_ms == 6.0
    assert metrics.active_robots == 1
    assert metrics.failed_robots == 0
    assert metrics.completed_tasks == 0
    assert metrics.pending_tasks == 1
    assert metrics.open_conflicts == 0
    assert metrics.event_throughput_per_s >= 0.0
    assert metrics.extra_metrics["planned_routes"] == 1.0
    assert metrics.extra_metrics["emitted_events"] == float(
        runtime.event_stream.last_sequence
    )
    with pytest.raises(ValueError):
        runtime.record_allocation_latency_ms(-1.0)


def test_a_controller_outage_is_reported_separately_from_robot_execution() -> None:
    runtime = crossing_runtime()
    runtime.submit_task(task())
    runtime.assign_task("task-001", "robot-001")

    runtime.set_controller_available(False)
    runtime.run_ticks(20)

    assert runtime.controller_available is False
    # Robots keep executing their local work while coordination is down.
    assert runtime.robot_states()[0].cells_travelled > 0
    assert runtime.snapshot().metrics.controller_available is False


def test_a_default_demo_fleet_runs_without_errors() -> None:
    runtime = SimulationRuntime(build_fleet())

    assert len(runtime.robots()) == 5
    assert runtime.now_s == 0.0
    assert runtime.tick_rate_hz == 10.0
    assert runtime.tick_dt_s == 0.1
    assert runtime.event_stream.last_sequence == 0
    with pytest.raises(ValueError):
        SimulationRuntime(build_fleet(), tick_rate_hz=0.0)
    with pytest.raises(ValueError):
        SimulationRuntime(build_fleet(), communication_timeout_s=0.0)


def test_the_runtime_never_imports_agent_one_internals() -> None:
    """The dependency rule: Agent 2 does not reach into Agent 1."""

    import ast
    import inspect

    from backend.safety import battery as battery_module
    from backend.simulation import runtime as runtime_module

    for module in (runtime_module, battery_module):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(
            name.startswith(("backend.negotiation", "backend.allocation"))
            for name in imported
        ), f"{module.__name__} must not import Agent 1 internals"
