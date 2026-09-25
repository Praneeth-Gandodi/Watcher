"""Predictive collision detection and right-of-way resolution for Agent 2.

Two ideas carry this module:

* **Predict, do not react.** Each moving robot's next ``horizon_s`` of travel is
  sampled into discrete time steps, and a conflict is raised when two robots
  occupy the same cell at the same predicted step. Sampling on the shared
  simulation clock keeps the check deterministic and replayable.
* **Resolve by protocol, not by force.** When two robots conflict, a documented
  priority order decides who keeps its path and who yields. The loser receives
  a recovery intent; it never teleports or stops permanently, so a yield
  resolves in bounded time instead of converting into a deadlock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from math import hypot

from backend.contracts.models import (
    Conflict,
    ConflictKind,
    ConflictSeverity,
    Position2D,
    ResolutionStatus,
    Robot,
    RoutePlan,
)
from backend.simulation.grid import Cell, WorldIndex

PREDICTION_HORIZON_S = 2.0
PREDICTION_STEP_S = 0.4
SAFETY_MARGIN_M = 0.6
CONFLICT_MEMORY_S = 1.5


@dataclass(frozen=True, slots=True)
class MotionIntent:
    """A robot's predicted path over the prediction horizon."""

    robot_id: str
    route: RoutePlan
    speed_mps: float
    cells: tuple[Cell, ...]
    positions: tuple[Position2D, ...]


@dataclass(frozen=True, slots=True)
class YieldDecision:
    """Who yields, to whom, and why — the input to the recovery coordinator."""

    yielding_robot_id: str
    right_of_way_robot_id: str
    position: Position2D
    reason: str
    conflict_kind: ConflictKind


@dataclass(slots=True)
class CollisionDetector:
    """Stateful detector that remembers recent conflicts to avoid spam."""

    horizon_s: float = PREDICTION_HORIZON_S
    step_s: float = PREDICTION_STEP_S
    margin_m: float = SAFETY_MARGIN_M
    memory_s: float = CONFLICT_MEMORY_S
    recent: dict[tuple[str, str], float] = field(default_factory=dict)

    def build_intent(
        self,
        robot: Robot,
        route: RoutePlan,
        *,
        speed_mps: float,
        index: WorldIndex,
    ) -> MotionIntent:
        """Sample the robot's near-future occupancy along its current route."""

        step_s = self.step_s
        # One sample at the current position plus one per prediction step, so
        # the horizon is fully covered instead of stopping a step short.
        samples = max(2, int(self.horizon_s / step_s) + 1)
        cells: list[Cell] = []
        positions: list[Position2D] = []
        position = robot.position
        cursor = 1
        for _ in range(samples):
            positions.append(position)
            cells.append(index.cell_of(position))
            position, cursor = _project(position, route.waypoints, speed_mps * step_s, cursor)
        return MotionIntent(
            robot_id=robot.robot_id,
            route=route,
            speed_mps=speed_mps,
            cells=tuple(cells),
            positions=tuple(positions),
        )

    def detect(
        self,
        intents: Sequence[MotionIntent],
        *,
        robots_by_id: dict[str, Robot],
        task_priority_by_robot: dict[str, int] | None = None,
        now_s: float,
    ) -> tuple[list[Conflict], list[YieldDecision]]:
        """Find predicted conflicts and decide who yields for each.

        Conflicts are deduplicated per robot pair and suppressed for
        ``CONFLICT_MEMORY_S`` after the first report so a single approach does
        not flood the event stream while it resolves.
        """

        self._expire(now_s)
        priorities = task_priority_by_robot or {}
        conflicts: list[Conflict] = []
        decisions: list[YieldDecision] = []

        for left_index in range(len(intents)):
            for right_index in range(left_index + 1, len(intents)):
                left = intents[left_index]
                right = intents[right_index]
                overlap = _first_overlap(left, right, self.margin_m)
                if overlap is None:
                    continue
                left_robot = robots_by_id.get(left.robot_id)
                right_robot = robots_by_id.get(right.robot_id)
                if left_robot is None or right_robot is None:
                    continue
                key = _pair_key(left.robot_id, right.robot_id)
                if key in self.recent:
                    continue
                self.recent[key] = now_s
                position, kind, severity = overlap
                conflicts.append(
                    Conflict(
                        conflict_id=_conflict_id(key, now_s),
                        kind=kind,
                        severity=severity,
                        robot_ids=_sorted_pair(left.robot_id, right.robot_id),
                        task_ids=_task_ids(left_robot, right_robot),
                        position=position,
                        status=ResolutionStatus.RESOLVING,
                        detected_at_s=round(now_s, 3),
                    )
                )
                decisions.append(
                    decide_right_of_way(
                        left_robot,
                        left.route,
                        priorities.get(left.robot_id, 1),
                        right_robot,
                        right.route,
                        priorities.get(right.robot_id, 1),
                        position=position,
                        kind=kind,
                    )
                )
        return conflicts, decisions

    def _expire(self, now_s: float) -> None:
        cutoff = now_s - self.memory_s
        for key, detected_at in list(self.recent.items()):
            if detected_at < cutoff:
                del self.recent[key]


def decide_right_of_way(
    left: Robot,
    left_route: RoutePlan,
    left_task_priority: int,
    right: Robot,
    right_route: RoutePlan,
    right_task_priority: int,
    *,
    position: Position2D,
    kind: ConflictKind,
) -> YieldDecision:
    """Return the right-of-way decision for two conflicting robots.

    The order is deliberately explainable, because an evaluator has to be able
    to argue with it:

    1. A robot that is already blocked yields, so a jam unwinds immediately.
    2. The robot whose task has the higher priority keeps its path.
    3. The robot with the longer remaining route keeps its path, because
       re-planning the shorter one disturbs less of the fleet.
    4. The robot with the lower identifier yields, which makes the outcome
       independent of iteration order and therefore reproducible.
    """

    left_score = _claim_score(left, left_route, left_task_priority)
    right_score = _claim_score(right, right_route, right_task_priority)
    if left_score > right_score:
        keeper, yielder, keeper_score, yielder_score = left, right, left_score, right_score
    else:
        keeper, yielder, keeper_score, yielder_score = right, left, right_score, left_score
    return YieldDecision(
        yielding_robot_id=yielder.robot_id,
        right_of_way_robot_id=keeper.robot_id,
        position=position,
        reason=(
            f"{keeper.robot_id} holds right of way over {yielder.robot_id}: "
            f"{_explain(keeper, keeper_score, yielder_score)}"
        ),
        conflict_kind=kind,
    )


def _claim_score(robot: Robot, route: RoutePlan, task_priority: int) -> tuple[int, int, float, str]:
    """Sortable claim on the current path; the higher score keeps moving.

    Every component is chosen so the ordering matches the documented policy:
    a moving robot outranks a stuck one, a higher-priority task outranks a
    lower one, a longer remaining route outranks a shorter one, and the
    identifier makes an otherwise exact tie reproducible.
    """

    moving = 1 if robot.status.value in {"idle", "active"} else 0
    return (moving, task_priority, _remaining_distance(route, robot.position), robot.robot_id)


def _remaining_distance(route: RoutePlan, position: Position2D) -> float:
    if not route.waypoints:
        return 0.0
    total = 0.0
    previous = position
    for waypoint in route.waypoints:
        total += hypot(waypoint.x - previous.x, waypoint.y - previous.y)
        previous = waypoint
    return total


def _explain(
    keeper: Robot,
    keeper_score: tuple[int, int, float, str],
    yielder_score: tuple[int, int, float, str],
) -> str:
    """Name the first score component that actually decided the comparison.

    Reporting a component that merely happens to be non-zero would be a false
    explanation, so the two scores are compared directly and only the deciding
    index is cited.
    """

    if keeper_score[0] != yielder_score[0]:
        return f"{keeper.robot_id} is running while the other robot is blocked"
    if keeper_score[1] != yielder_score[1]:
        return f"{keeper.robot_id} carries the higher priority task"
    if keeper_score[2] != yielder_score[2]:
        return f"{keeper.robot_id} has the longer remaining route"
    return f"{keeper.robot_id} wins the identifier tie-break"


def _first_overlap(
    left: MotionIntent, right: MotionIntent, margin_m: float
) -> tuple[Position2D, ConflictKind, ConflictSeverity] | None:
    """Return the first predicted overlap between two intents, if any."""

    samples = min(len(left.positions), len(right.positions))
    for step in range(samples):
        left_position = left.positions[step]
        right_position = right.positions[step]
        distance = hypot(
            left_position.x - right_position.x, left_position.y - right_position.y
        )
        if distance > margin_m:
            continue
        if left.cells[step] != right.cells[step]:
            # Close but in different cells: a right-of-way situation rather than
            # an imminent collision.
            midpoint = Position2D(
                x=round((left_position.x + right_position.x) / 2, 3),
                y=round((left_position.y + right_position.y) / 2, 3),
            )
            return midpoint, ConflictKind.RIGHT_OF_WAY, ConflictSeverity.WARNING
        return (
            left_position,
            ConflictKind.COLLISION_RISK,
            ConflictSeverity.CRITICAL,
        )
    return None


def _project(
    position: Position2D,
    waypoints: tuple[Position2D, ...],
    distance_m: float,
    cursor: int,
) -> tuple[Position2D, int]:
    """Advance ``position`` along the polyline and return the new waypoint cursor.

    The cursor is essential: without it every sample restarts from
    ``waypoints[0]``, which lies *behind* the robot, so the projection walks
    the robot backwards along its own route and never predicts a conflict.
    """

    remaining = distance_m
    index = max(1, cursor)
    while index < len(waypoints):
        target = waypoints[index]
        segment = hypot(target.x - position.x, target.y - position.y)
        if segment <= 1e-9:
            index += 1
            continue
        if segment <= remaining:
            position = target
            remaining -= segment
            index += 1
            continue
        ratio = remaining / segment
        return (
            Position2D(
                x=round(position.x + (target.x - position.x) * ratio, 4),
                y=round(position.y + (target.y - position.y) * ratio, 4),
            ),
            index,
        )
    return position, index


def _pair_key(left_id: str, right_id: str) -> str:
    return "|".join(_sorted_pair(left_id, right_id))


def _sorted_pair(left_id: str, right_id: str) -> tuple[str, str]:
    return (left_id, right_id) if left_id <= right_id else (right_id, left_id)


def _task_ids(left: Robot, right: Robot) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                task_id
                for task_id in (left.current_task_id, right.current_task_id)
                if task_id is not None
            }
        )
    )


def _conflict_id(key: str, now_s: float) -> str:
    safe = key.replace("|", "-").replace(" ", "-")
    return f"conflict-{safe}-{int(now_s * 1000)}"
