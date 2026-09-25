"""Test plan K: task -> allocation -> route -> trajectory -> safety -> snapshot.

A small world with five robots driven entirely through the composition root, so
the real Agent 1 negotiation/allocation code and the real Agent 2 movement/safety
code run together and exchange only canonical contracts and events.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.composition import build_coordinator
from backend.contracts.commands import (
    CreateTaskCommand,
    PauseSimulationCommand,
    ResetSimulationCommand,
    ResumeSimulationCommand,
)
from backend.contracts.events import (
    EventType,
    TaskAssignedPayload,
    TaskCreatedPayload,
    parse_event,
)
from backend.contracts.models import (
    NegotiationStatus,
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


def coordinator():
    """A coordinator over the deterministic five-robot scenario fleet."""

    return build_coordinator(five_robot_blueprint())


def test_a_task_flows_from_allocation_through_movement_to_a_valid_snapshot() -> None:
    board = coordinator()
    runtime = board.runtime

    created = board.create_task(task("task-001", (8, 3), priority=4))

    # The full Agent 1 -> Agent 2 cascade, in one call.
    assert created[0].event_type is EventType.TASK_CREATED
    types = [item.event_type for item in created]
    assert EventType.NEGOTIATION_STARTED in types
    assert EventType.BID_SUBMITTED in types
    assert EventType.TASK_ASSIGNED in types
    assert EventType.ROUTE_REQUESTED in types
    assert EventType.ROUTE_PLANNED in types

    # Agent 1 chose a robot and Agent 2 planned a route for exactly that robot.
    winner = runtime.task("task-001").assigned_robot_id
    assert winner == "robot-001"
    route = runtime.routes()[0]
    assert route.robot_id == winner
    assert route.task_id == "task-001"
    assert route.status.value == "active"
    assert len(route.waypoints) >= 2

    # The trajectory is time-aware and derived from the route's own waypoints.
    state = next(s for s in runtime.robot_states() if s.robot_id == winner)
    assert [point.cell for point in state.trajectory] == [
        runtime.grid.cell_for_position(point) for point in route.waypoints
    ]
    assert all(
        earlier.timestamp_s < later.timestamp_s
        for earlier, later in zip(state.trajectory, state.trajectory[1:])
    )
    assert state.profile.time_per_cell_s(runtime.grid.cell_size_m) == pytest.approx(
        state.trajectory[1].timestamp_s
    )

    # Safety approved the movement, so the fleet is conflict free.
    assert fleets_conflict_free(runtime) is True
    assert runtime.snapshot().metrics.open_conflicts == 0

    # Movement costs energy and keeps the world consistent.
    before = runtime.robot(winner).battery_percent
    board.advance(30)
    assert_world_invariants(runtime, None)
    assert runtime.robot(winner).battery_percent < before

    snapshot = board.snapshot()
    assert len(snapshot.robots) == 5
    assert len(snapshot.tasks) == 1
    assert len(snapshot.routes) == 1
    assert snapshot.last_event_sequence >= snapshot.revision
    assert snapshot.controller_available is True
    assert 0.0 <= snapshot.metrics.average_battery_percent <= 100.0
    assert snapshot.metrics.average_allocation_latency_ms >= 0.0
    assert snapshot.metrics.active_robots + snapshot.metrics.completed_tasks >= 1
    assert board.metrics().model_dump() == snapshot.metrics.model_dump()


def test_a_second_task_cannot_take_a_robot_that_already_holds_one() -> None:
    """The assignment binds the task immediately, before the first movement."""

    board = coordinator()
    runtime = board.runtime

    first = board.create_task(task("task-001", (8, 3), priority=5))
    winner = runtime.task("task-001").assigned_robot_id

    second = board.create_task(task("task-002", (6, 6), priority=1))

    assignments = {
        item.payload.assignment.task_id: item.payload.assignment.robot_id
        for item in (*first, *second)
        if isinstance(item.payload, TaskAssignedPayload)
    }
    assert assignments["task-001"] == winner
    assert assignments["task-002"] != winner, (
        "a robot already holding a task must not win a second one"
    )
    assert runtime.robot(winner).current_task_id == "task-001"


def test_the_snapshot_and_events_are_serializable_for_the_dashboard() -> None:
    board = coordinator()
    board.create_task(task("task-001", (8, 3), priority=4))
    board.advance(20)

    snapshot = board.snapshot().model_dump(mode="json")
    assert snapshot["world"]["columns"] == 10
    assert len(snapshot["robots"]) == 5
    assert snapshot["world"]["cells"], "the wall must be present in the snapshot"

    for event in board.events_after():
        assert parse_event(event.model_dump(mode="json")) == event


def test_agent_one_and_agent_two_share_one_monotonic_event_sequence() -> None:
    board = coordinator()
    board.create_task(task("task-001", (8, 3), priority=4))
    board.advance(20)

    events = board.events_after()

    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert {item.producer for item in events} == {
        "agent-1-negotiation",
        "agent-2-safety",
    }
    types = [item.event_type for item in events]
    assert types.index(EventType.TASK_ASSIGNED) < types.index(EventType.ROUTE_REQUESTED)
    assert types.index(EventType.NEGOTIATION_STARTED) < types.index(
        EventType.TASK_ASSIGNED
    )
    assert isinstance(events[0].payload, TaskCreatedPayload)


def test_a_create_task_command_runs_the_whole_cascade() -> None:
    board = coordinator()
    command = CreateTaskCommand(
        command_id=uuid4(),
        issued_at_s=0.0,
        task=task("task-001", (8, 3), priority=4),
    )

    events = board.submit_command(command)

    assert events[0].event_type is EventType.TASK_CREATED
    assert {item.event_type for item in events} >= {
        EventType.TASK_ASSIGNED,
        EventType.ROUTE_PLANNED,
    }
    assert board.runtime.task("task-001").status is TaskStatus.ASSIGNED
    assert board.runtime.task("task-001").assigned_robot_id == "robot-001"


def test_only_capable_robots_win_a_task_that_requires_capabilities() -> None:
    """Required capabilities decide eligibility, independent of bid cost."""

    board = coordinator()
    runtime = board.runtime

    board.create_task(
        task(
            "task-003",
            (2, 6),
            priority=2,
            required_capabilities=(RobotCapability.TUG,),
        )
    )

    assert runtime.task("task-003").assigned_robot_id == "robot-003"
    assert runtime.robot("robot-003").current_task_id == "task-003"


def test_a_completed_task_frees_its_robot_and_keeps_the_route_visible() -> None:
    board = coordinator()
    runtime = board.runtime
    board.create_task(task("task-001", (8, 3), priority=4))

    events = board.advance(200)

    assert EventType.TASK_COMPLETED in {item.event_type for item in events}
    assert runtime.task("task-001").status is TaskStatus.COMPLETED
    assert runtime.robot("robot-001").status is RobotStatus.IDLE
    assert runtime.robot("robot-001").current_task_id is None
    # The finished robot keeps its route so the dashboard can draw it.
    assert runtime.routes()[0].status.value == "completed"


def test_a_paused_simulation_does_not_advance() -> None:
    board = coordinator()
    board.create_task(task("task-001", (8, 3), priority=4))
    before = board.runtime.now_s

    board.submit_command(PauseSimulationCommand(command_id=uuid4(), issued_at_s=0.0))
    assert board.advance(50) == ()
    assert board.runtime.now_s == before

    board.submit_command(ResumeSimulationCommand(command_id=uuid4(), issued_at_s=0.0))
    board.advance(10)
    assert board.runtime.now_s > before


def test_reset_clears_the_simulation_and_keeps_the_fleet() -> None:
    board = coordinator()
    board.create_task(task("task-001", (8, 3), priority=4))
    board.advance(30)

    board.submit_command(ResetSimulationCommand(command_id=uuid4(), issued_at_s=0.0, seed=1))

    snapshot = board.snapshot()
    assert snapshot.simulation_time_s == 0.0
    assert snapshot.tasks == ()
    assert snapshot.revision == 0
    assert snapshot.last_event_sequence == 0
    assert len(snapshot.robots) == 5
    assert all(robot.status is RobotStatus.IDLE for robot in snapshot.robots)
    assert all(robot.current_task_id is None for robot in snapshot.robots)


def test_negotiation_outcomes_are_reported_and_reproducible() -> None:
    board = coordinator()
    board.runtime.submit_task(task("task-001", (8, 3), priority=4))

    first = board.negotiate_task(board.runtime.task("task-001"))
    second = board.negotiate_task(board.runtime.task("task-001"))

    assert first.outcome.status is NegotiationStatus.ASSIGNED
    assert first.assigned_robot_id == second.assigned_robot_id
    assert first.event_types == second.event_types
    assert EventType.TASK_ASSIGNED in first.event_types
    assert len([e for e in first.events if e.event_type is EventType.BID_SUBMITTED]) == 5

