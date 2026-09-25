"""Grid indexing, coordinate conversion, and footprint checks.

The coordinate convention is the single source of truth for the whole backend,
so these tests pin it down explicitly: ``x`` is the column, ``y`` is the row,
Python indexing is ``grid[cell_y][cell_x]``, and world metres are cell centres.
"""

from __future__ import annotations

import pytest
from backend.contracts.models import GridCell, GridCellType, Position2D, WorldState
from backend.simulation.grid import (
    BLOCKING_CELL_TYPES,
    GridIndex,
    cell_to_world_position,
    footprint_cells,
    world_to_cell,
)
from tests.unit.safety.conftest import world_with_obstacles


def test_cell_centres_convert_to_world_metres() -> None:
    assert cell_to_world_position((0, 0), 1.0) == Position2D(x=0.5, y=0.5)
    assert cell_to_world_position((3, 4), 2.0) == Position2D(x=7.0, y=9.0)
    assert cell_to_world_position((0, 0), 0.5) == Position2D(x=0.25, y=0.25)


def test_world_positions_convert_back_to_the_same_cell() -> None:
    for cell in ((0, 0), (1, 1), (7, 4), (19, 11)):
        for cell_size in (0.5, 1.0, 2.0, 2.5):
            position = cell_to_world_position(cell, cell_size)
            assert world_to_cell(position, cell_size) == cell


def test_the_lowest_possible_position_maps_to_the_origin_cell() -> None:
    assert world_to_cell(Position2D(x=0.0, y=0.0), 1.0) == (0, 0)
    assert world_to_cell(Position2D(x=0.99, y=0.99), 1.0) == (0, 0)
    assert world_to_cell(Position2D(x=1.0, y=1.0), 1.0) == (1, 1)


@pytest.mark.parametrize("cell_size", [0.0, -1.0, float("inf"), float("nan")])
def test_a_non_positive_cell_size_is_rejected(cell_size: float) -> None:
    with pytest.raises(ValueError):
        cell_to_world_position((0, 0), cell_size)
    with pytest.raises(ValueError):
        world_to_cell(Position2D(x=0.0, y=0.0), cell_size)


def test_footprints_are_row_major_and_follow_the_documented_growth() -> None:
    assert footprint_cells((3, 4), 2, 2) == ((3, 4), (4, 4), (3, 5), (4, 5))
    assert footprint_cells((0, 0), 1, 1) == ((0, 0),)
    assert footprint_cells((0, 0), 3, 2) == (
        (0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1),
    )
    with pytest.raises(ValueError):
        footprint_cells((0, 0), 0, 1)


def test_occupancy_is_row_indexed_by_cell_y() -> None:
    world = world_with_obstacles(4, 3, ((1, 2),))
    index = GridIndex(world)

    # grid[cell_y][cell_x]: the obstacle is at row 2, column 1.
    assert index.occupancy[2][1] is True
    assert index.occupancy[1][1] is False
    assert index.is_blocked(1, 2) is True
    assert index.is_traversable(1, 2) is False


def test_deadzones_block_like_obstacles() -> None:
    world = WorldState(
        width_m=4.0,
        height_m=2.0,
        cell_size_m=1.0,
        columns=4,
        rows=2,
        cells=(
            GridCell(cell_x=0, cell_y=0, cell_type=GridCellType.DEADZONE),
            GridCell(cell_x=1, cell_y=0, cell_type=GridCellType.CHARGING),
        ),
        revision=1,
    )
    index = GridIndex(world)

    assert GridCellType.DEADZONE in BLOCKING_CELL_TYPES
    assert index.is_blocked(0, 0) is True
    # Chargers and workstations are traversable, unlike obstacles.
    assert index.is_blocked(1, 0) is False
    assert index.cell_type(1, 0) is GridCellType.CHARGING
    assert index.charging_cells == ((1, 0),)


def test_unlisted_cells_are_free() -> None:
    index = GridIndex(world_with_obstacles(3, 3, ()))

    assert index.cell_type(1, 1) is GridCellType.FREE
    assert index.is_blocked(1, 1) is False


def test_out_of_bounds_cells_are_treated_as_blocked() -> None:
    index = GridIndex(world_with_obstacles(3, 3, ()))

    assert index.contains(2, 2) is True
    assert index.contains(3, 2) is False
    assert index.is_blocked(3, 0) is True
    assert index.is_blocked(0, -1) is True
    with pytest.raises(IndexError):
        index.cell_type(3, 0)


def test_footprint_bounds_checks_reject_overflow_in_both_directions() -> None:
    index = GridIndex(world_with_obstacles(5, 4, ()))

    assert index.footprint_within_bounds((3, 3), 2, 1) is True
    assert index.footprint_within_bounds((4, 3), 2, 1) is False
    assert index.footprint_within_bounds((0, 0), 1, 5) is False
    assert index.footprint_within_bounds((-1, 0), 1, 1) is False
    assert index.footprint_within_bounds((0, 0), 6, 1) is False
    with pytest.raises(ValueError):
        index.footprint_within_bounds((0, 0), 0, 1)


def test_footprint_is_free_requires_bounds_and_no_obstacles() -> None:
    index = GridIndex(world_with_obstacles(5, 4, ((2, 1),)))

    assert index.footprint_is_free((0, 0), 2, 2) is True
    assert index.footprint_is_free((2, 1), 1, 1) is False
    assert index.footprint_is_free((1, 1), 2, 1) is False
    assert index.footprint_is_free((2, 1), 1, 1, ) is False
    assert index.footprint_is_free((4, 3), 2, 1) is False, "out of bounds is not free"
    assert index.footprint_blocked_cells((1, 1), 2, 1) == ((2, 1),)
    assert index.footprint_blocked_cells((0, 0), 2, 2) == ()


def test_positions_outside_the_world_are_rejected() -> None:
    index = GridIndex(world_with_obstacles(4, 4, ()))

    assert index.cell_for_position(Position2D(x=1.5, y=1.5)) == (1, 1)
    assert index.try_cell_for_position(Position2D(x=99.0, y=1.5)) is None
    with pytest.raises(ValueError, match="outside the world"):
        index.cell_for_position(Position2D(x=99.0, y=1.5))
    with pytest.raises(ValueError, match="outside the world"):
        index.position_for_cell((9, 9))


def test_the_nearest_traversable_cell_searches_inwards() -> None:
    index = GridIndex(world_with_obstacles(5, 5, ((1, 1),)))

    assert index.nearest_traversable_cell((0, 0)) == (0, 0)
    assert index.nearest_traversable_cell((1, 1)) in {
        (0, 1), (1, 0), (2, 1), (1, 2), (0, 0), (0, 2), (2, 0), (2, 2)
    }


def test_a_grid_index_requires_a_world_state() -> None:
    with pytest.raises(TypeError):
        GridIndex({"columns": 2, "rows": 2})  # type: ignore[arg-type]


def test_the_index_exposes_the_underlying_world() -> None:
    world = world_with_obstacles(6, 5, ())
    index = GridIndex(world)

    assert index.world is world
    assert (index.columns, index.rows) == (6, 5)
    assert index.cell_size_m == 1.0
    assert index.width_m == 6.0
    assert index.height_m == 5.0
    assert index.revision == world.revision
    assert "GridIndex" in repr(index)
