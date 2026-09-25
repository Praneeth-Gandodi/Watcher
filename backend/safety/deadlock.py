"""Deadlock detection and recovery over a wait graph.

The wait graph is the single deadlock model in the project. A robot has an edge
to another robot when it is blocked and waiting for that robot to move first::

    robot-001 -> robot-002
    robot-002 -> robot-003
    robot-003 -> robot-001

A cycle means every robot in it waits for another that never moves: that is a
deadlock. Detection is iterative so a large fleet cannot exhaust the Python
recursion limit, and recovery clears one robot's dependency without mutating the
caller's graph.

``DeadlockReport`` and ``RecoveryAction`` from the protected contracts are
produced here so the event stream carries canonical records.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.contracts.models import (
    ActionStatus,
    DeadlockReport,
    RecoveryAction,
    RecoveryActionType,
)
from backend.safety.right_of_way import RobotPriority

__all__ = [
    "DeadlockCycle",
    "DeadlockRecovery",
    "build_deadlock_report",
    "build_recovery_action",
    "choose_deadlock_robot",
    "find_deadlock_cycles",
    "has_deadlock",
    "recover_deadlock",
    "wait_graph_from_blocked",
]

#: A wait graph maps a robot to the robots it is waiting for.
WaitGraph = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class DeadlockCycle:
    """A closed chain of wait dependencies."""

    robot_ids: tuple[str, ...]

    def __contains__(self, robot_id: object) -> bool:
        return robot_id in self.robot_ids

    def __len__(self) -> int:
        return len(self.robot_ids)

    def as_dict(self) -> dict[str, object]:
        return {"robot_ids": list(self.robot_ids)}


@dataclass(frozen=True, slots=True)
class DeadlockRecovery:
    """Result of breaking a deadlock by clearing one wait dependency."""

    recovered: bool
    graph: dict[str, tuple[str, ...]]
    robot_id: str | None = None
    action: str | None = None
    cycle: DeadlockCycle | None = None
    reason: str = ""


def has_deadlock(wait_graph: WaitGraph) -> bool:
    """Return whether the wait graph contains a cycle."""

    return bool(find_deadlock_cycles(wait_graph))


def find_deadlock_cycles(wait_graph: WaitGraph) -> tuple[DeadlockCycle, ...]:
    """Return every elementary cycle, deterministically ordered.

    Uses an iterative depth-first search and reports each cycle once, in the
    order it is closed, so a fleet of hundreds of robots stays safe.
    """

    normalised: dict[str, tuple[str, ...]] = {
        robot_id: tuple(sorted(wait_graph[robot_id]))
        for robot_id in sorted(wait_graph)
    }

    cycles: list[DeadlockCycle] = []
    seen_signatures: set[tuple[str, ...]] = set()
    visited: set[str] = set()
    on_path: dict[str, int] = {}
    path: list[str] = []

    for root in normalised:
        if root in visited:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        on_path[root] = 0
        path.append(root)
        while stack:
            node, cursor = stack[-1]
            successors = normalised.get(node, ())
            if cursor >= len(successors):
                stack.pop()
                position = on_path.pop(node)
                del path[position]
                visited.add(node)
                continue
            stack[-1] = (node, cursor + 1)
            successor = successors[cursor]
            if successor in on_path:
                cycle = path[on_path[successor] :]
                signature = _canonical_signature(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(DeadlockCycle(robot_ids=tuple(cycle)))
                continue
            if successor in visited or successor not in normalised:
                continue
            on_path[successor] = len(path)
            path.append(successor)
            stack.append((successor, 0))
    return tuple(cycles)


def _canonical_signature(cycle: Sequence[str]) -> tuple[str, ...]:
    """Return a rotation-independent key so a cycle is only reported once."""

    if not cycle:
        return ()
    pivot = min(range(len(cycle)), key=lambda index: cycle[index])
    return tuple(cycle[pivot:] + cycle[:pivot])


def choose_deadlock_robot(
    candidates: Iterable[str] | DeadlockCycle,
    robot_info: Mapping[str, RobotPriority],
) -> str | None:
    """Return the robot inside a deadlock that should yield.

    The lowest priority score yields; ties are broken by ``robot_id`` so the
    choice is reproducible.
    """

    robot_ids = (
        candidates.robot_ids if isinstance(candidates, DeadlockCycle) else tuple(candidates)
    )
    if not robot_ids:
        return None
    return min(
        sorted(robot_ids),
        key=lambda robot_id: (
            robot_info.get(robot_id, RobotPriority(robot_id=robot_id)).score(),
            robot_id,
        ),
    )


def recover_deadlock(
    wait_graph: WaitGraph,
    robot_info: Mapping[str, RobotPriority],
) -> DeadlockRecovery:
    """Break a deadlock by clearing one robot's wait dependency.

    The input graph is never mutated: a new graph is returned so the caller
    decides whether to adopt it.
    """

    cycles = find_deadlock_cycles(wait_graph)
    if not cycles:
        return DeadlockRecovery(
            recovered=False,
            graph={key: tuple(value) for key, value in wait_graph.items()},
            reason="no deadlock detected",
        )

    cycle = min(cycles, key=lambda item: (len(item.robot_ids), item.robot_ids))
    robot_id = choose_deadlock_robot(cycle, robot_info)
    if robot_id is None:
        return DeadlockRecovery(
            recovered=False,
            graph={key: tuple(value) for key, value in wait_graph.items()},
            cycle=cycle,
            reason="deadlock cycle contains no robot",
        )

    recovered_graph = {
        key: (() if key == robot_id else tuple(value))
        for key, value in wait_graph.items()
    }
    return DeadlockRecovery(
        recovered=True,
        graph=recovered_graph,
        robot_id=robot_id,
        action=RecoveryActionType.YIELD.value,
        cycle=cycle,
        reason=(
            f"{robot_id} stops waiting and yields so the cycle "
            f"{' -> '.join(cycle.robot_ids)} -> {cycle.robot_ids[0]} can clear"
        ),
    )


def wait_graph_from_blocked(
    blocked: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Build a deterministic wait graph from blocked-robot observations."""

    return {
        robot_id: tuple(sorted(dict.fromkeys(wait_graph.get(robot_id, ()))))
        for robot_id in sorted(blocked)
        if wait_graph.get(robot_id)
    }


def build_deadlock_report(
    deadlock_id: str,
    cycle: DeadlockCycle,
    blocked_task_ids: Sequence[str],
    detected_at_s: float,
    *,
    confidence: float = 1.0,
) -> DeadlockReport:
    """Create the canonical ``DeadlockReport`` for a detected cycle."""

    return DeadlockReport(
        deadlock_id=deadlock_id,
        cycle_robot_ids=tuple(sorted(cycle.robot_ids)),
        blocked_task_ids=tuple(sorted(dict.fromkeys(blocked_task_ids))),
        detected_at_s=detected_at_s,
        confidence=confidence,
    )


def build_recovery_action(
    action_id: str,
    action_type: RecoveryActionType,
    target_robot_ids: Sequence[str],
    affected_task_ids: Sequence[str],
    reason: str,
    started_at_s: float,
) -> RecoveryAction:
    """Create the canonical ``RecoveryAction`` for a recovery step."""

    return RecoveryAction(
        action_id=action_id,
        action_type=action_type,
        target_robot_ids=tuple(sorted(dict.fromkeys(target_robot_ids))),
        affected_task_ids=tuple(sorted(dict.fromkeys(affected_task_ids))),
        reason=reason,
        started_at_s=started_at_s,
        status=ActionStatus.ACTIVE,
    )
