"""Footprint-aware A* spatial route planning.

A* answers **where** a robot travels. Timing is
:mod:`backend.safety.trajectory`'s job, so nothing here knows about speed.

Grid convention
---------------

* ``grid[cell_y][cell_x]`` is ``True`` when the cell is impassable.
* A robot cell position is the **top-left anchor** of its footprint. A
  ``width_cells x height_cells`` robot anchored at ``(3, 4)`` occupies
  ``(3,4) (3,5) (4,4) (4,5)`` -- the footprint grows along ``+x`` for
  ``width_cells`` and along ``+y`` for ``height_cells``.
* A candidate position is valid only when the *entire* footprint is inside the
  world and free of obstacles. Negative and out-of-bounds anchors are rejected
  before any list is indexed, so the search can never index a negative grid
  position.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass

from backend.contracts.models import Position2D, RoutePlan, RouteStatus, WorldState
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.simulation.grid import Cell, GridIndex, OccupancyGrid, footprint_cells

__all__ = [
    "AStarPathPlanner",
    "PathStep",
    "astar_route_plan",
    "find_path",
    "get_neighbors",
    "is_valid_position",
    "manhattan_distance",
    "path_cells",
]

#: Strategy identifier published on every ``RoutePlan`` produced here.
ASTAR_STRATEGY = "grid-astar-footprint"

#: Neighbour order is fixed so that equal-cost paths are reproducible.
_NEIGHBOUR_OFFSETS: tuple[tuple[int, int], ...] = ((1, 0), (0, 1), (-1, 0), (0, -1))


@dataclass(frozen=True, slots=True)
class PathStep:
    """One reachable footprint placement along an A* route."""

    cell: Cell
    occupied_cells: tuple[Cell, ...]

    @property
    def x(self) -> int:
        return self.cell[0]

    @property
    def y(self) -> int:
        return self.cell[1]


def manhattan_distance(a: Cell, b: Cell) -> int:
    """Admissible four-connected heuristic for a uniform-cost grid."""

    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _grid_shape(grid: OccupancyGrid) -> tuple[int, int]:
    """Return ``(rows, columns)`` for a ``grid[cell_y][cell_x]`` grid."""

    if not grid or not grid[0]:
        raise ValueError("grid must contain at least one row and one column")
    rows = len(grid)
    columns = len(grid[0])
    for row in grid:
        if len(row) != columns:
            raise ValueError("grid rows must all have the same length")
    return rows, columns


def is_valid_position(
    position: Cell,
    grid: OccupancyGrid,
    robot_width: int = 1,
    robot_height: int = 1,
) -> bool:
    """Return whether a whole footprint can legally occupy ``position``.

    ``position`` is the top-left anchor of the footprint. Negative anchors are
    rejected explicitly before any indexing happens.
    """

    if robot_width < 1 or robot_height < 1:
        raise ValueError("a robot footprint must cover at least one cell")
    rows, columns = _grid_shape(grid)
    anchor_x, anchor_y = position
    if anchor_x < 0 or anchor_y < 0:
        return False
    if anchor_x + robot_width > columns:
        return False
    if anchor_y + robot_height > rows:
        return False
    for cell_x, cell_y in footprint_cells(position, robot_width, robot_height):
        if grid[cell_y][cell_x]:
            return False
    return True


def get_neighbors(
    position: Cell,
    grid: OccupancyGrid,
    robot_width: int = 1,
    robot_height: int = 1,
) -> tuple[Cell, ...]:
    """Return the legal four-connected neighbours of a footprint anchor."""

    anchor_x, anchor_y = position
    return tuple(
        (anchor_x + offset_x, anchor_y + offset_y)
        for offset_x, offset_y in _NEIGHBOUR_OFFSETS
        if is_valid_position(
            (anchor_x + offset_x, anchor_y + offset_y),
            grid,
            robot_width,
            robot_height,
        )
    )


def find_path(
    grid: OccupancyGrid,
    start: Cell,
    goal: Cell,
    robot_width: int = 1,
    robot_height: int = 1,
) -> tuple[PathStep, ...] | None:
    """Plan a footprint-aware A* route from ``start`` to ``goal``.

    Returns ``None`` when the start or the goal is illegal, or when no route
    exists. The returned steps always include the start and the goal.
    """

    if not is_valid_position(start, grid, robot_width, robot_height):
        return None
    if not is_valid_position(goal, grid, robot_width, robot_height):
        return None
    if start == goal:
        return (
            PathStep(cell=start, occupied_cells=footprint_cells(start, robot_width, robot_height)),
        )

    open_set: list[tuple[int, int, int, int, Cell]] = []
    heapq.heappush(open_set, (manhattan_distance(start, goal), 0, start[0], start[1], start))
    came_from: dict[Cell, Cell] = {}
    g_score: dict[Cell, int] = {start: 0}
    closed: set[Cell] = set()

    while open_set:
        # ``x``/``y`` are part of the heap key only to break score ties
        # deterministically; the cell tuple already carries them.
        _, current_cost, _, _, current = heapq.heappop(open_set)
        if current in closed:
            continue
        closed.add(current)
        if current == goal:
            return _reconstruct(came_from, current, robot_width, robot_height)

        for neighbour in get_neighbors(current, grid, robot_width, robot_height):
            if neighbour in closed:
                continue
            tentative_cost = current_cost + 1
            known_cost = g_score.get(neighbour)
            if known_cost is not None and tentative_cost >= known_cost:
                continue
            came_from[neighbour] = current
            g_score[neighbour] = tentative_cost
            heapq.heappush(
                open_set,
                (
                    tentative_cost + manhattan_distance(neighbour, goal),
                    tentative_cost,
                    neighbour[0],
                    neighbour[1],
                    neighbour,
                ),
            )
    return None


def _reconstruct(
    came_from: dict[Cell, Cell],
    goal: Cell,
    robot_width: int,
    robot_height: int,
) -> tuple[PathStep, ...]:
    cells: list[Cell] = [goal]
    while cells[-1] in came_from:
        cells.append(came_from[cells[-1]])
    cells.reverse()
    return tuple(
        PathStep(
            cell=cell,
            occupied_cells=footprint_cells(cell, robot_width, robot_height),
        )
        for cell in cells
    )


def path_cells(path: Sequence[PathStep]) -> tuple[Cell, ...]:
    """Return only the anchor cells of a planned path."""

    return tuple(step.cell for step in path)


def astar_route_plan(
    grid_index: GridIndex,
    profile: RobotProfile,
    robot_id: str,
    task_id: str,
    origin: Position2D,
    target: Position2D,
    planned_at_s: float,
    *,
    route_id: str,
    version: int = 1,
) -> RoutePlan:
    """Adapt ``WorldState`` + a profile to a canonical ``RoutePlan``.

    The returned waypoints use the cell-centre world convention documented in
    :mod:`backend.simulation.grid`. When no route exists the plan is still a
    valid ``RoutePlan`` with ``RouteStatus.INVALID`` so that the snapshot and
    the event stream stay total functions of the world.
    """

    if not isinstance(grid_index, GridIndex):
        raise TypeError("grid_index must be a GridIndex")
    if planned_at_s < 0:
        raise ValueError("planned_at_s must not be negative")

    start = grid_index.cell_for_position(origin)
    goal = grid_index.cell_for_position(target)
    path = find_path(
        grid_index.occupancy,
        start,
        goal,
        profile.width_cells,
        profile.height_cells,
    )

    if path is None:
        return RoutePlan(
            route_id=route_id,
            robot_id=robot_id,
            task_id=task_id,
            waypoints=(origin, target),
            strategy=ASTAR_STRATEGY,
            status=RouteStatus.INVALID,
            version=version,
            planned_at_s=planned_at_s,
        )

    waypoints = tuple(
        grid_index.position_for_cell(step.cell) for step in path
    )
    return RoutePlan(
        route_id=route_id,
        robot_id=robot_id,
        task_id=task_id,
        waypoints=waypoints,
        strategy=ASTAR_STRATEGY,
        status=RouteStatus.PROPOSED,
        version=version,
        planned_at_s=planned_at_s,
    )


class AStarPathPlanner:
    """``PathPlanner`` implementation backed by footprint-aware A*.

    The profile registry supplies footprint and speed metadata that the frozen
    ``Robot`` contract does not carry. Route versions are tracked per
    ``(robot_id, task_id)`` so replans publish an incremented version.
    """

    def __init__(
        self,
        profiles: RobotProfileRegistry | None = None,
        *,
        version_factory=None,
    ) -> None:
        self._profiles = profiles or RobotProfileRegistry()
        self._versions: dict[tuple[str, str], int] = {}
        self._version_factory = version_factory or self._default_route_id

    @staticmethod
    def _default_route_id(robot_id: str, task_id: str, version: int) -> str:
        return f"route-{robot_id}-{task_id}-v{version}"

    def plan_sync(
        self,
        robot_id: str,
        task_id: str,
        origin: Position2D,
        target: Position2D,
        world: WorldState,
        planned_at_s: float,
        *,
        version: int | None = None,
    ) -> RoutePlan:
        """Synchronous planning core, reused by the runtime and the protocol."""

        if version is None:
            key = (robot_id, task_id)
            version = self._versions.get(key, 0) + 1
        self._versions[(robot_id, task_id)] = version
        return astar_route_plan(
            GridIndex(world),
            self._profiles.get(robot_id),
            robot_id,
            task_id,
            origin,
            target,
            planned_at_s,
            route_id=self._version_factory(robot_id, task_id, version),
            version=version,
        )

    async def plan(
        self,
        robot_id: str,
        task_id: str,
        origin: Position2D,
        target: Position2D,
        world: WorldState,
        planned_at_s: float,
    ) -> RoutePlan:
        """Implement the canonical ``PathPlanner`` protocol."""

        return self.plan_sync(
            robot_id,
            task_id,
            origin,
            target,
            world,
            planned_at_s,
        )

    def reset_versions(self) -> None:
        """Forget every tracked route version, e.g. on a simulation reset."""

        self._versions.clear()
