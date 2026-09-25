"""Unit tests for the deterministic world generator."""

from __future__ import annotations

from collections import deque

import pytest

from backend.contracts.models import GridCellType
from backend.simulation.grid import Cell, WorldIndex
from backend.simulation.world import WorldSpec, build_world

SPEC = WorldSpec(seed=2026)


def reachable_free_cells(index: WorldIndex) -> set[Cell]:
    """Flood fill the traversable space from a corner of the ring corridor."""

    start = Cell(1, 1)
    if index.is_blocked(start):
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbour = Cell(cell.x + dx, cell.y + dy)
            if neighbour in seen or not index.contains(neighbour) or index.is_blocked(neighbour):
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return seen


class TestWorldGeneration:
    def test_is_deterministic_for_a_seed(self) -> None:
        assert build_world(SPEC) == build_world(SPEC)

    def test_different_seeds_produce_different_layouts(self) -> None:
        assert build_world(SPEC) != build_world(WorldSpec(seed=2027))

    def test_dimensions_follow_the_spec(self) -> None:
        world = build_world(SPEC)
        assert world.columns == 100
        assert world.rows == 60
        assert world.cell_size_m == 2.0

    def test_places_every_feature_family(self) -> None:
        index = WorldIndex.from_world(build_world(SPEC))
        for cell_type in (
            GridCellType.OBSTACLE,
            GridCellType.WORKSTATION,
            GridCellType.CHARGING,
            GridCellType.RESOURCE,
            GridCellType.DEADZONE,
        ):
            assert index.stations(cell_type), f"world generated no {cell_type} cells"

    def test_walls_the_perimeter(self) -> None:
        index = WorldIndex.from_world(build_world(SPEC))
        for x in range(index.columns):
            assert index.is_blocked(Cell(x, 0))
            assert index.is_blocked(Cell(x, index.rows - 1))
        for y in range(index.rows):
            assert index.is_blocked(Cell(0, y))
            assert index.is_blocked(Cell(index.columns - 1, y))

    def test_leaves_a_connected_traversable_floor(self) -> None:
        index = WorldIndex.from_world(build_world(SPEC))
        traversable = sum(index.open_mask)
        assert len(reachable_free_cells(index)) / traversable > 0.95

    def test_keeps_most_of_the_floor_traversable(self) -> None:
        world = build_world(SPEC)
        blocked = sum(
            1
            for cell_type in WorldIndex.from_world(world).cell_types.values()
            if cell_type in {GridCellType.OBSTACLE, GridCellType.DEADZONE}
        )
        assert blocked / (world.columns * world.rows) < 0.7

    def test_features_are_reachable_from_the_corridor(self) -> None:
        index = WorldIndex.from_world(build_world(SPEC))
        reachable = reachable_free_cells(index)
        for cell_type in (GridCellType.CHARGING, GridCellType.WORKSTATION, GridCellType.RESOURCE):
            for cell in index.stations(cell_type):
                assert cell in reachable, f"{cell_type} at {cell} is walled off"

    def test_keeps_the_ring_corridor_clear_of_obstacles(self) -> None:
        index = WorldIndex.from_world(build_world(SPEC))
        for x in range(1, index.columns - 1):
            assert not index.is_blocked(Cell(x, 1))
            assert not index.is_blocked(Cell(x, index.rows - 2))
        for y in range(1, index.rows - 1):
            assert not index.is_blocked(Cell(1, y))
            assert not index.is_blocked(Cell(index.columns - 2, y))

    def test_rejects_a_world_too_small_for_a_layout(self) -> None:
        with pytest.raises(ValueError):
            build_world(WorldSpec(seed=1, width_m=8.0, height_m=8.0, cell_size_m=2.0))

    def test_rejects_a_layout_without_aisles(self) -> None:
        with pytest.raises(ValueError):
            build_world(WorldSpec(seed=1, aisle_count=0))

    @pytest.mark.parametrize("seed", [3, 42, 2026, 31337])
    def test_any_seed_keeps_the_floor_connected(self, seed: int) -> None:
        index = WorldIndex.from_world(build_world(WorldSpec(seed=seed)))
        assert len(reachable_free_cells(index)) / sum(index.open_mask) > 0.9
