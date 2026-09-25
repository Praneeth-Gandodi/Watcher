"""Deterministic grid A* planning with occupancy-aware costs.

The planner searches the canonical ``WorldState`` grid and returns a canonical
``RoutePlan``. Two properties matter for the coordination story:

* **Determinism.** Ties break on ``(f, h, cell)`` so the same observation and
  the same seed always produce the same route. Reproducible replans are what
  make deadlock and collision tests meaningful.
* **Soft occupancy.** Cells reserved by other robots are traversable at a
  penalty instead of being walls. A hard block would let a single robot seal
  an aisle and freeze the fleet behind it, which is exactly the failure mode
  this system is supposed to avoid.
"""

from __future__ import annotations

from heapq import heappop, heappush
from math import hypot

from backend.contracts.models import Position2D, RoutePlan, RouteStatus, WorldState
from backend.simulation.grid import Cell, OccupancyGrid, WorldIndex

STRATEGY_DIRECT = "astar-grid-direct"
STRATEGY_YIELD_AWARE = "astar-grid-yield-aware"
STRATEGY_FAILED = "astar-grid-failed"

ORTHOGONAL_STEP = 1.0
DIAGONAL_STEP = 1.4142135623730951

# Extra cost, in cells of travel, for entering a cell another robot has
# reserved. It only has to break ties towards a clearer aisle.
#
# It must stay small. The static heuristic cannot see reservations, so a large
# penalty makes real costs diverge sharply from the estimate, A* stops trusting
# its own guidance, and the search degenerates towards Dijkstra over the whole
# floor — which is how a 500-robot plan ended up costing 10 ms.
RESERVATION_PENALTY = 1.5
MAX_EXPANSIONS = 40000

NEIGHBOURS_8: tuple[tuple[int, int, float], ...] = (
    (1, 0, ORTHOGONAL_STEP),
    (-1, 0, ORTHOGONAL_STEP),
    (0, 1, ORTHOGONAL_STEP),
    (0, -1, ORTHOGONAL_STEP),
    (1, 1, DIAGONAL_STEP),
    (1, -1, DIAGONAL_STEP),
    (-1, 1, DIAGONAL_STEP),
    (-1, -1, DIAGONAL_STEP),
)


def heuristic(a: Cell, b: Cell) -> float:
    """Octile distance — admissible for 8-connected movement."""

    dx = abs(a.x - b.x)
    dy = abs(a.y - b.y)
    return max(dx, dy) + (DIAGONAL_STEP - 1.0) * min(dx, dy)


def build_distance_field(index: WorldIndex, goal: Cell) -> list[float]:
    """Dijkstra cost-to-go over static obstacles, indexed by flat cell offset.

    Workstations, depots, and chargers are the natural destinations in this
    world, so many robots share a goal. One field per goal turns each
    subsequent plan into a cheap A* that is strongly informed, instead of a
    fresh blind search across the whole floor. Reservation penalties are
    deliberately excluded, which keeps the field admissible for a search that
    does include them.
    """

    columns = index.columns
    open_mask = index.open_mask
    total = columns * index.rows
    distances = [float("inf")] * total
    goal_flat = goal.y * columns + goal.x
    if not open_mask[goal_flat]:
        return distances
    distances[goal_flat] = 0.0
    frontier: list[tuple[float, int]] = [(0.0, goal_flat)]
    while frontier:
        distance, flat = heappop(frontier)
        if distance > distances[flat]:
            continue
        x = flat % columns
        y = flat // columns
        for dx, dy, step_cost in NEIGHBOURS_8:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < columns and 0 <= ny < index.rows):
                continue
            nflat = ny * columns + nx
            if not open_mask[nflat]:
                continue
            if dx and dy:
                if not open_mask[y * columns + nx] or not open_mask[ny * columns + x]:
                    continue
            candidate = distance + step_cost
            if candidate < distances[nflat]:
                distances[nflat] = candidate
                heappush(frontier, (candidate, nflat))
    return distances


def plan_route(
    *,
    robot_id: str,
    task_id: str,
    origin: Position2D,
    target: Position2D,
    world: WorldState,
    occupancy: OccupancyGrid,
    planned_at_s: float,
    route_id: str,
    version: int = 1,
    index: WorldIndex | None = None,
    distance_field: list[float] | None = None,
) -> RoutePlan:
    """Plan a route from ``origin`` to ``target`` avoiding blocked cells.

    Raises ``ValueError`` when no route exists, which the runtime converts into
    an explicit recovery action rather than a silent failure.
    """

    world_index = index or WorldIndex.from_world(world)
    if occupancy.columns != world_index.columns:
        # Align the occupancy fast path with this world once, so callers do
        # not have to thread the column count through every construction site.
        occupancy.columns = world_index.columns
    start = _nearest_traversable(world_index, occupancy, world_index.cell_of(origin), robot_id)
    goal = _nearest_traversable(world_index, occupancy, world_index.cell_of(target), robot_id)
    if start is None or goal is None:
        raise ValueError("origin or target has no traversable cell")

    cells = _search(start, goal, world_index, occupancy, robot_id, distance_field)
    if cells is None:
        raise ValueError("no route exists between origin and target")

    waypoints = _string_pull(cells, world_index)
    world_waypoints = tuple(
        Position2D(x=round(x, 4), y=round(y, 4)) for x, y in waypoints
    )
    if len(world_waypoints) < 2:
        # A plan must carry at least two waypoints; a same-cell move is
        # expressed as origin then target so movement still has a direction.
        world_waypoints = (
            Position2D(x=round(origin.x, 4), y=round(origin.y, 4)),
            Position2D(x=round(target.x, 4), y=round(target.y, 4)),
        )

    saw_reservation = any(
        occupancy.is_reserved_by_other(cell, robot_id) for cell in cells
    )
    return RoutePlan(
        route_id=route_id,
        robot_id=robot_id,
        task_id=task_id,
        waypoints=world_waypoints,
        strategy=STRATEGY_YIELD_AWARE if saw_reservation else STRATEGY_DIRECT,
        status=RouteStatus.ACTIVE,
        version=version,
        planned_at_s=planned_at_s,
    )


def _nearest_traversable(
    index: WorldIndex,
    occupancy: OccupancyGrid,
    cell: Cell,
    robot_id: str,
) -> Cell | None:
    """Return ``cell`` or the closest traversable cell around it.

    A robot whose own reservation covers the cell is still allowed to start or
    end there; only static obstacles and dead zones are hard blocks.
    """

    center_x, center_y = index.center_of(cell)
    return index.nearest_traversable_cell((center_x, center_y))


def _search(
    start: Cell,
    goal: Cell,
    index: WorldIndex,
    occupancy: OccupancyGrid,
    robot_id: str,
    distance_field: list[float] | None = None,
) -> list[Cell] | None:
    """Run A* over flat arrays and return the cell path.

    The search works on integer flat offsets with a preallocated blocked mask
    instead of ``Cell`` objects and dictionary lookups. At 500 robots a plan
    is the single most frequent allocation-time operation, and the object
    overhead dominated the profile. When a shared ``distance_field`` is
    supplied it replaces the octile estimate with exact static cost-to-go,
    which usually cuts the expansion count by an order of magnitude.
    """

    if start == goal:
        return [start]

    columns = index.columns
    open_mask = index.open_mask
    owners = occupancy.flat_owners if occupancy.columns else {}
    start_flat = start.y * columns + start.x
    goal_flat = goal.y * columns + goal.x
    goal_x, goal_y = goal.x, goal.y

    def estimate(flat: int, x: int, y: int) -> float:
        if distance_field is not None:
            field_value = distance_field[flat]
            if field_value < float("inf"):
                return field_value
        dx = abs(x - goal_x)
        dy = abs(y - goal_y)
        return max(dx, dy) + (DIAGONAL_STEP - 1.0) * min(dx, dy)

    open_heap: list[tuple[float, float, int]] = [
        (estimate(start_flat, start.x, start.y), 0.0, start_flat)
    ]
    came_from: dict[int, int] = {}
    best_cost: dict[int, float] = {start_flat: 0.0}
    expansions = 0

    while open_heap:
        _, cost_so_far, current_flat = heappop(open_heap)
        if current_flat == goal_flat:
            return [
                Cell(x=flat % columns, y=flat // columns)
                for flat in _reconstruct_flat(came_from, current_flat)
            ]
        if cost_so_far > best_cost.get(current_flat, float("inf")):
            continue
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return None

        current_x = current_flat % columns
        current_y = current_flat // columns

        for dx, dy, step_cost in NEIGHBOURS_8:
            neighbour_x = current_x + dx
            neighbour_y = current_y + dy
            if not (0 <= neighbour_x < columns and 0 <= neighbour_y < index.rows):
                continue
            neighbour_flat = neighbour_y * columns + neighbour_x
            if not open_mask[neighbour_flat]:
                continue
            if dx and dy:
                # Never cut a corner between two blocked orthogonals.
                if not open_mask[current_y * columns + neighbour_x] or not open_mask[
                    neighbour_y * columns + current_x
                ]:
                    continue
            owner = owners.get(neighbour_flat)
            penalty = RESERVATION_PENALTY if (owner is not None and owner != robot_id) else 0.0
            tentative = cost_so_far + step_cost + penalty
            if tentative >= best_cost.get(neighbour_flat, float("inf")):
                continue
            best_cost[neighbour_flat] = tentative
            came_from[neighbour_flat] = current_flat
            heappush(
                open_heap,
                (tentative + estimate(neighbour_flat, neighbour_x, neighbour_y), tentative, neighbour_flat),
            )
    return None


def _reconstruct_flat(came_from: dict[int, int], goal_flat: int) -> list[int]:
    path = [goal_flat]
    while path[-1] in came_from:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def _string_pull(path: list[Cell], index: WorldIndex) -> list[tuple[float, float]]:
    """Collapse a cell path into turn-only waypoints.

    Shortening the polyline keeps route payloads small at 500 robots, and the
    straight segments stay inside the free corridor because they are validated
    by line of sight before being accepted.
    """

    if len(path) <= 2:
        return [index.center_of(cell) for cell in path]

    waypoints = [index.center_of(path[0])]
    anchor = 0
    while anchor < len(path) - 1:
        probe = len(path) - 1
        while probe > anchor + 1 and not _has_line_of_sight(path[anchor], path[probe], index):
            probe -= 1
        waypoints.append(index.center_of(path[probe]))
        anchor = probe
    return waypoints


def _has_line_of_sight(start: Cell, end: Cell, index: WorldIndex) -> bool:
    """Supercover line walk between two cells, rejecting blocked intermediates.

    A diagonal step touches the two orthogonally adjacent cells it passes
    between. Testing only the cells the walk actually lands on would let a long
    straight segment slip diagonally past the corner of a rack block, and the
    robot would then drive through it. Both intermediates are therefore checked
    whenever a step moves on both axes.
    """

    x0, y0 = start.x, start.y
    x1, y1 = end.x, end.y
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    if dx == 0 and dy == 0:
        return True
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    x, y = x0, y0

    while True:
        if (x, y) == (x1, y1):
            return True
        if index.is_blocked(Cell(x, y)):
            return False

        doubled = 2 * error
        step_x = doubled > -dy
        step_y = doubled < dx
        if step_x:
            error -= dy
        if step_y:
            error += dx
        if step_x and step_y:
            if index.is_blocked(Cell(x + sx, y)) or index.is_blocked(Cell(x, y + sy)):
                return False
        if step_x:
            x += sx
        if step_y:
            y += sy


def route_length_m(waypoints: tuple[Position2D, ...] | list[Position2D]) -> float:
    """Total polyline length in meters, used for route-efficiency metrics."""

    return sum(
        hypot(b.x - a.x, b.y - a.y) for a, b in zip(waypoints, waypoints[1:], strict=False)
    )


def straight_line_distance_m(origin: Position2D, target: Position2D) -> float:
    """Ideal travel distance, used as the route-efficiency denominator."""

    return hypot(target.x - origin.x, target.y - origin.y)

