"""Unit tests for grid indexing, coordinate conversion, and occupancy."""

from __future__ import annotations

import pytest

from backend.contracts.models import GridCell, GridCellType, Position2D, WorldState
from backend.simulation.grid import Cell, OccupancyGrid, WorldIndex


def build_index() -> WorldIndex:
    world = WorldState(
        width_m=20.0,
        height_m=10.0,
        cell_size_m=2.0,
        columns=10,
        rows=5,
        cells=(
            GridCell(cell_x=2, cell_y=2, cell_type=GridCellType.OBSTACLE),
            GridCell(cell_x=4, cell_y=2, cell_type=GridCellType.CHARGING),
            GridCell(cell_x=6, cell_y=2, cell_type=GridCellType.WORKSTATION),
            GridCell(cell_x=8, cell_y=2, cell_type=GridCellType.RESOURCE),
            GridCell(cell_x=5, cell_y=3, cell_type=GridCellType.DEADZONE),
        ),
        revision=1,
    )
    return WorldIndex.from_world(world)


class TestWorldIndex:
    def test_converts_world_position_to_cell(self) -> None:
        index = build_index()
        assert index.cell_of((0.0, 0.0)) == Cell(0, 0)
        assert index.cell_of((4.9, 2.1)) == Cell(2, 1)
        assert index.cell_of((19.9, 9.9)) == Cell(9, 4)

    def test_clamps_positions_outside_the_world(self) -> None:
        index = build_index()
        assert index.cell_of((-5.0, 99.0)) == Cell(0, 4)

    def test_cell_center_is_inside_its_own_cell(self) -> None:
        index = build_index()
        for cell in (Cell(0, 0), Cell(3, 4), Cell(9, 2)):
            assert index.cell_of(index.center_of(cell)) == cell

    def test_unlisted_cells_are_free(self) -> None:
        index = build_index()
        assert index.cell_type(Cell(7, 4)) is GridCellType.FREE

    def test_obstacles_and_dead_zones_block_movement(self) -> None:
        index = build_index()
        assert index.is_blocked(Cell(2, 2)) is True
        assert index.is_blocked(Cell(5, 3)) is True
        assert index.is_blocked(Cell(4, 2)) is False

    def test_out_of_bounds_cells_are_blocked(self) -> None:
        index = build_index()
        assert index.is_blocked(Cell(-1, 0)) is True
        assert index.is_blocked(Cell(0, 5)) is True

    def test_reports_membership_of_the_world(self) -> None:
        index = build_index()
        assert index.is_inside_world((0.0, 0.0)) is True
        assert index.is_inside_world((20.0, 10.0)) is True
        assert index.is_inside_world((20.1, 5.0)) is False
        assert index.is_inside_world((-0.1, 5.0)) is False

    def test_groups_feature_cells_by_type(self) -> None:
        index = build_index()
        assert index.stations(GridCellType.CHARGING) == (Cell(4, 2),)
        assert index.stations(GridCellType.WORKSTATION) == (Cell(6, 2),)
        assert index.stations(GridCellType.RESOURCE) == (Cell(8, 2),)

    def test_finds_the_nearest_feature_cell(self) -> None:
        index = build_index()
        nearest = index.nearest_cell_of_type(
            Position2D(x=0.0, y=0.0), GridCellType.CHARGING
        )
        assert nearest == Cell(4, 2)

    def test_nearest_feature_search_can_exclude_cells(self) -> None:
        index = build_index()
        nearest = index.nearest_cell_of_type(
            Position2D(x=0.0, y=0.0),
            GridCellType.CHARGING,
            unavailable=(Cell(4, 2),),
        )
        assert nearest is None

    def test_nearest_feature_search_returns_none_when_absent(self) -> None:
        empty = WorldIndex.from_world(
            WorldState(
                width_m=8.0,
                height_m=8.0,
                cell_size_m=2.0,
                columns=4,
                rows=4,
                cells=(),
                revision=1,
            )
        )
        assert empty.nearest_cell_of_type(Position2D(x=1.0, y=1.0), GridCellType.CHARGING) is None

    def test_reports_index_revision(self) -> None:
        assert build_index().revision == 1


class TestOccupancyGrid:
    def test_reserves_and_reports_the_owner(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(1, 1), Cell(2, 1)))
        assert occupancy.owner_of(Cell(1, 1)) == "robot-001"
        assert occupancy.owner_in((Cell(9, 9), Cell(2, 1)), "robot-002") == "robot-001"

    def test_a_robot_never_conflicts_with_itself(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(3, 3),))
        assert occupancy.owner_in((Cell(3, 3),), "robot-001") is None
        assert occupancy.is_reserved_by_other(Cell(3, 3), "robot-001") is False

    def test_reserving_again_keeps_previously_held_cells(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(1, 1),))
        occupancy.reserve("robot-001", (Cell(2, 1),))
        assert occupancy.owner_of(Cell(1, 1)) == "robot-001"
        assert occupancy.owner_of(Cell(2, 1)) == "robot-001"

    def test_clearing_a_robot_leaves_other_robots_reservations(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(1, 1), Cell(5, 5)))
        occupancy.reserve("robot-002", (Cell(2, 1),))
        occupancy.clear_robot("robot-002")
        assert occupancy.owner_of(Cell(2, 1)) is None
        assert occupancy.owner_of(Cell(1, 1)) == "robot-001"
        assert occupancy.owner_of(Cell(5, 5)) == "robot-001"

    def test_a_cell_holds_a_single_owner(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(1, 1),))
        occupancy.reserve("robot-002", (Cell(1, 1),))
        # The later reservation takes the cell, and clearing the original
        # holder must not evict the current owner.
        assert occupancy.owner_of(Cell(1, 1)) == "robot-002"
        occupancy.clear_robot("robot-001")
        assert occupancy.owner_of(Cell(1, 1)) == "robot-002"

    def test_flat_lookups_match_the_coordinate_lookups(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(3, 4),))
        assert occupancy.owner_of_flat(4 * 10 + 3) == "robot-001"
        assert occupancy.owner_of_flat(0) is None

    def test_flat_lookups_are_skipped_without_a_stride(self) -> None:
        occupancy = OccupancyGrid()
        occupancy.reserve("robot-001", (Cell(3, 4),))
        assert occupancy.owner_of_flat(43) is None
        assert occupancy.owner_of(Cell(3, 4)) == "robot-001"

    def test_counts_reserved_cells(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", (Cell(1, 1), Cell(2, 1)))
        occupancy.reserve("robot-002", (Cell(3, 1),))
        assert occupancy.reserved_count() == 3

    def test_reserving_nothing_is_a_no_op(self) -> None:
        occupancy = OccupancyGrid(columns=10)
        occupancy.reserve("robot-001", ())
        assert occupancy.reserved_count() == 0

    def test_clearing_an_unknown_robot_is_safe(self) -> None:
        OccupancyGrid(columns=10).clear_robot("robot-404")


class TestIndexInvariants:
    @pytest.mark.parametrize("seed", [1, 7, 2026, 99991])
    def test_generated_world_keeps_features_inside_bounds(self, seed: int) -> None:
        from backend.simulation.world import WorldSpec, build_world

        world = build_world(WorldSpec(seed=seed))
        index = WorldIndex.from_world(world)
        for cell, cell_type in index.cell_types.items():
            assert index.contains(cell)
            assert index.cell_type(cell) is cell_type
