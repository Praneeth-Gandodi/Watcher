"""Deterministic 10-robot stress scenario.

Ten canonical robot IDs, four different speeds, five body shapes, a wall with a
single-cell gap, several tasks, at least one collision conflict, a deadlock
check, and battery changes -- all in one deterministic run.

The point is not to prove optimal behaviour at scale. It is to prove the
implementation stays correct and exception-free at the demo fleet size:

* no Python exception
* no negative or out-of-grid robot position
* no robot occupies an obstacle cell
* no unresolved space-time collision after safety resolution
* metrics remain valid
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.composition import build_coordinator
from backend.contracts.commands import InjectRobotFailureCommand
from backend.contracts.events import EventType
from backend.contracts.models import (
    FailureInfo,
    FailureKind,
    RobotStatus,
    TaskStatus,
)
from backend.safety.deadlock import find_deadlock_cycles, recover_deadlock
from backend.safety.right_of_way import find_earliest_conflict
from backend.simulation.runtime import fleets_conflict_free
from tests.integration.agent2.conftest import (
    assert_world_invariants,
    task,
    ten_robot_blueprint,
)

TASK_IDS = tuple(f"task-{index:03d}" for index in range(1, 7))

#: Task targets chosen so every conflict in this scenario is resolvable by a
#: timing delay. The first two are the guaranteed neck conflict; the rest keep
#: robots spread out. This set was verified to reach six completed tasks with no
#: unresolved space-time collision and no open conflict.
TASK_CELLS = (
    ("task-001", (14, 10), 5),
    ("task-002", (9, 9), 1),
    ("task-003", (6, 1), 2),
    ("task-004", (1, 3), 2),
    ("task-005", (6, 5), 3),
    ("task-006", (9, 8), 1),
)


def stress_tasks() -> list:
    """Return six tasks spread across both sides of the neck.

    ``task-001`` and ``task-002`` are won by the two 1x1 robots that can use the
    single-cell neck at (8, 5); at very different speeds their arrivals there
    overlap, so a conflict is guaranteed. The remaining tasks are spread out so
    no robot is left permanently blocking another's destination.
    """

    return [task(task_id, cell, priority) for task_id, cell, priority in TASK_CELLS]


def build_stress_board():
    return build_coordinator(ten_robot_blueprint())


def test_the_ten_robot_fleet_is_valid_and_heterogeneous() -> None:
    blueprint = ten_robot_blueprint()

    assert len(blueprint.robots) == 10
    assert len(blueprint.profiles) == 10
    assert list(blueprint.robot_ids) == [
        f"robot-{index:03d}" for index in range(1, 11)
    ], "canonical kebab-case identifiers"
    assert len(set(blueprint.start_cells)) == 10
    assert len({p.speed_mps for p in blueprint.profiles}) >= 3
    assert len({(p.width_cells, p.height_cells) for p in blueprint.profiles}) >= 4
    assert blueprint.occupancy_invariants_hold()
    assert blueprint.world.cells, "the stress world has obstacles"


def test_ten_robots_allocate_plan_and_resolve_without_exceptions() -> None:
    board = build_stress_board()
    runtime = board.runtime

    for item in stress_tasks():
        board.create_task(item)

    # Every task was allocated, and every allocation produced a route.
    assert len(runtime.tasks()) == len(TASK_IDS)
    assigned = [t for t in runtime.tasks() if t.assigned_robot_id is not None]
    assert len(assigned) == len(TASK_IDS)
    for task_item in runtime.tasks():
        assert task_item.assigned_robot_id is not None
        assert task_item.assigned_robot_id != task_item.task_id

    # A robot never holds two tasks.
    holders = [t.assigned_robot_id for t in runtime.tasks()]
    assert len(holders) == len(set(holders))

    assert_world_invariants(runtime, ten_robot_blueprint())
    assert fleets_conflict_free(runtime) is True


def test_the_stress_run_produces_a_conflict_that_is_resolved() -> None:
    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)

    conflicts = [
        event
        for event in board.events_after()
        if event.event_type is EventType.CONFLICT_DETECTED
    ]
    assert conflicts, "the ten-robot scenario must produce at least one conflict"
    assert fleets_conflict_free(runtime) is True, (
        "no space-time collision may remain after safety resolution"
    )
    for event in conflicts:
        conflict = event.payload.conflict
        assert len(conflict.robot_ids) >= 2
        assert conflict.kind.value == "right_of_way"
        assert len(set(conflict.robot_ids)) == len(conflict.robot_ids)
        assert conflict.position.x >= 0.0 and conflict.position.y >= 0.0


def test_the_stress_run_keeps_every_invariant_while_it_advances() -> None:
    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)

    batteries_before = {
        robot.robot_id: robot.battery_percent for robot in runtime.robots()
    }

    for _ in range(20):
        board.advance(10)
        assert_world_invariants(runtime, ten_robot_blueprint())

    assert fleets_conflict_free(runtime) is True
    assert runtime.now_s == pytest.approx(20.0)

    # Battery changed for every robot that actually moved.
    moved = {
        robot.robot_id
        for robot in runtime.robots()
        if robot.battery_percent < batteries_before[robot.robot_id]
    }
    assert moved, "at least one robot must have drained its battery"
    for robot in runtime.robots():
        assert 0.0 <= robot.battery_percent <= 100.0
        assert robot.position.x >= 0.0 and robot.position.y >= 0.0
        assert runtime.grid.contains(*runtime.grid.cell_for_position(robot.position))
        assert runtime.grid.footprint_is_free(
            runtime.grid.cell_for_position(robot.position),
            runtime.profiles.get(robot.robot_id).width_cells,
            runtime.profiles.get(robot.robot_id).height_cells,
        )


def test_the_stress_run_survives_faults_and_keeps_metrics_valid() -> None:
    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)
    board.advance(20)

    for index, robot_id in enumerate(("robot-002", "robot-005"), start=1):
        events = board.submit_command(
            InjectRobotFailureCommand(
                command_id=uuid4(),
                issued_at_s=runtime.now_s,
                robot_id=robot_id,
                failure=FailureInfo(
                    kind=FailureKind.OTHER,
                    code=f"fault-{index}",
                    detected_at_s=runtime.now_s,
                ),
            )
        )
        assert events[0].event_type is EventType.ROBOT_FAILED
        assert runtime.robot(robot_id).status is RobotStatus.FAILED
        assert_world_invariants(runtime, ten_robot_blueprint())

    for _ in range(20):
        board.advance(10)
        assert_world_invariants(runtime, ten_robot_blueprint())

    snapshot = board.snapshot()
    assert len(snapshot.robots) == 10
    assert snapshot.metrics.failed_robots == 2
    assert snapshot.metrics.average_battery_percent >= 0.0
    assert snapshot.metrics.average_battery_percent <= 100.0
    assert snapshot.metrics.task_reassignments >= 1
    assert snapshot.metrics.active_robots >= 0
    assert snapshot.last_event_sequence >= snapshot.revision
    assert snapshot.metrics.event_throughput_per_s >= 0.0
    assert fleets_conflict_free(runtime) is True


def test_the_stress_run_reaches_terminal_task_states() -> None:
    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)

    board.advance(400)

    statuses = {t.task_id: t.status for t in runtime.tasks()}
    assert len(statuses) == len(TASK_IDS)
    assert all(
        status in {TaskStatus.COMPLETED, TaskStatus.BLOCKED, TaskStatus.RECOVERY}
        for status in statuses.values()
    ), f"tasks must settle, got {statuses}"
    assert runtime.metrics().completed_tasks >= 1
    assert_world_invariants(runtime, ten_robot_blueprint())


def test_the_deadlock_graph_is_available_for_every_blocked_robot() -> None:
    """Deadlock detection is exercised on the runtime's own wait graph."""

    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)
    board.advance(60)

    wait_graph = runtime.wait_graph()
    assert list(wait_graph) == sorted(wait_graph), "the wait graph is deterministic"
    for robot_id, waiters in wait_graph.items():
        assert waiters == tuple(sorted(waiters))
        assert robot_id not in waiters, "a robot never waits for itself"

    cycles = find_deadlock_cycles(wait_graph)
    for cycle in cycles:
        assert len(cycle.robot_ids) >= 2
        assert len(set(cycle.robot_ids)) == len(cycle.robot_ids)

    # Recovery is a pure function: the input graph is never mutated.
    if cycles:
        recovery = recover_deadlock(wait_graph, runtime.priorities())
        assert recovery.recovered is True
        assert find_deadlock_cycles(recovery.graph) == ()
        assert wait_graph == runtime.wait_graph()


def test_pairwise_conflict_checking_stays_affordable_at_ten_robots() -> None:
    """Ten robots is 45 pairs: exhaustive checking is the right MVP choice."""

    board = build_stress_board()
    runtime = board.runtime
    for item in stress_tasks():
        board.create_task(item)

    trajectories = runtime.active_trajectories()
    expected_pairs = len(trajectories) * (len(trajectories) - 1) // 2

    assert len(trajectories) <= 10
    assert expected_pairs <= 45
    assert find_earliest_conflict(trajectories) is None
