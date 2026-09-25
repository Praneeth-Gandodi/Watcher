"""Shared, deterministic fixtures for Agent 2 safety and simulation tests.

Everything here uses canonical kebab-case identifiers (``robot-001``) and
simulation time only: no wall-clock, no randomness outside explicit seeds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from backend.contracts.models import GridCell, GridCellType, WorldState
from backend.safety.pathfinding import PathStep
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.safety.trajectory import TrajectoryPoint, trajectory_from_cells
from backend.simulation.grid import Cell, footprint_cells
from backend.simulation.world import FleetBlueprint, make_robot

CellSize = tuple[int, int]


def free_grid(columns: int, rows: int) -> tuple[tuple[bool, ...], ...]:
    """Return an empty ``grid[cell_y][cell_x]`` grid where ``True`` is blocked."""

    return tuple(tuple(False for _ in range(columns)) for _ in range(rows))


def grid_with_obstacles(
    columns: int,
    rows: int,
    obstacles: Sequence[Cell],
) -> tuple[tuple[bool, ...], ...]:
    """Return a grid with ``obstacles`` marked as blocked."""

    blocked = set(obstacles)
    return tuple(
        tuple((column, row) in blocked for column in range(columns))
        for row in range(rows)
    )


def world_with_obstacles(
    columns: int,
    rows: int,
    obstacles: Sequence[Cell],
    *,
    cell_size_m: float = 1.0,
) -> WorldState:
    """Return a canonical ``WorldState`` with typed obstacle cells."""

    return WorldState(
        width_m=columns * cell_size_m,
        height_m=rows * cell_size_m,
        cell_size_m=cell_size_m,
        columns=columns,
        rows=rows,
        cells=tuple(
            GridCell(cell_x=cell[0], cell_y=cell[1], cell_type=GridCellType.OBSTACLE)
            for cell in obstacles
        ),
        revision=1,
    )


def wall_column_obstacles(
    columns: int,
    rows: int,
    wall_column: int,
    gap_row: int,
) -> tuple[Cell, ...]:
    """Return a full wall with a single gap, so routing must use the gap."""

    return tuple(
        (wall_column, row) for row in range(rows) if row != gap_row
    )


def make_steps(
    cells: Sequence[Cell],
    width_cells: int = 1,
    height_cells: int = 1,
) -> tuple[PathStep, ...]:
    """Return A*-style steps for a hand-written cell path."""

    return tuple(
        PathStep(cell=cell, occupied_cells=footprint_cells(cell, width_cells, height_cells))
        for cell in cells
    )


def make_trajectory(
    cells: Sequence[Cell],
    speed_mps: float = 1.0,
    cell_size_m: float = 1.0,
    start_time_s: float = 0.0,
    width_cells: int = 1,
    height_cells: int = 1,
) -> tuple[TrajectoryPoint, ...]:
    """Return a trajectory for a hand-written cell path."""

    return trajectory_from_cells(
        cells,
        width_cells=width_cells,
        height_cells=height_cells,
        speed_mps=speed_mps,
        cell_size_m=cell_size_m,
        start_time_s=start_time_s,
    )


def build_registry(
    profiles: Sequence[tuple[str, int, int, float]],
) -> RobotProfileRegistry:
    """Build a registry from ``(robot_id, width, height, speed)`` tuples."""

    return RobotProfileRegistry(
        RobotProfile(
            robot_id=robot_id,
            width_cells=width_cells,
            height_cells=height_cells,
            speed_mps=speed_mps,
        )
        for robot_id, width_cells, height_cells, speed_mps in profiles
    )


def make_blueprint(
    obstacles: Sequence[Cell],
    robots: Sequence[tuple[str, Cell, float]],
    profiles: Sequence[tuple[str, int, int, float]],
    *,
    columns: int,
    rows: int,
    cell_size_m: float = 1.0,
) -> FleetBlueprint:
    """Return a fully deterministic fleet blueprint for runtime tests."""

    world = world_with_obstacles(columns, rows, obstacles, cell_size_m=cell_size_m)
    return FleetBlueprint(
        world=world,
        profiles=build_registry(profiles),
        robots=tuple(
            make_robot(robot_id, cell, cell_size_m=cell_size_m, battery_percent=battery)
            for robot_id, cell, battery in robots
        ),
        start_cells=tuple(cell for _, cell, _ in robots),
    )


@pytest.fixture
def empty_world() -> WorldState:
    return world_with_obstacles(10, 8, ())


@pytest.fixture
def wall_world() -> WorldState:
    """A 10x8 world with a wall at column 5 whose only gap is row 2."""

    return world_with_obstacles(10, 8, wall_column_obstacles(10, 8, 5, 2))


@pytest.fixture
def make_cell_trajectory() -> Callable[..., tuple[TrajectoryPoint, ...]]:
    return make_trajectory
