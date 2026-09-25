"""Test plan A and B: obstacle avoidance, footprint, bounds, and robot sizes.

Covers:
* a valid route exists and is returned
* the route never enters an obstacle
* the path respects the robot footprint
* negative coordinates are rejected before any grid indexing
* out-of-bounds footprints are rejected
* 1x1, 2x2, and 3x2 bodies all route
* a narrow corridor rejects a robot that cannot fit
"""

from __future__ import annotations

import asyncio

import pytest
from backend.contracts.models import RouteStatus
from backend.safety.pathfinding import (
    ASTAR_STRATEGY,
    AStarPathPlanner,
    find_path,
    get_neighbors,
    is_valid_position,
    manhattan_distance,
    path_cells,
)
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.simulation.grid import GridIndex, footprint_cells
from tests.unit.safety.conftest import (
    free_grid,
    grid_with_obstacles,
    make_blueprint,
    wall_column_obstacles,
    world_with_obstacles,
)


# ----------------------------------------------------------------------
# A. obstacle avoidance, footprint, and bounds
# ----------------------------------------------------------------------


def test_valid_route_exists_across_an_open_world() -> None:
    path = find_path(free_grid(6, 6), (0, 0), (5, 5), 1, 1)

    assert path is not None
    cells = path_cells(path)
    assert cells[0] == (0, 0)
    assert cells[-1] == (5, 5)
    assert len(cells) == 11  # 10 moves + the start placement
    assert all(
        manhattan_distance(start, end) == 1
        for start, end in zip(cells, cells[1:])
    ), "A* must move one cell at a time"


def test_route_never_enters_an_obstacle_and_uses_the_only_gap() -> None:
    obstacles = wall_column_obstacles(10, 8, 5, 2)
    grid = grid_with_obstacles(10, 8, obstacles)

    path = find_path(grid, (1, 2), (8, 2), 1, 1)

    assert path is not None
    cells = path_cells(path)
    assert cells[0] == (1, 2)
    assert cells[-1] == (8, 2)
    for cell_x, cell_y in cells:
        assert not grid[cell_y][cell_x], f"route entered obstacle at {cell_x, cell_y}"
    assert (5, 2) in cells, "the only gap in the wall must be used"


def test_path_respects_robot_footprint_not_just_the_anchor() -> None:
    # A single blocked cell at (4, 2). A 1x1 robot travelling along row 2 must
    # detour through (4, 1) or (4, 3). A 3x2 robot travelling along rows 1-2
    # must leave row 2 entirely, because its footprint covers (4, 2) when the
    # anchor sits on row 1 even though no anchor ever sits on the obstacle.
    grid = grid_with_obstacles(9, 5, [(4, 2)])

    single = find_path(grid, (2, 2), (6, 2), 1, 1)
    wide = find_path(grid, (1, 1), (5, 1), 3, 2)

    assert single is not None
    assert wide is not None
    assert (4, 2) not in path_cells(single)
    assert {(4, 1), (4, 3)} & set(path_cells(single)), (
        "a 1x1 robot must detour around the blocked cell"
    )
    for step in wide:
        assert len(step.occupied_cells) == 6
        assert (4, 2) not in step.occupied_cells, (
            "the 3x2 body must never cover the blocked cell, even though no "
            "anchor ever sits on it"
        )
        for cell_x, cell_y in step.occupied_cells:
            assert not grid[cell_y][cell_x], (
                f"3x2 footprint entered blocked cell {cell_x, cell_y}"
            )


def test_two_by_two_footprint_anchored_at_three_four_covers_the_documented_cells() -> None:
    # The documented convention: a 2x2 robot at (3,4) occupies
    # (3,4) (3,5) (4,4) (4,5) -- the footprint grows along +x and +y.
    assert footprint_cells((3, 4), 2, 2) == ((3, 4), (4, 4), (3, 5), (4, 5))


def test_negative_anchor_is_rejected_without_indexing_the_grid() -> None:
    grid = free_grid(4, 4)

    assert is_valid_position((-1, 0), grid, 1, 1) is False
    assert is_valid_position((0, -1), grid, 1, 1) is False
    assert is_valid_position((-1, -1), grid, 2, 2) is False
    assert get_neighbors((0, 0), grid, 1, 1) == ((1, 0), (0, 1))
    # A start or goal that is negative must return None, not raise.
    assert find_path(grid, (-1, 0), (3, 3), 1, 1) is None
    assert find_path(grid, (0, 0), (-1, 3), 1, 1) is None
    # Even a negative neighbour of a valid cell is filtered out.
    assert (-1, 0) not in get_neighbors((0, 0), grid, 1, 1)


def test_out_of_bounds_footprint_is_rejected() -> None:
    grid = free_grid(5, 4)

    assert is_valid_position((4, 3), grid, 1, 1) is True
    assert is_valid_position((4, 3), grid, 2, 1) is False
    assert is_valid_position((3, 3), grid, 1, 2) is False
    assert is_valid_position((0, 0), grid, 6, 1) is False
    assert find_path(grid, (0, 0), (4, 3), 2, 2) is None


def test_unreachable_goal_returns_none() -> None:
    obstacles = wall_column_obstacles(6, 5, 3, 2)
    # Seal the gap too: the goal is then isolated from the start.
    obstacles = (*obstacles, (2, 2), (4, 2))
    grid = grid_with_obstacles(6, 5, obstacles)

    assert find_path(grid, (0, 2), (5, 2), 1, 1) is None


def test_manhattan_heuristic_matches_grid_distance() -> None:
    assert manhattan_distance((0, 0), (3, 4)) == 7
    assert manhattan_distance((2, 2), (2, 2)) == 0


def test_path_planning_is_deterministic_for_equal_cost_routes() -> None:
    grid = free_grid(7, 7)

    first = find_path(grid, (0, 0), (6, 0), 1, 1)
    second = find_path(grid, (0, 0), (6, 0), 1, 1)

    assert first == second
    assert first is not None


# ----------------------------------------------------------------------
# B. different robot sizes
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width_cells", "height_cells", "expected_footprint", "goal"),
    [(1, 1, 1, (9, 7)), (2, 2, 4, (8, 6)), (3, 2, 6, (7, 6))],
)
def test_every_supported_body_size_routes_and_reports_its_footprint(
    width_cells: int,
    height_cells: int,
    expected_footprint: int,
    goal: tuple[int, int],
) -> None:
    grid = free_grid(10, 8)

    path = find_path(grid, (0, 0), goal, width_cells, height_cells)

    assert path is not None
    for step in path:
        assert len(step.occupied_cells) == expected_footprint
        for cell_x, cell_y in step.occupied_cells:
            assert 0 <= cell_x < 10
            assert 0 <= cell_y < 8


def test_a_robot_larger_than_the_world_never_routes() -> None:
    grid = free_grid(3, 3)

    assert find_path(grid, (0, 0), (2, 2), 3, 3) is None
    assert find_path(grid, (0, 0), (2, 2), 4, 1) is None


def test_narrow_corridor_rejects_a_robot_that_cannot_fit() -> None:
    # A one-cell-wide corridor at row 4 through an otherwise solid wall.
    obstacles = tuple((column, row) for column in range(10) for row in range(8) if row != 4)
    grid = grid_with_obstacles(10, 8, obstacles)

    narrow = find_path(grid, (0, 4), (9, 4), 1, 1)
    too_wide = find_path(grid, (0, 4), (9, 4), 2, 1)
    too_tall = find_path(grid, (0, 4), (9, 4), 1, 2)

    assert narrow is not None, "a 1x1 robot fits through a one-cell corridor"
    assert too_wide is None, "a 2x1 robot must not squeeze into a one-cell corridor"
    assert too_tall is None, "a 1x2 robot must not squeeze into a one-cell corridor"


def test_wider_robot_needs_a_wider_gap_in_a_wall() -> None:
    # Column 4 is a wall whose free rows are the only route across.
    one_cell_gap = grid_with_obstacles(
        8, 5, wall_column_obstacles(8, 5, 4, 2)
    )
    two_cell_gap = grid_with_obstacles(8, 5, ((4, 0), (4, 3), (4, 4)))

    assert find_path(one_cell_gap, (0, 2), (6, 2), 2, 2) is None
    assert find_path(two_cell_gap, (0, 2), (6, 2), 2, 2) is not None


# ----------------------------------------------------------------------
# PathPlanner protocol adapter
# ----------------------------------------------------------------------


def test_planner_returns_a_canonical_route_plan(wall_world: object) -> None:
    world = wall_world
    profiles = RobotProfileRegistry(
        (RobotProfile("robot-001", 1, 1, 1.0),)
    )
    planner = AStarPathPlanner(profiles)
    index = GridIndex(world)

    route = asyncio.run(
        planner.plan(
            "robot-001",
            "task-001",
            index.position_for_cell((1, 2)),
            index.position_for_cell((8, 2)),
            world,
            2.5,
        )
    )

    assert route.route_id == "route-robot-001-task-001-v1"
    assert route.strategy == ASTAR_STRATEGY
    assert route.status is RouteStatus.PROPOSED
    assert route.version == 1
    assert route.planned_at_s == 2.5
    assert len(route.waypoints) >= 2
    assert route.waypoints[0] == index.position_for_cell((1, 2))
    assert route.waypoints[-1] == index.position_for_cell((8, 2))
    # Waypoints are cell centres, so they round-trip back onto the same cells.
    assert tuple(index.cell_for_position(point) for point in route.waypoints) == (
        path_cells(find_path(index.occupancy, (1, 2), (8, 2), 1, 1))
    )


def test_planner_uses_the_profile_footprint_and_speed_is_not_its_concern() -> None:
    obstacles = wall_column_obstacles(10, 8, 5, 2)
    world = world_with_obstacles(10, 8, obstacles)
    index = GridIndex(world)
    # A 3x2 body cannot use the one-cell gap, so no route may exist.
    planner = AStarPathPlanner(
        RobotProfileRegistry((RobotProfile("robot-001", 3, 2, 5.0),))
    )

    route = planner.plan_sync(
        "robot-001",
        "task-001",
        index.position_for_cell((1, 2)),
        index.position_for_cell((8, 2)),
        world,
        0.0,
    )

    assert route.status is RouteStatus.INVALID
    # An invalid plan still carries origin and target so callers stay total.
    assert len(route.waypoints) == 2
    assert route.waypoints[0] == index.position_for_cell((1, 2))
    assert route.waypoints[-1] == index.position_for_cell((8, 2))


def test_replanning_increments_the_route_version(empty_world: object) -> None:
    world = empty_world
    index = GridIndex(world)
    planner = AStarPathPlanner(
        RobotProfileRegistry((RobotProfile("robot-001", 1, 1, 1.0),))
    )

    first = planner.plan_sync(
        "robot-001", "task-001",
        index.position_for_cell((0, 0)), index.position_for_cell((3, 0)), world, 0.0,
    )
    second = planner.plan_sync(
        "robot-001", "task-001",
        index.position_for_cell((0, 0)), index.position_for_cell((3, 0)), world, 1.0,
    )

    assert (first.version, first.route_id) == (1, "route-robot-001-task-001-v1")
    assert (second.version, second.route_id) == (2, "route-robot-001-task-001-v2")

    planner.reset_versions()
    third = planner.plan_sync(
        "robot-001", "task-001",
        index.position_for_cell((0, 0)), index.position_for_cell((3, 0)), world, 2.0,
    )
    assert third.version == 1


def test_blueprint_obstacles_are_typed_as_obstacles_in_the_world() -> None:
    blueprint = make_blueprint(
        obstacles=((1, 1),),
        robots=(("robot-001", (0, 0), 90.0),),
        profiles=(("robot-001", 1, 1, 1.0),),
        columns=4,
        rows=4,
    )

    assert blueprint.world.cells[0].cell_x == 1
    assert blueprint.grid.is_blocked(1, 1)
    assert blueprint.grid.is_traversable(1, 0)
    assert blueprint.occupancy_invariants_hold()
