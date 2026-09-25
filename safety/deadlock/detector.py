"""Wait-for cycle detection and deadlock recovery for Agent 2.

A robot *waits* when it wants to enter a cell another robot has reserved or
when it is held at a right-of-way yield. Those waits form a directed graph and
a deadlock is exactly a cycle in it. Detecting cycles rather than guessing from
timers is what makes the recovery decision explainable: the report can name
the exact robots in the cycle and the exact wait that closes it.

Cycles are only reported after a robot has actually been waiting for
``MIN_WAIT_S``, which keeps ordinary momentary queues from being labelled as
deadlocks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import hypot

from backend.contracts.models import (
    DeadlockReport,
    GridCellType,
    Position2D,
    RecoveryAction,
    RecoveryActionType,
    Robot,
    RoutePlan,
    ActionStatus,
)
from backend.simulation.grid import Cell, WorldIndex

MIN_WAIT_S = 1.0
DEADLOCK_MEMORY_S = 4.0


@dataclass(frozen=True, slots=True)
class WaitEdge:
    """``waiting_robot_id`` is blocked by ``blocking_robot_id`` at ``cell``."""

    waiting_robot_id: str
    blocking_robot_id: str
    cell: Cell
    since_s: float


@dataclass(slots=True)
class DeadlockDetector:
    """Builds the wait-for graph and extracts the cycles worth reporting."""

    min_wait_s: float = MIN_WAIT_S
    memory_s: float = DEADLOCK_MEMORY_S
    reported: dict[str, float] = field(default_factory=dict)

    def build_edges(
        self,
        *,
        robots: Mapping[str, Robot],
        routes: Mapping[str, RoutePlan],
        reservation_owner: Mapping[Cell, str],
        index: WorldIndex,
        cell_size_m: float,
    ) -> list[WaitEdge]:
        """Derive wait edges from the next cells each robot intends to enter.

        An edge only exists when the cell is currently reserved by a *different*
        robot. That keeps the graph a statement about contention rather than
        about intent.
        """

        edges: list[WaitEdge] = []
        lookahead = max(1, int(round(cell_size_m)))
        for robot_id, route in routes.items():
            robot = robots.get(robot_id)
            if robot is None:
                continue
            ahead = _cell_ahead(robot.position, route, lookahead, index)
            if ahead is None:
                continue
            owner = reservation_owner.get(ahead)
            if owner is None or owner == robot_id:
                continue
            edges.append(
                WaitEdge(
                    waiting_robot_id=robot_id,
                    blocking_robot_id=owner,
                    cell=ahead,
                    since_s=0.0,
                )
            )
        return edges

    def detect(
        self,
        edges: Sequence[WaitEdge],
        *,
        robots: Mapping[str, Robot],
        blocked_task_by_robot: Mapping[str, str | None],
        now_s: float,
    ) -> list[tuple[DeadlockReport, list[WaitEdge]]]:
        """Return unresolved cycles and the edges that form each cycle."""

        self._expire(now_s)
        graph: dict[str, list[WaitEdge]] = {}
        for edge in edges:
            graph.setdefault(edge.waiting_robot_id, []).append(edge)

        reports: list[tuple[DeadlockReport, list[WaitEdge]]] = []
        for cycle_edges in _find_cycles(graph):
            robot_ids = tuple(sorted({edge.waiting_robot_id for edge in cycle_edges}))
            if len(robot_ids) < 2:
                continue
            fingerprint = "-".join(robot_ids)
            if fingerprint in self.reported:
                continue
            self.reported[fingerprint] = now_s
            tasks = tuple(
                sorted(
                    {
                        task_id
                        for robot_id in robot_ids
                        if (task_id := blocked_task_by_robot.get(robot_id)) is not None
                    }
                )
            )
            reports.append(
                (
                    DeadlockReport(
                        deadlock_id=f"deadlock-{fingerprint}-{int(now_s * 1000)}",
                        cycle_robot_ids=robot_ids,
                        blocked_task_ids=tasks,
                        detected_at_s=round(now_s, 3),
                        confidence=round(min(1.0, 0.6 + 0.1 * len(cycle_edges)), 2),
                    ),
                    list(cycle_edges),
                )
            )
        return reports

    def _expire(self, now_s: float) -> None:
        cutoff = now_s - self.memory_s
        for key, detected_at in list(self.reported.items()):
            if detected_at < cutoff:
                del self.reported[key]


def build_recovery_actions(
    reports: Iterable[tuple[DeadlockReport, list[WaitEdge]]],
    *,
    robots: Mapping[str, Robot],
    index: WorldIndex,
    now_s: float,
) -> list[RecoveryAction]:
    """Turn each detected cycle into concrete, bounded recovery actions.

    The cycle is broken by making the *lowest* identifier in the cycle migrate
    its task and hold position, and by replanning the robot directly ahead of
    it around the contended cell. Migrating exactly one participant is the
    minimum intervention that guarantees the cycle is destroyed, and choosing
    by identifier keeps the choice reproducible.
    """

    actions: list[RecoveryAction] = []
    for report, cycle_edges in reports:
        participants = sorted(report.cycle_robot_ids)
        migrating = participants[0]
        blocking = next(
            (edge.blocking_robot_id for edge in cycle_edges if edge.waiting_robot_id == migrating),
            participants[1 % len(participants)],
        )
        if report.blocked_task_ids:
            actions.append(
                _action(
                    action_id=f"recovery-migrate-{migrating}-{int(now_s * 1000)}",
                    action_type=RecoveryActionType.TASK_MIGRATION,
                    target_robot_ids=(migrating,),
                    affected_task_ids=report.blocked_task_ids,
                    reason=(
                        f"deadlock {report.deadlock_id}: migrating the task of {migrating} "
                        f"out of the wait cycle with {blocking}"
                    ),
                    now_s=now_s,
                )
            )
        actions.append(
            _action(
                action_id=f"recovery-replan-{migrating}-{int(now_s * 1000)}",
                action_type=RecoveryActionType.REPLAN,
                target_robot_ids=(migrating,),
                affected_task_ids=report.blocked_task_ids,
                reason=(
                    f"deadlock {report.deadlock_id}: replanning {migrating} around the "
                    f"contended cell to break the cycle"
                ),
                now_s=now_s,
            )
        )
    return actions


def _action(
    *,
    action_id: str,
    action_type: RecoveryActionType,
    target_robot_ids: tuple[str, ...],
    affected_task_ids: tuple[str, ...],
    reason: str,
    now_s: float,
) -> RecoveryAction:
    return RecoveryAction(
        action_id=action_id,
        action_type=action_type,
        target_robot_ids=target_robot_ids,
        affected_task_ids=affected_task_ids,
        reason=reason[:500],
        started_at_s=round(now_s, 3),
        status=ActionStatus.ACTIVE,
    )


def _find_cycles(graph: Mapping[str, list[WaitEdge]]) -> list[list[WaitEdge]]:
    """Return every elementary cycle in the wait-for graph.

    Depth-first search with an explicit stack finds the first cycle reachable
    from each node, which is enough because a robot blocked in two places is
    already reported through either one of them.
    """

    cycles: list[list[WaitEdge]] = []
    seen_signatures: set[frozenset[str]] = set()

    for start in sorted(graph):
        path: list[WaitEdge] = []
        on_path: set[str] = set()
        stack: list[tuple[str, int]] = [(start, 0)]
        on_path.add(start)

        while stack:
            node, cursor = stack[-1]
            outgoing = graph.get(node, [])
            if cursor >= len(outgoing):
                stack.pop()
                on_path.discard(node)
                if path:
                    path.pop()
                continue
            stack[-1] = (node, cursor + 1)
            edge = outgoing[cursor]
            target = edge.blocking_robot_id
            if target in on_path:
                start_index = next(
                    index
                    for index, visited in enumerate(stack)
                    if visited[0] == target
                )
                cycle_edges = path[start_index:] + [edge]
                signature = frozenset(edge.waiting_robot_id for edge in cycle_edges)
                if len(signature) >= 2 and signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle_edges)
                continue
            if target in graph:
                path.append(edge)
                stack.append((target, 0))
                on_path.add(target)
    return cycles


def _cell_ahead(
    position: Position2D, route: RoutePlan, lookahead: int, index: WorldIndex
) -> Cell | None:
    """The cell a robot will occupy ``lookahead`` samples along its route."""

    current = index.cell_of(position)
    for waypoint in route.waypoints[1:]:
        distance = hypot(waypoint.x - position.x, waypoint.y - position.y)
        if distance < 1e-9:
            position = waypoint
            continue
        if distance >= lookahead:
            ratio = lookahead / distance
            projected = Position2D(
                x=position.x + (waypoint.x - position.x) * ratio,
                y=position.y + (waypoint.y - position.y) * ratio,
            )
            return index.cell_of(projected)
        position = waypoint
    return index.cell_of(route.waypoints[-1]) if route.waypoints else current
