"""Test plan H: wait-graph deadlock detection and recovery.

Covers:
* ``robot-001 -> robot-002 -> robot-003 -> robot-001`` is detected as a cycle
* acyclic wait graphs are not deadlocks
* recovery breaks the cycle by clearing one robot's dependency
* recovery never mutates the caller's graph
* the chosen yielder is deterministic
* canonical ``DeadlockReport`` and ``RecoveryAction`` are produced
"""

from __future__ import annotations

import pytest
from backend.contracts.models import ActionStatus, RecoveryActionType
from backend.safety.deadlock import (
    build_deadlock_report,
    build_recovery_action,
    choose_deadlock_robot,
    find_deadlock_cycles,
    has_deadlock,
    recover_deadlock,
    wait_graph_from_blocked,
)
from backend.safety.right_of_way import RobotPriority

CYCLE = {
    "robot-001": ("robot-002",),
    "robot-002": ("robot-003",),
    "robot-003": ("robot-001",),
}

PRIORITIES = {
    "robot-001": RobotPriority("robot-001", task_priority=4),
    "robot-002": RobotPriority("robot-002", task_priority=2),
    "robot-003": RobotPriority("robot-003", task_priority=3),
}


def test_three_robot_cycle_is_a_deadlock() -> None:
    assert has_deadlock(CYCLE) is True

    cycles = find_deadlock_cycles(CYCLE)

    assert len(cycles) == 1
    assert sorted(cycles[0].robot_ids) == ["robot-001", "robot-002", "robot-003"]


def test_a_two_robot_cycle_is_a_deadlock() -> None:
    graph = {"robot-001": ("robot-002",), "robot-002": ("robot-001",)}

    assert has_deadlock(graph) is True
    assert sorted(find_deadlock_cycles(graph)[0].robot_ids) == [
        "robot-001",
        "robot-002",
    ]


def test_an_acyclic_wait_graph_is_not_a_deadlock() -> None:
    graph = {
        "robot-001": ("robot-002",),
        "robot-002": ("robot-003",),
        "robot-003": (),
    }

    assert has_deadlock(graph) is False
    assert find_deadlock_cycles(graph) == ()


def test_a_robot_waiting_for_itself_is_a_deadlock() -> None:
    assert has_deadlock({"robot-001": ("robot-001",)}) is True


def test_an_empty_wait_graph_is_not_a_deadlock() -> None:
    assert has_deadlock({}) is False
    assert recover_deadlock({}, PRIORITIES).recovered is False


def test_two_independent_cycles_are_both_reported() -> None:
    graph = {
        "robot-001": ("robot-002",),
        "robot-002": ("robot-001",),
        "robot-003": ("robot-004",),
        "robot-004": ("robot-005",),
        "robot-005": ("robot-003",),
    }

    cycles = find_deadlock_cycles(graph)

    assert len(cycles) == 2
    assert sorted(len(cycle) for cycle in cycles) == [2, 3]


def test_cycle_detection_is_stable_across_mapping_order() -> None:
    forward = find_deadlock_cycles(CYCLE)
    reversed_order = find_deadlock_cycles(dict(reversed(list(CYCLE.items()))))

    assert [cycle.robot_ids for cycle in forward] == [
        cycle.robot_ids for cycle in reversed_order
    ]


# ----------------------------------------------------------------------
# recovery
# ----------------------------------------------------------------------


def test_recovery_breaks_the_cycle_by_clearing_one_dependency() -> None:
    recovery = recover_deadlock(CYCLE, PRIORITIES)

    assert recovery.recovered is True
    assert recovery.action == RecoveryActionType.YIELD.value
    # The lowest-priority robot in the cycle yields.
    assert recovery.robot_id == "robot-002"
    assert recovery.cycle is not None
    assert "robot-002" in recovery.reason

    assert recovery.graph == {
        "robot-001": ("robot-002",),
        "robot-002": (),
        "robot-003": ("robot-001",),
    }
    assert has_deadlock(recovery.graph) is False, "the cycle must be gone"


def test_recovery_does_not_mutate_the_caller_graph() -> None:
    original = {"robot-001": ("robot-002",), "robot-002": ("robot-001",)}

    recovery = recover_deadlock(original, PRIORITIES)

    assert original == {"robot-001": ("robot-002",), "robot-002": ("robot-001",)}
    assert recovery.graph is not original


def test_recovery_reports_no_deadlock_for_an_acyclic_graph() -> None:
    graph = {"robot-001": ("robot-002",), "robot-002": ()}

    recovery = recover_deadlock(graph, PRIORITIES)

    assert recovery.recovered is False
    assert recovery.robot_id is None
    assert recovery.reason == "no deadlock detected"
    assert recovery.graph == graph


def test_the_yielder_is_the_lowest_priority_robot_in_the_cycle() -> None:
    cycle = find_deadlock_cycles(CYCLE)[0]

    assert choose_deadlock_robot(cycle, PRIORITIES) == "robot-002"
    assert choose_deadlock_robot((), PRIORITIES) is None


def test_the_yielder_is_broken_by_robot_id_on_a_priority_tie() -> None:
    priorities = {
        robot_id: RobotPriority(robot_id, task_priority=2) for robot_id in CYCLE
    }

    assert choose_deadlock_robot(CYCLE, priorities) == "robot-001"
    assert recover_deadlock(CYCLE, priorities).robot_id == "robot-001"


def test_a_long_cycle_is_detected_without_recursion() -> None:
    """Iterative detection keeps a large fleet away from the recursion limit."""

    size = 1500
    graph = {
        f"robot-{index + 1:04d}": (f"robot-{(index + 1) % size + 1:04d}",)
        for index in range(size)
    }

    assert has_deadlock(graph) is True
    recovery = recover_deadlock(graph, {})
    assert recovery.recovered is True
    assert has_deadlock(recovery.graph) is False


# ----------------------------------------------------------------------
# canonical records
# ----------------------------------------------------------------------


def test_wait_graph_from_blocked_observations_is_deterministic() -> None:
    graph = wait_graph_from_blocked(
        {
            "robot-003": ["robot-001", "robot-002", "robot-002"],
            "robot-001": ["robot-003"],
            "robot-002": (),
        }
    )

    assert list(graph) == ["robot-001", "robot-003"]
    assert graph["robot-003"] == ("robot-001", "robot-002")


def test_deadlock_report_is_canonical_and_sorted() -> None:
    cycle = find_deadlock_cycles(CYCLE)[0]

    report = build_deadlock_report(
        "deadlock-001",
        cycle,
        ["task-002", "task-001"],
        16.0,
        confidence=0.95,
    )

    assert report.deadlock_id == "deadlock-001"
    assert report.cycle_robot_ids == ("robot-001", "robot-002", "robot-003")
    assert report.blocked_task_ids == ("task-001", "task-002")
    assert report.detected_at_s == 16.0
    assert report.confidence == 0.95


def test_recovery_action_is_canonical_and_active() -> None:
    action = build_recovery_action(
        "recovery-001",
        RecoveryActionType.YIELD,
        ["robot-002", "robot-002", "robot-001"],
        ["task-001", "task-001"],
        "cyclic waiting detected",
        16.0,
    )

    assert action.target_robot_ids == ("robot-001", "robot-002")
    assert action.affected_task_ids == ("task-001",)
    assert action.status is ActionStatus.ACTIVE
    assert action.action_type is RecoveryActionType.YIELD


def test_recovery_action_rejects_an_empty_target() -> None:
    with pytest.raises(ValueError):
        build_recovery_action(
            "recovery-001",
            RecoveryActionType.YIELD,
            [],
            (),
            "no target",
            0.0,
        )
