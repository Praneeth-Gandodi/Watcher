"""Right-of-way conflict detection and deterministic resolution.

Resolution flow
---------------

1. Take every active trajectory in the fleet.
2. Find the **earliest** space-time conflict.
3. Decide which robot yields.
4. Try a **safe timing delay** first.
5. Validate the delayed trajectory against **all** other robots, using only
   their future part -- waiting at the previous position can itself create a new
   conflict, so an unvalidated delay is never accepted.

For the MVP the priority order is:

1. safe timing delay
2. safe holding position (experimental, see the bottom of this module)
3. alternate route / replan (experimental, see the bottom of this module)

Every decision is derived from explicit values and sorted robot IDs, never from
dictionary iteration order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import isfinite

from backend.safety.collision import Collision, build_segments, detect_collision
from backend.safety.trajectory import (
    Trajectory,
    hold_until,
    trajectory_future_view,
)
from backend.simulation.grid import Cell

__all__ = [
    "FleetConflict",
    "RightOfWayDecision",
    "RobotPriority",
    "WaitCheck",
    "YieldResolution",
    "can_wait_safely",
    "choose_yielding_robot",
    "clearance_time_s",
    "find_earliest_conflict",
    "resolve_conflict",
    "try_timing_resolution",
]

#: Extra clearance added after a conflict window before a robot restarts.
DEFAULT_SAFETY_MARGIN_S = 0.1

#: How many times a delay may be lengthened after the fleet vetoes it. A delay
#: long enough for one shared cell can still be too short for a cell further
#: along the same corridor, so the release time escalates until the whole
#: corridor is clear or the bound is reached.
MAX_DELAY_ATTEMPTS = 8

#: Weighting of the deterministic priority score.
_TASK_PRIORITY_WEIGHT = 100.0
_BATTERY_DEFICIT_WEIGHT = 0.5
_WAITING_TIME_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class RobotPriority:
    """Observable inputs to the right-of-way priority score.

    Higher score wins the right of way. ``task_priority`` dominates; a smaller
    battery deficit and a longer wait act as tie-breakers.
    """

    robot_id: str
    task_priority: int = 1
    battery_percent: float = 100.0
    waiting_time_s: float = 0.0

    def score(self) -> float:
        return (
            self.task_priority * _TASK_PRIORITY_WEIGHT
            + (100.0 - self.battery_percent) * _BATTERY_DEFICIT_WEIGHT
            + self.waiting_time_s * _WAITING_TIME_WEIGHT
        )


@dataclass(frozen=True, slots=True)
class FleetConflict:
    """The earliest space-time conflict found across the whole fleet."""

    robot1: str
    robot2: str
    collision: Collision

    @property
    def start_time_s(self) -> float:
        return self.collision.start_time_s

    @property
    def end_time_s(self) -> float:
        return self.collision.end_time_s

    @property
    def involved_robot_ids(self) -> tuple[str, str]:
        return (self.robot1, self.robot2)

    @property
    def cells(self) -> tuple[Cell, ...]:
        return self.collision.cells

    def involves(self, robot_id: str) -> bool:
        return robot_id in (self.robot1, self.robot2)


@dataclass(frozen=True, slots=True)
class RightOfWayDecision:
    """Which robot proceeds and which robot must yield."""

    priority_robot: str
    yield_robot: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "priority_robot": self.priority_robot,
            "yield_robot": self.yield_robot,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class WaitCheck:
    """Result of validating a candidate hold against the rest of the fleet."""

    safe: bool
    trajectory: Trajectory
    conflict_with: str | None = None
    collision: Collision | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class YieldResolution:
    """Outcome of a right-of-way resolution attempt."""

    resolved: bool
    decision: RightOfWayDecision | None
    strategy: str | None
    yield_robot: str | None
    trajectory: Trajectory | None
    reason: str


def find_earliest_conflict(
    trajectories: Mapping[str, Trajectory],
) -> FleetConflict | None:
    """Return the earliest space-time conflict across every robot pair.

    For a fleet of N robots this costs ``N * (N - 1) / 2`` comparisons, which is
    45 pairs at N=10 and remains the right choice for the MVP. Robot pairs are
    generated in sorted order, so an exact tie always resolves the same way.
    """

    robot_ids = sorted(trajectories)
    earliest: FleetConflict | None = None
    for robot1_id, robot2_id in combinations(robot_ids, 2):
        collision = detect_collision(
            trajectories[robot1_id], trajectories[robot2_id]
        )
        if collision is None:
            continue
        if earliest is None or collision.start_time_s < earliest.start_time_s:
            earliest = FleetConflict(
                robot1=robot1_id,
                robot2=robot2_id,
                collision=collision,
            )
    return earliest


def choose_yielding_robot(
    robot1_id: str,
    robot2_id: str,
    robot_info: Mapping[str, RobotPriority],
) -> RightOfWayDecision:
    """Return the deterministic right-of-way decision for one pair.

    Ties are broken by ``robot_id`` so the result never depends on mapping
    iteration order.
    """

    first = robot_info.get(robot1_id, RobotPriority(robot_id=robot1_id))
    second = robot_info.get(robot2_id, RobotPriority(robot_id=robot2_id))
    first_score = first.score()
    second_score = second.score()

    if first_score > second_score:
        priority, yielder = robot1_id, robot2_id
    elif second_score > first_score:
        priority, yielder = robot2_id, robot1_id
    elif robot1_id <= robot2_id:
        priority, yielder = robot1_id, robot2_id
    else:
        priority, yielder = robot2_id, robot1_id

    return RightOfWayDecision(
        priority_robot=priority,
        yield_robot=yielder,
        reason=(
            f"{priority} keeps right of way over {yielder} "
            f"(score {max(first_score, second_score):.3f} vs "
            f"{min(first_score, second_score):.3f})"
        ),
    )


def resolve_conflict(
    conflict: FleetConflict,
    robot_info: Mapping[str, RobotPriority],
) -> RightOfWayDecision:
    """Decide which robot in a conflict must yield."""

    if conflict is None:
        raise ValueError("a conflict is required")
    return choose_yielding_robot(
        conflict.robot1, conflict.robot2, robot_info
    )


def can_wait_safely(
    robot_id: str,
    trajectories: Mapping[str, Trajectory],
    point_index: int,
    wait_until_s: float,
    *,
    from_time_s: float | None = None,
) -> WaitCheck:
    """Validate a candidate hold against every other robot in the fleet.

    ``from_time_s`` limits the comparison to the future part of every
    trajectory, so a modified route is never re-compared against already
    elapsed history. ``conflict_with`` names the robot that vetoed the hold,
    which is what makes "waiting is not free" observable.
    """

    if robot_id not in trajectories:
        raise KeyError(f"robot {robot_id!r} has no registered trajectory")
    trajectory = trajectories[robot_id]
    try:
        candidate = hold_until(trajectory, point_index, wait_until_s)
    except ValueError as error:
        return WaitCheck(
            safe=False,
            trajectory=trajectory,
            reason=str(error),
        )

    horizon = trajectory[point_index].timestamp_s if from_time_s is None else from_time_s
    candidate_view = trajectory_future_view(candidate, horizon)
    for other_id in sorted(trajectories):
        if other_id == robot_id:
            continue
        other_view = trajectory_future_view(trajectories[other_id], horizon)
        collision = detect_collision(candidate_view, other_view)
        if collision is not None:
            return WaitCheck(
                safe=False,
                trajectory=candidate,
                conflict_with=other_id,
                collision=collision,
                reason=(
                    f"holding at point {point_index} until {wait_until_s:g}s "
                    f"conflicts with {other_id}"
                ),
            )
    return WaitCheck(
        safe=True,
        trajectory=candidate,
        reason=(
            f"holding at point {point_index} until {wait_until_s:g}s is clear "
            "of the rest of the fleet"
        ),
    )


def clearance_time_s(
    trajectory: Trajectory,
    cells: frozenset[Cell],
    from_time_s: float,
) -> float:
    """Return when a robot has finished sweeping every cell in ``cells``.

    A right-of-way delay must outlast the other robot's *whole* occupancy of the
    shared cells, not just the window that overlaps right now: with the swept
    footprint model a cell can stay occupied across several consecutive
    segments. Releasing after the first overlapping window simply recreates the
    same conflict one segment later, which is why the prototype appeared to
    "delay without ever clearing".
    """

    latest = from_time_s
    for segment in build_segments(trajectory):
        if segment.end_time_s <= from_time_s:
            continue
        if segment.swept_cells & cells:
            latest = max(latest, segment.end_time_s)
    return latest


def _escalated_release_time_s(
    trajectories: Mapping[str, Trajectory],
    check: WaitCheck,
    release_time_s: float,
    safety_margin_s: float,
) -> float:
    """Return a longer release time that accounts for a vetoing conflict.

    ``can_wait_safely`` reports the robot that blocked the candidate hold and
    the cells involved. Releasing after *that* robot finishes sweeping those
    cells turns "too short" into a concrete new deadline, which is what lets a
    two-robot corridor clear instead of deadlocking. Returns ``release_time_s``
    unchanged when no longer delay exists, so the caller can stop.
    """

    if check.collision is None or check.conflict_with is None:
        return release_time_s
    other = trajectories.get(check.conflict_with)
    if other is None:
        return release_time_s
    extended = clearance_time_s(
        other,
        frozenset(check.collision.cells),
        check.collision.start_time_s,
    ) + safety_margin_s
    return extended if extended > release_time_s else release_time_s


def try_timing_resolution(
    conflict: FleetConflict,
    trajectories: Mapping[str, Trajectory],
    robot_info: Mapping[str, RobotPriority],
    *,
    safety_margin_s: float = DEFAULT_SAFETY_MARGIN_S,
) -> YieldResolution:
    """Try to resolve a conflict with a safe timing delay only.

    The yielder chosen by priority is attempted first; if that robot cannot
    safely hold, the other robot is tried, because a slightly lower-priority
    robot yielding immediately is far better than a standstill. A vetoed hold is
    retried with a longer release time rather than abandoned.
    """

    decision = resolve_conflict(conflict, robot_info)
    shared_cells = frozenset(conflict.cells)
    clearance = max(
        clearance_time_s(
            trajectories[conflict.robot1], shared_cells, conflict.start_time_s
        ),
        clearance_time_s(
            trajectories[conflict.robot2], shared_cells, conflict.start_time_s
        ),
    )
    if not isfinite(clearance):
        return YieldResolution(
            resolved=False,
            decision=decision,
            strategy=None,
            yield_robot=None,
            trajectory=None,
            reason=(
                f"{conflict.robot1} or {conflict.robot2} occupies "
                f"{sorted(shared_cells)} permanently, so no delay can clear the "
                "conflict"
            ),
        )
    attempts: list[str] = []

    for candidate_yielder in (decision.yield_robot, decision.priority_robot):
        if candidate_yielder not in trajectories:
            continue
        segment_index = (
            conflict.collision.segment1
            if candidate_yielder == conflict.robot1
            else conflict.collision.segment2
        )
        trajectory = trajectories[candidate_yielder]
        if not 0 <= segment_index < len(trajectory):
            attempts.append(
                f"{candidate_yielder} is already at its destination, a delay "
                "cannot help"
            )
            continue
        hold_from_s = trajectory[segment_index].timestamp_s
        release_time = clearance + safety_margin_s
        for _ in range(MAX_DELAY_ATTEMPTS):
            check = can_wait_safely(
                candidate_yielder,
                trajectories,
                segment_index,
                release_time,
                from_time_s=hold_from_s,
            )
            if check.safe:
                return _resolved_delay(
                    resolution=check,
                    decision=decision,
                    candidate_yielder=candidate_yielder,
                    conflict=conflict,
                    release_time=release_time,
                )
            longer = _escalated_release_time_s(
                trajectories, check, release_time, safety_margin_s
            )
            if longer <= release_time:
                attempts.append(f"{candidate_yielder}: {check.reason}")
                break
            release_time = longer

    return YieldResolution(
        resolved=False,
        decision=decision,
        strategy=None,
        yield_robot=None,
        trajectory=None,
        reason="; ".join(attempts) or "no timing delay is available",
    )


def _resolved_delay(
    *,
    resolution: WaitCheck,
    decision: RightOfWayDecision,
    candidate_yielder: str,
    conflict: FleetConflict,
    release_time: float,
) -> YieldResolution:
    """Package a validated hold as a ``YieldResolution``."""

    resolved_decision = (
        decision
        if candidate_yielder == decision.yield_robot
        else RightOfWayDecision(
            priority_robot=decision.yield_robot,
            yield_robot=candidate_yielder,
            reason=(
                f"{candidate_yielder} yields because the preferred yielder cannot "
                f"hold safely ({decision.reason})"
            ),
        )
    )
    return YieldResolution(
        resolved=True,
        decision=resolved_decision,
        strategy="wait",
        yield_robot=candidate_yielder,
        trajectory=resolution.trajectory,
        reason=(
            f"{candidate_yielder} delays departure until {release_time:g}s: "
            f"{resolution.reason}"
        ),
    )


# ---------------------------------------------------------------------------
# FUTURE / EXPERIMENTAL SAFETY RECOVERY
#
# The helpers below are kept for continuity with the Agent 2 prototype. They
# are NOT on the MVP integration path: nothing in backend/simulation or the
# composition root imports them, and the runtime never depends on their
# behaviour. Treat them as parked work behind a higher-priority strategy
# ("safe holding position", then "alternate route") and repair them only after
# the stable path above is proven.
# ---------------------------------------------------------------------------


def trim_trajectory_from_time(trajectory: Trajectory, start_time_s: float) -> Trajectory:
    """Experimental alias of the stable :func:`trajectory_future_view`."""

    return trajectory_future_view(trajectory, start_time_s)


def find_safe_holding_position(
    robot_id: str,
    conflict: FleetConflict,
    trajectories: Mapping[str, Trajectory],
    grid,
    robot_width: int,
    robot_height: int,
    speed_mps: float,
    cell_size_m: float = 1.0,
    *,
    search_radius: int = 4,
    safety_margin_s: float = DEFAULT_SAFETY_MARGIN_S,
) -> dict[str, object]:
    """Experimental: detour to a nearby cell and hold there until released.

    Provided for continuity only. The MVP uses a timing delay, which is always
    attempted first and is validated against the whole fleet the same way.
    """

    from backend.safety.pathfinding import find_path
    from backend.safety.trajectory import create_trajectory

    if conflict is None:
        return {"found": False, "reason": "no conflict supplied"}
    segment_index = (
        conflict.collision.segment1
        if robot_id == conflict.robot1
        else conflict.collision.segment2
    )
    trajectory = trajectories.get(robot_id)
    if trajectory is None or not 0 < segment_index < len(trajectory):
        return {"found": False, "reason": "no departure point to divert from"}

    start_point = trajectory[segment_index - 1]
    goal_point = trajectory[-1]
    release_time = conflict.end_time_s + safety_margin_s

    rows = len(grid)
    columns = len(grid[0]) if rows else 0
    candidates: list[tuple[int, Cell]] = []
    for delta_x in range(-search_radius, search_radius + 1):
        for delta_y in range(-search_radius, search_radius + 1):
            cell = (start_point.cell[0] + delta_x, start_point.cell[1] + delta_y)
            if cell == start_point.cell or not 0 <= cell[0] < columns:
                continue
            if not 0 <= cell[1] < rows:
                continue
            distance = abs(delta_x) + abs(delta_y)
            candidates.append((distance, cell))
    candidates.sort()

    for _, holding_cell in candidates:
        holding_path = find_path(
            grid, start_point.cell, holding_cell, robot_width, robot_height
        )
        if not holding_path:
            continue
        holding_arrival = start_point.timestamp_s + (
            (len(holding_path) - 1) * cell_size_m / speed_mps
        )
        if holding_arrival > release_time:
            continue
        resume_path = find_path(
            grid, holding_cell, goal_point.cell, robot_width, robot_height
        )
        if not resume_path:
            continue
        combined = holding_path[:-1] + resume_path
        candidate = create_trajectory(
            combined, speed_mps, cell_size_m, start_point.timestamp_s
        )
        holding_index = len(holding_path) - 1
        holding_arrival_time = candidate[holding_index].timestamp_s
        if release_time > holding_arrival_time:
            candidate = hold_until(candidate, holding_index, release_time)

        safe = True
        for other_id in sorted(trajectories):
            if other_id == robot_id:
                continue
            other = trajectory_future_view(
                trajectories[other_id], start_point.timestamp_s
            )
            if detect_collision(
                trajectory_future_view(candidate, start_point.timestamp_s), other
            ):
                safe = False
                break
        if safe:
            return {
                "found": True,
                "position": holding_cell,
                "distance": abs(holding_cell[0] - start_point.cell[0])
                + abs(holding_cell[1] - start_point.cell[1]),
                "wait_until": release_time,
                "trajectory": candidate,
            }
    return {"found": False, "reason": "no safe holding position was reachable"}


def replan_around_conflict(
    robot_id: str,
    conflict: FleetConflict,
    trajectories: Mapping[str, Trajectory],
    grid,
    robot_width: int,
    robot_height: int,
    speed_mps: float,
    cell_size_m: float = 1.0,
) -> dict[str, object]:
    """Experimental: plan an alternate route that avoids the conflict cells."""

    from backend.safety.pathfinding import find_path
    from backend.safety.trajectory import create_trajectory

    if conflict is None:
        return {"found": False, "reason": "no conflict supplied"}
    segment_index = (
        conflict.collision.segment1
        if robot_id == conflict.robot1
        else conflict.collision.segment2
    )
    trajectory = trajectories.get(robot_id)
    if trajectory is None or not 0 < segment_index < len(trajectory):
        return {"found": False, "reason": "no departure point to replan from"}

    start_cell = trajectory[segment_index - 1].cell
    start_time_s = trajectory[segment_index - 1].timestamp_s
    goal_cell = trajectory[-1].cell

    blocked = set(conflict.cells)
    alternate_grid = tuple(
        tuple(
            True
            if (column, row) in blocked or row_cells[column]
            else False
            for column in range(len(row_cells))
        )
        for row, row_cells in enumerate(grid)
    )
    path = find_path(alternate_grid, start_cell, goal_cell, robot_width, robot_height)
    if not path:
        return {"found": False, "reason": "no alternate route exists"}
    candidate = create_trajectory(path, speed_mps, cell_size_m, start_time_s)
    for other_id in sorted(trajectories):
        if other_id == robot_id:
            continue
        if detect_collision(
            trajectory_future_view(candidate, start_time_s),
            trajectory_future_view(trajectories[other_id], start_time_s),
        ):
            return {
                "found": False,
                "reason": f"alternate route conflicts with {other_id}",
            }
    return {"found": True, "path": path, "trajectory": candidate}


def evaluate_yield_options(
    conflict: FleetConflict,
    trajectories: Mapping[str, Trajectory],
    robot_info: Mapping[str, RobotPriority],
    grid,
    safety_margin_s: float = DEFAULT_SAFETY_MARGIN_S,
) -> dict[str, dict[str, object]]:
    """Experimental: rank the timing, holding, and replanning options."""

    decision = resolve_conflict(conflict, robot_info)
    results: dict[str, dict[str, object]] = {}
    for robot_id in sorted(conflict.involved_robot_ids):
        segment_index = (
            conflict.collision.segment1
            if robot_id == conflict.robot1
            else conflict.collision.segment2
        )
        trajectory = trajectories.get(robot_id)
        options: list[dict[str, object]] = []
        if trajectory is not None and 0 <= segment_index < len(trajectory):
            wait_result = can_wait_safely(
                robot_id,
                trajectories,
                segment_index,
                conflict.end_time_s + safety_margin_s,
                from_time_s=trajectory[segment_index].timestamp_s,
            )
            if wait_result.safe:
                options.append(
                    {"possible": True, "method": "WAIT", "cost": 0, "trajectory": wait_result.trajectory}
                )
        results[robot_id] = {
            "possible": bool(options),
            "decision": decision.as_dict(),
            "options": options,
        }
    return results


def choose_safe_yielding_robot(
    conflict: FleetConflict,
    trajectories: Mapping[str, Trajectory],
    robot_info: Mapping[str, RobotPriority],
    *,
    safety_margin_s: float = DEFAULT_SAFETY_MARGIN_S,
) -> YieldResolution:
    """Experimental entry point: feasibility first, then cost, then priority."""

    return try_timing_resolution(
        conflict, trajectories, robot_info, safety_margin_s=safety_margin_s
    )


def sorted_conflict_robot_ids(conflict: FleetConflict) -> Sequence[str]:
    """Return the two involved robot IDs in sorted order."""

    return tuple(sorted(conflict.involved_robot_ids))
