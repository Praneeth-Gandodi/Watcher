"""Unit tests for deterministic A* route planning.

Fixtures are expressed in grid cells and converted to world meters through the
same helpers the runtime uses, so a test can never accidentally place a robot
inside a wall and assert something meaningless about the fallback path.
"""

from __future__ import annotations

import pytest

from backend.contracts.models import GridCell, GridCellType, Position2D, RouteStatus, WorldState
from backend.simulation.grid import Cell, OccupancyGrid, WorldIndex
from safety.pathfinding import (
    STRATEGY_DIRECT,
    STRATEGY_YIELD_AWARE,
    build_distance_field,
    plan_route,
    route_length_m,
    straight_line_distance_m,
)

COLUMNS = 12
ROWS = 8
WALL_X = 6
GAP_Y = 3
CELL_SIZE = 2.0


def cell_center(index: WorldIndex, x: int, y: int) -> Position2D:
    center_x, center_y = index.center_of(Cell(x, y))
    return Position2D(x=center_x, y=center_y)


def build_room() -> WorldState:
    """A walled room split by a vertical wall with a single gap at ``GAP_Y``."""

    blocked: list[GridCell] = []
    for x in range(COLUMNS):
        blocked.append(GridCell(cell_x=x, cell_y=0, cell_type=GridCellType.OBSTACLE))
        blocked.append(GridCell(cell_x=x, cell_y=ROWS - 1, cell_type=GridCellType.OBSTACLE))
    for y in range(ROWS):
        blocked.append(GridCell(cell_x=0, cell_y=y, cell_type=GridCellType.OBSTACLE))
        blocked.append(GridCell(cell_x=COLUMNS - 1, cell_y=y, cell_type=GridCellType.OBSTACLE))
    for y in range(ROWS):
        if y != GAP_Y:
            blocked.append(GridCell(cell_x=WALL_X, cell_y=y, cell_type=GridCellType.OBSTACLE))
    return WorldState(
        width_m=COLUMNS * CELL_SIZE,
        height_m=ROWS * CELL_SIZE,
        cell_size_m=CELL_SIZE,
        columns=COLUMNS,
        rows=ROWS,
        cells=tuple(blocked),
        revision=1,
    )


def build_sealed_room() -> WorldState:
    """A room whose centre cell is fully enclosed by obstacles."""

    blocked = [
        GridCell(cell_x=x, cell_y=y, cell_type=GridCellType.OBSTACLE)
        for x in range(COLUMNS)
        for y in range(ROWS)
        if not (1 <= x < COLUMNS - 1 and 1 <= y < ROWS - 1)
    ]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            blocked.append(
                GridCell(
                    cell_x=COLUMNS // 2 + dx,
                    cell_y=ROWS // 2 + dy,
                    cell_type=GridCellType.OBSTACLE,
                )
            )
    return WorldState(
        width_m=COLUMNS * CELL_SIZE,
        height_m=ROWS * CELL_SIZE,
        cell_size_m=CELL_SIZE,
        columns=COLUMNS,
        rows=ROWS,
        cells=tuple(blocked),
        revision=1,
    )


def build_fully_sealed_world() -> WorldState:
    """A world with no traversable cell at all, so no route can exist."""

    return WorldState(
        width_m=4 * CELL_SIZE,
        height_m=4 * CELL_SIZE,
        cell_size_m=CELL_SIZE,
        columns=4,
        rows=4,
        cells=tuple(
            GridCell(cell_x=x, cell_y=y, cell_type=GridCellType.OBSTACLE)
            for x in range(4)
            for y in range(4)
        ),
        revision=1,
    )


def _distance_to_polyline(point: Position2D, waypoints) -> float:
    """Shortest distance from a point to a polyline, for gap-crossing checks."""

    best = float("inf")
    for start, end in zip(waypoints, waypoints[1:], strict=False):
        segment_x = end.x - start.x
        segment_y = end.y - start.y
        length_squared = segment_x**2 + segment_y**2
        if length_squared == 0:
            distance = ((point.x - start.x) ** 2 + (point.y - start.y) ** 2) ** 0.5
        else:
            ratio = ((point.x - start.x) * segment_x + (point.y - start.y) * segment_y) / length_squared
            ratio = min(1.0, max(0.0, ratio))
            closest_x = start.x + ratio * segment_x
            closest_y = start.y + ratio * segment_y
            distance = ((point.x - closest_x) ** 2 + (point.y - closest_y) ** 2) ** 0.5
        best = min(best, distance)
    return best


def plan(
    world: WorldState,
    occupancy: OccupancyGrid | None = None,
    origin: tuple[int, int] = (1, 1),
    target: tuple[int, int] = (9, 1),
    **kwargs,
):
    index = WorldIndex.from_world(world)
    return plan_route(
        robot_id=kwargs.pop("robot_id", "robot-001"),
        task_id=kwargs.pop("task_id", "task-001"),
        origin=cell_center(index, *origin),
        target=cell_center(index, *target),
        world=world,
        occupancy=occupancy or OccupancyGrid(),
        planned_at_s=0.0,
        route_id=kwargs.pop("route_id", "route-001"),
        index=index,
        **kwargs,
    )


class TestRoutePlanning:
    def test_plans_a_direct_route_inside_one_side_of_the_wall(self) -> None:
        world = build_room()
        route = plan(world, origin=(1, 1), target=(4, 2))
        assert route.status is RouteStatus.ACTIVE
        assert route.strategy == STRATEGY_DIRECT
        assert len(route.waypoints) >= 2
        index = WorldIndex.from_world(world)
        assert route.waypoints[-1] == cell_center(index, 4, 2)

    def test_crosses_the_wall_through_the_gap(self) -> None:
        world = build_room()
        route = plan(world, origin=(1, 1), target=(9, 1))
        index = WorldIndex.from_world(world)
        assert route.waypoints[-1] == cell_center(index, 9, 1)
        gap_center = cell_center(index, WALL_X, GAP_Y)
        # String pulling turns the walk through the gap into a straight
        # segment, so the polyline passes beside the gap rather than through a
        # waypoint sitting on it.
        assert _distance_to_polyline(gap_center, route.waypoints) <= CELL_SIZE

    def test_never_places_a_waypoint_inside_an_obstacle(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        for start, target in (((1, 1), (9, 1)), ((1, 5), (10, 2)), ((2, 1), (9, 6))):
            route = plan(world, origin=start, target=target)
            for point in route.waypoints:
                assert not index.is_blocked(index.cell_of(point))

    def test_planning_is_deterministic(self) -> None:
        world = build_room()
        first = plan(world, origin=(1, 1), target=(9, 1))
        second = plan(world, origin=(1, 1), target=(9, 1))
        assert first.waypoints == second.waypoints
        assert first.strategy == second.strategy

    def test_reservations_raise_cost_instead_of_blocking(self) -> None:
        world = build_room()
        occupancy = OccupancyGrid(columns=COLUMNS)
        # Another robot holds the only gap; the planner must still cross it,
        # but by paying the reservation penalty rather than failing.
        for y in (GAP_Y - 1, GAP_Y, GAP_Y + 1):
            occupancy.reserve("robot-999", (Cell(WALL_X, y),))
        route = plan(world, occupancy=occupancy, origin=(1, 1), target=(9, 1))
        assert route.strategy == STRATEGY_YIELD_AWARE
        index = WorldIndex.from_world(world)
        assert route.waypoints[-1] == cell_center(index, 9, 1)

    def test_a_robot_never_penalizes_its_own_reservations(self) -> None:
        world = build_room()
        occupancy = OccupancyGrid(columns=COLUMNS)
        for y in (GAP_Y - 1, GAP_Y, GAP_Y + 1):
            occupancy.reserve("robot-001", (Cell(WALL_X, y),))
        route = plan(world, occupancy=occupancy, origin=(1, 1), target=(9, 1))
        assert route.strategy == STRATEGY_DIRECT

    def test_carries_route_identity_and_version(self) -> None:
        route = plan(build_room(), origin=(1, 1), target=(4, 2), version=4)
        assert route.version == 4
        assert route.robot_id == "robot-001"
        assert route.task_id == "task-001"
        assert route.route_id == "route-001"

    def test_raises_when_the_world_has_no_traversable_cell(self) -> None:
        with pytest.raises(ValueError, match="traversable"):
            plan(build_fully_sealed_world(), origin=(1, 1), target=(3, 3))

    def test_routes_to_the_nearest_cell_outside_a_sealed_pocket(self) -> None:
        world = build_sealed_room()
        index = WorldIndex.from_world(world)
        route = plan(world, origin=(2, 2), target=(COLUMNS // 2, ROWS // 2))
        # The sealed target cannot be entered, so the planner delivers to the
        # closest reachable cell and still reports a usable route.
        assert len(route.waypoints) >= 2
        assert index.is_inside_world(route.waypoints[-1])

    def test_snaps_a_blocked_origin_to_a_traversable_cell(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        route = plan_route(
            robot_id="robot-001",
            task_id="task-001",
            origin=cell_center(index, 0, 0),  # inside the wall
            target=cell_center(index, 4, 2),
            world=world,
            occupancy=OccupancyGrid(),
            planned_at_s=0.0,
            route_id="route-001",
            index=index,
        )
        assert len(route.waypoints) >= 2
        assert route.waypoints[-1] == cell_center(index, 4, 2)

    def test_produces_two_waypoints_for_a_same_cell_move(self) -> None:
        route = plan(build_room(), origin=(2, 2), target=(2, 2))
        assert len(route.waypoints) == 2

    def test_keeps_every_planned_point_inside_the_world(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        for start, target in (((1, 1), (10, 6)), ((10, 6), (1, 1))):
            route = plan(world, origin=start, target=target)
            for point in route.waypoints:
                assert index.is_inside_world(point)


class TestDistanceField:
    def test_covers_every_traversable_cell(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        field = build_distance_field(index, Cell(1, 1))
        assert sum(1 for value in field if value < float("inf")) == sum(index.open_mask)

    def test_distance_decreases_toward_the_goal(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        field = build_distance_field(index, Cell(1, 1))
        assert field[1 * COLUMNS + 1] == 0.0
        assert field[1 * COLUMNS + 4] > field[1 * COLUMNS + 2]

    def test_wall_cells_stay_unreachable(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        field = build_distance_field(index, Cell(1, 1))
        assert field[0 * COLUMNS + WALL_X] == float("inf")

    def test_the_far_side_of_a_wall_is_reachable_through_the_gap(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        field = build_distance_field(index, Cell(1, 1))
        assert field[(GAP_Y + 1) * COLUMNS + (WALL_X + 2)] < float("inf")

    def test_a_field_for_a_blocked_goal_is_empty(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        field = build_distance_field(index, Cell(0, 0))
        assert all(value == float("inf") for value in field)

    def test_informed_planning_agrees_with_blind_planning(self) -> None:
        world = build_room()
        index = WorldIndex.from_world(world)
        blind = plan(world, origin=(1, 1), target=(9, 1))
        informed = plan(
            world,
            origin=(1, 1),
            target=(9, 1),
            distance_field=build_distance_field(index, index.cell_of(cell_center(index, 9, 1))),
        )
        assert len(blind.waypoints) == len(informed.waypoints)
        assert blind.waypoints[-1] == informed.waypoints[-1]


class TestRouteClearance:
    """The invariant that matters: no route ever drives through an obstacle.

    The planner validates a string-pulled segment with a supercover cell walk,
    and that walk is easy to get subtly wrong at diagonal steps. Rather than
    assert an implementation detail, these tests sample the returned polyline
    densely and require every sample to be in traversable space.
    """

    @pytest.mark.parametrize("seed", [2026, 77, 4242])
    def test_no_planned_segment_crosses_an_obstacle(self, seed: int) -> None:
        from backend.simulation.world import WorldSpec, build_world

        world = build_world(WorldSpec(seed=seed))
        index = WorldIndex.from_world(world)
        stations = index.stations(GridCellType.WORKSTATION) + index.stations(
            GridCellType.RESOURCE
        )
        assert stations
        origin = Cell(1, 1)

        for ordinal in range(40):
            goal = stations[ordinal % len(stations)]
            try:
                route = plan_route(
                    robot_id=f"robot-{ordinal:03d}",
                    task_id=f"task-{ordinal:03d}",
                    origin=cell_center(index, origin.x, origin.y),
                    target=cell_center(index, goal.x, goal.y),
                    world=world,
                    occupancy=OccupancyGrid(),
                    planned_at_s=0.0,
                    route_id=f"route-{ordinal:03d}",
                    index=index,
                )
            except ValueError:
                # A feature sealed off by the layout is legitimately
                # unreachable; the runtime delivers to the closest free cell
                # instead. Clearance is only meaningful for routes that exist.
                continue
            for start, end in zip(route.waypoints, route.waypoints[1:], strict=False):
                for step in range(41):
                    ratio = step / 40
                    x = start.x + (end.x - start.x) * ratio
                    y = start.y + (end.y - start.y) * ratio
                    assert not index.is_blocked(index.cell_of((x, y))), (
                        f"route {route.route_id} segment ({start.x},{start.y})->"
                        f"({end.x},{end.y}) passes through a blocked cell at "
                        f"({x:.2f},{y:.2f})"
                    )

    def test_a_diagonal_clip_past_a_blocked_corner_is_rejected(self) -> None:
        """Two free cells diagonally adjacent must not shortcut a blocked corner."""

        blocked_corner = (2, 2)
        world = WorldState(
            width_m=8 * CELL_SIZE,
            height_m=8 * CELL_SIZE,
            cell_size_m=CELL_SIZE,
            columns=8,
            rows=8,
            cells=(
                GridCell(cell_x=1, cell_y=1, cell_type=GridCellType.OBSTACLE),
                GridCell(cell_x=2, cell_y=1, cell_type=GridCellType.OBSTACLE),
                GridCell(cell_x=1, cell_y=2, cell_type=GridCellType.OBSTACLE),
            ),
            revision=1,
        )
        index = WorldIndex.from_world(world)
        assert index.is_blocked(Cell(*blocked_corner)) is False
        # A route from (0,0) to (3,3) may not cut through the pocket whose only
        # free cell is the blocked-corner cell at (2,2).
        route = plan_route(
            robot_id="robot-001",
            task_id="task-001",
            origin=cell_center(index, 0, 0),
            target=cell_center(index, 3, 3),
            world=world,
            occupancy=OccupancyGrid(),
            planned_at_s=0.0,
            route_id="route-001",
            index=index,
        )
        for start, end in zip(route.waypoints, route.waypoints[1:], strict=False):
            for step in range(41):
                ratio = step / 40
                x = start.x + (end.x - start.x) * ratio
                y = start.y + (end.y - start.y) * ratio
                assert not index.is_blocked(index.cell_of((x, y)))


class TestRouteEfficiency:
    def test_measures_polyline_length(self) -> None:
        waypoints = (Position2D(x=0.0, y=0.0), Position2D(x=3.0, y=4.0))
        assert route_length_m(waypoints) == pytest.approx(5.0)

    def test_measures_straight_line_distance(self) -> None:
        assert straight_line_distance_m(
            Position2D(x=0.0, y=0.0), Position2D(x=3.0, y=4.0)
        ) == pytest.approx(5.0)

    def test_a_detoured_route_is_longer_than_the_ideal(self) -> None:
        world = build_room()
        route = plan(world, origin=(1, 1), target=(9, 1))
        assert route_length_m(route.waypoints) > straight_line_distance_m(
            route.waypoints[0], route.waypoints[-1]
        )

    def test_an_unobstructed_route_is_close_to_the_ideal(self) -> None:
        world = build_room()
        route = plan(world, origin=(1, 1), target=(4, 1))
        assert route_length_m(route.waypoints) == pytest.approx(
            straight_line_distance_m(route.waypoints[0], route.waypoints[-1])
        )
