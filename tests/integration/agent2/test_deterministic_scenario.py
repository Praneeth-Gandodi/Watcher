"""The required deterministic end-to-end scenario.

One scenario proves the whole Agent 1 + Agent 2 pipeline works together on a
single 10x8 world with five robots, four different speeds, three body shapes, a
wall with a single-cell gap, a genuine conflict, a battery-low observation, and
a robot failure that triggers reassignment.

Proven properties, in order:

1. a task is allocated by Agent 1
2. a route is planned by Agent 2 through the only gap in the wall
3. a space-time conflict is detected between two robots at different speeds
4. the conflict is resolved by a safe timing delay, and the fleet is then
   conflict free
5. the simulation keeps running and the world invariants hold every tick
6. battery drains as robots move and ``BATTERY_LOW`` is published once
7. a robot fault is published and Agent 1 reassigns the work
8. the final snapshot and metrics remain valid
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.composition import build_coordinator
from backend.contracts.commands import (
    InjectCommunicationLossCommand,
    InjectRobotFailureCommand,
    RestoreRobotCommand,
)
from backend.contracts.events import (
    BatteryLowPayload,
    CommunicationLostPayload,
    EventType,
    RobotFailedPayload,
    TaskReassignedPayload,
)
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    RobotCapability,
    RobotStatus,
    TaskStatus,
)
from backend.simulation.runtime import fleets_conflict_free
from tests.integration.agent2.conftest import (
    assert_world_invariants,
    five_robot_blueprint,
    task,
)


def build_scenario():
    """Create the scenario, run the four allocations, and return the board."""

    board = build_coordinator(five_robot_blueprint())
    board.create_task(task("task-001", (8, 3), priority=4))
    board.create_task(task("task-002", (7, 5), priority=1))
    board.create_task(
        task(
            "task-003",
            (2, 6),
            priority=2,
            required_capabilities=(RobotCapability.TUG,),
        )
    )
    board.create_task(
        task(
            "task-004",
            (1, 1),
            priority=2,
            required_capabilities=(RobotCapability.INSPECT,),
        )
    )
    return board


def test_the_scenario_fleet_is_heterogeneous() -> None:
    blueprint = five_robot_blueprint()

    assert len({p.width_cells for p in blueprint.profiles}) >= 2
    assert len({p.height_cells for p in blueprint.profiles}) >= 2
    assert len({(p.width_cells, p.height_cells) for p in blueprint.profiles}) == 4
    assert len({p.speed_mps for p in blueprint.profiles}) == 4
    assert len(blueprint.profiles) == 5
    assert blueprint.world.cells, "the scenario world has a wall"
    assert blueprint.occupancy_invariants_hold()


def test_allocation_planning_conflict_resolution_and_recovery() -> None:
    board = build_scenario()
    runtime = board.runtime

    # ---- 1. allocation -----------------------------------------------------
    assert runtime.task("task-001").assigned_robot_id == "robot-001"
    assert runtime.task("task-002").assigned_robot_id == "robot-002"
    assert runtime.task("task-003").assigned_robot_id == "robot-003"
    assert runtime.task("task-004").assigned_robot_id == "robot-004"
    assert {t.status for t in runtime.tasks()} == {TaskStatus.ASSIGNED}

    # ---- 2. route planning through the only gap ---------------------------
    for route in runtime.routes():
        assert route.status.value == "active"
        assert len(route.waypoints) >= 2
        for waypoint in route.waypoints:
            cell = runtime.grid.cell_for_position(waypoint)
            assert runtime.grid.footprint_is_free(
                cell,
                runtime.profiles.get(route.robot_id).width_cells,
                runtime.profiles.get(route.robot_id).height_cells,
            ), f"{route.route_id} planned through a blocked cell at {cell}"
    # Only the two 1x1 robots can use the single-cell gap at (5, 3).
    neck_users = {
        route.robot_id
        for route in runtime.routes()
        if any(
            runtime.grid.cell_for_position(waypoint) == (5, 3)
            for waypoint in route.waypoints
        )
    }
    assert neck_users == {"robot-001", "robot-002"}

    # ---- 3. a conflict is detected ----------------------------------------
    conflicts = [
        event
        for event in board.events_after()
        if event.event_type is EventType.CONFLICT_DETECTED
    ]
    assert len(conflicts) == 1
    conflict = conflicts[0].payload.conflict
    assert set(conflict.robot_ids) == {"robot-001", "robot-002"}
    assert conflict.kind.value == "right_of_way"
    assert conflict.severity.value == "warning"
    assert conflict.task_ids == ("task-001", "task-002")

    # ---- 4. the conflict is resolved by a timing delay --------------------
    recoveries = [
        event
        for event in board.events_after()
        if event.event_type is EventType.RECOVERY_STARTED
    ]
    assert len(recoveries) == 1
    action = recoveries[0].payload.action
    assert action.action_type.value == "yield"
    assert action.target_robot_ids == ("robot-002",), (
        "the lower-priority robot yields to the higher-priority one"
    )
    assert action.affected_task_ids == ("task-001", "task-002")
    assert "delays departure" in action.reason
    assert fleets_conflict_free(runtime) is True, (
        "no space-time conflict may remain after safety resolution"
    )

    # The delay is an explicit hold, not a stretched movement segment: the
    # yielding robot has two consecutive points on the same cell.
    yielding = next(
        state.trajectory
        for state in runtime.robot_states()
        if state.robot_id == "robot-002"
    )
    holds = [
        (earlier, later)
        for earlier, later in zip(yielding, yielding[1:])
        if earlier.cell == later.cell and later.timestamp_s > earlier.timestamp_s
    ]
    assert len(holds) == 1, "the yielding robot must hold at its departure point"
    held_cell, held_from, held_to = holds[0][0].cell, holds[0][0].timestamp_s, holds[0][1].timestamp_s
    assert held_from < held_to
    assert held_to > 0.0
    # The hold is at a cell the higher-priority robot never occupies.
    assert runtime.grid.footprint_is_free(
        held_cell,
        runtime.profiles.get("robot-002").width_cells,
        runtime.profiles.get("robot-002").height_cells,
    )

    # ---- 5. the simulation continues and the world stays valid ------------
    batteries_before = {
        robot.robot_id: robot.battery_percent for robot in runtime.robots()
    }
    board.advance(60)
    assert_world_invariants(runtime, five_robot_blueprint())
    assert fleets_conflict_free(runtime) is True

    # ---- 6. battery drained and BATTERY_LOW was published -----------------
    drained = {
        robot.robot_id
        for robot in runtime.robots()
        if robot.battery_percent < batteries_before[robot.robot_id]
    }
    assert "robot-001" in drained
    low_events = [
        event
        for event in board.events_after()
        if event.event_type is EventType.BATTERY_LOW
    ]
    assert low_events, "the battery-low robot must publish BATTERY_LOW"
    assert isinstance(low_events[0].payload, BatteryLowPayload)
    assert low_events[0].payload.robot_id == "robot-003"
    assert low_events[0].payload.battery_percent <= 20.0
    assert low_events[0].payload.estimated_range_m >= 0.0
    assert runtime.robot("robot-003").battery_percent < 19.0

    # ---- 7. a fault is published and Agent 1 reassigns the work -----------
    failure_events = board.submit_command(
        InjectRobotFailureCommand(
            command_id=uuid4(),
            issued_at_s=runtime.now_s,
            robot_id="robot-001",
            failure=FailureInfo(
                kind=FailureKind.ACTUATOR,
                code="drive-failure",
                detected_at_s=runtime.now_s,
            ),
        )
    )

    assert failure_events[0].event_type is EventType.ROBOT_FAILED
    assert isinstance(failure_events[0].payload, RobotFailedPayload)
    failed = runtime.robot("robot-001")
    assert failed.status is RobotStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "drive-failure"

    reassigned = [
        event
        for event in failure_events
        if isinstance(event.payload, TaskReassignedPayload)
    ]
    assert len(reassigned) == 1
    assert reassigned[0].payload.previous_robot_id == "robot-001"
    assert reassigned[0].payload.new_robot_id == "robot-005"
    assert reassigned[0].payload.trigger_event_id == failure_events[0].event_id
    assert runtime.task("task-001").assigned_robot_id == "robot-005"
    # Agent 2 planned a fresh route for the replacement robot.
    assert any(
        event.event_type is EventType.ROUTE_PLANNED
        and event.payload.route.robot_id == "robot-005"
        for event in failure_events
    )

    # ---- 8. the final state is valid --------------------------------------
    board.advance(120)
    assert_world_invariants(runtime, five_robot_blueprint())
    fleets_conflict_free(runtime)

    snapshot = board.snapshot()
    assert len(snapshot.robots) == 5
    assert len(snapshot.tasks) == 4
    assert snapshot.last_event_sequence >= snapshot.revision
    assert snapshot.metrics.failed_robots == 1
    assert snapshot.metrics.task_reassignments == 1
    assert snapshot.metrics.completed_tasks >= 1
    assert snapshot.metrics.open_conflicts == 0
    assert 0.0 <= snapshot.metrics.average_battery_percent <= 100.0
    assert snapshot.metrics.average_allocation_latency_ms >= 0.0
    assert snapshot.metrics.controller_available is True
    assert snapshot.world.revision >= 0
    # Every event in the scenario is a valid canonical envelope.
    assert [event.sequence for event in board.events_after()] == list(
        range(1, len(board.events_after()) + 1)
    )


def test_communication_loss_is_distinct_from_failure_and_recovers() -> None:
    board = build_coordinator(five_robot_blueprint())
    runtime = board.runtime
    board.create_task(task("task-001", (8, 3), priority=4))
    holder = runtime.task("task-001").assigned_robot_id
    board.advance(10)

    events = board.submit_command(
        InjectCommunicationLossCommand(
            command_id=uuid4(),
            issued_at_s=runtime.now_s,
            robot_id=holder,
            timeout_s=3.0,
        )
    )

    assert events[0].event_type is EventType.COMMUNICATION_LOST
    assert isinstance(events[0].payload, CommunicationLostPayload)
    lost = runtime.robot(holder)
    # The robot still exists physically: it is not FAILED and has no failure
    # record. It is only out of coordination.
    assert lost.status is not RobotStatus.FAILED
    assert lost.failure is None
    assert lost.communication_state is CommunicationState.LOST
    # It is excluded from coordination, so Agent 1 moves the work elsewhere.
    assert any(event.event_type is EventType.TASK_REASSIGNED for event in events)
    assert runtime.task("task-001").assigned_robot_id != holder
    assert runtime.robot(holder).current_task_id is None

    # Communication can be restored without inventing a failure history.
    board.submit_command(
        RestoreRobotCommand(
            command_id=uuid4(), issued_at_s=runtime.now_s, robot_id=holder
        )
    )
    restored = runtime.robot(holder)
    assert restored.communication_state is CommunicationState.ONLINE
    assert restored.status is RobotStatus.IDLE
    assert restored.failure is None


def test_the_scenario_is_reproducible() -> None:
    """Two identical runs must produce identical decisions and state."""

    def run() -> tuple:
        board = build_scenario()
        board.advance(60)
        board.submit_command(
            InjectRobotFailureCommand(
                command_id=uuid4(),
                issued_at_s=board.runtime.now_s,
                robot_id="robot-001",
                failure=FailureInfo(
                    kind=FailureKind.ACTUATOR,
                    code="drive-failure",
                    detected_at_s=board.runtime.now_s,
                ),
            )
        )
        board.advance(120)
        snapshot = board.snapshot()
        return (
            tuple(
                (robot.robot_id, robot.position.x, robot.position.y, robot.battery_percent)
                for robot in snapshot.robots
            ),
            tuple((t.task_id, t.status.value, t.assigned_robot_id) for t in snapshot.tasks),
            tuple(
                (r.route_id, r.status.value, len(r.waypoints)) for r in snapshot.routes
            ),
            board.event_types_after(),
        )

    first, second = run(), run()
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[2] == second[2]
    assert first[3] == second[3]


@pytest.mark.parametrize("ticks", [1, 25, 100])
def test_world_invariants_hold_at_every_horizon(ticks: int) -> None:
    board = build_scenario()

    board.advance(ticks)

    assert_world_invariants(board.runtime, five_robot_blueprint())
    assert 0.0 <= board.runtime.now_s <= 10.0 * ticks / 10.0 + 1.0


def test_an_unroutable_assignment_is_reported_rather_than_stalling() -> None:
    """A 3x2 body cannot use the one-cell gap, so the task is reported blocked."""

    board = build_coordinator(five_robot_blueprint())
    runtime = board.runtime
    board.create_task(
        task(
            "task-003",
            (8, 5),
            priority=2,
            required_capabilities=(RobotCapability.TUG,),
        )
    )

    assert runtime.task("task-003").assigned_robot_id == "robot-003"
    state = next(s for s in runtime.robot_states() if s.robot_id == "robot-003")
    assert state.trajectory == ()
    assert state.route is not None and state.route.status.value == "invalid"
    assert state.robot.status is RobotStatus.BLOCKED
    assert runtime.task("task-003").status is TaskStatus.BLOCKED
    # The fleet is still consistent and no phantom conflict was invented.
    assert runtime.snapshot().metrics.open_conflicts == 0
    assert_world_invariants(runtime, five_robot_blueprint())


def test_agent_two_does_not_reimplement_agent_one_reassignment() -> None:
    """The runtime reacts to triggers; only Agent 1 decides who takes over."""

    import ast
    import inspect

    from backend.simulation import runtime as runtime_module

    tree = ast.parse(inspect.getsource(runtime_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(
        name.startswith(("backend.negotiation", "backend.allocation"))
        for name in imported
    )
    # The runtime publishes BATTERY_LOW but never creates a TaskReassignedPayload.
    source = inspect.getsource(runtime_module)
    assert "TaskReassignedPayload(" not in source
    assert "TaskAssignedPayload(" not in source


def test_holder_is_never_needed_for_the_core_scenario() -> None:
    """Every resolution in the scenario used a timing delay, not a hold."""

    board = build_scenario()

    strategies = {
        event.payload.action.action_type.value
        for event in board.events_after()
        if event.event_type is EventType.RECOVERY_STARTED
    }

    assert strategies == {"yield"}
