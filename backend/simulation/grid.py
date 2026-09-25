"""Deterministic grid indexing, coordinate conversion, and dynamic occupancy.

This module owns the mapping between world-space meters and integer grid
cells plus the short-lived reservations that moving robots hold on cells.
Reservations are what keep conflict detection cheap: detection queries only
the cells a robot intends to occupy instead of every robot pair, which is
what allows the simulation to run hundreds of robots at simulation rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from math import floor

from backend.contracts.models import GridCellType, Position2D, WorldState

BLOCKING_CELL_TYPES: frozenset[GridCellType] = frozenset(
    {GridCellType.OBSTACLE, GridCellType.DEADZONE}
)


@dataclass(frozen=True, slots=True, order=True)
class Cell:
    """An integer grid coordinate with ``x`` increasing rightward, ``y`` up."""

    x: int
    y: int

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"{self.x}:{self.y}"


@dataclass(slots=True)
class WorldIndex:
    """Read-optimized lookup tables derived from an immutable ``WorldState``."""

    width_m: float
    height_m: float
    cell_size_m: float
    columns: int
    rows: int
    revision: int
    cell_types: dict[Cell, GridCellType] = field(default_factory=dict)
    cells_of_type: dict[GridCellType, tuple[Cell, ...]] = field(default_factory=dict)
    open_mask: bytearray = field(default_factory=bytearray)

    @classmethod
    def from_world(cls, world: WorldState) -> WorldIndex:
        """Build lookup tables from the canonical world contract.

        Unlisted cells are free per ``CONTRACTS.md``; only explicitly listed
        cells are stored, so the index stays proportional to world features
        rather than to world area. A flat ``open_mask`` is built alongside the
        sparse tables because the planner's inner loop runs per expanded cell
        and must not pay for a dictionary lookup and a bounds check each time.
        ``open_mask`` holds ``1`` for traversable cells and ``0`` for blocked
        ones, so callers test ``if not open_mask[flat]`` to skip.
        """

        index = cls(
            width_m=world.width_m,
            height_m=world.height_m,
            cell_size_m=world.cell_size_m,
            columns=world.columns,
            rows=world.rows,
            revision=world.revision,
        )
        grouped: dict[GridCellType, list[Cell]] = {}
        for grid_cell in world.cells:
            cell = Cell(grid_cell.cell_x, grid_cell.cell_y)
            index.cell_types[cell] = grid_cell.cell_type
            grouped.setdefault(grid_cell.cell_type, []).append(cell)
        index.cells_of_type = {
            cell_type: tuple(sorted(cells))
            for cell_type, cells in grouped.items()
        }
        mask = bytearray(b"\x01") * (index.columns * index.rows)
        for cell, cell_type in index.cell_types.items():
            if cell_type in BLOCKING_CELL_TYPES:
                mask[cell.y * index.columns + cell.x] = 0
        index.open_mask = mask
        return index

    def flat_index(self, cell: Cell) -> int:
        """Return the flat array offset for a cell."""

        return cell.y * self.columns + cell.x

    def cell_from_flat(self, flat: int) -> Cell:
        return Cell(x=flat % self.columns, y=flat // self.columns)

    def contains(self, cell: Cell) -> bool:
        return 0 <= cell.x < self.columns and 0 <= cell.y < self.rows

    def cell_of(self, position: Position2D | tuple[float, float]) -> Cell:
        """Return the cell containing a world position, clamped to bounds."""

        x, y = (position.x, position.y) if isinstance(position, Position2D) else position
        return Cell(
            x=min(self.columns - 1, max(0, floor(x / self.cell_size_m))),
            y=min(self.rows - 1, max(0, floor(y / self.cell_size_m))),
        )

    def center_of(self, cell: Cell) -> tuple[float, float]:
        """Return the world-space center of a cell."""

        return ((cell.x + 0.5) * self.cell_size_m, (cell.y + 0.5) * self.cell_size_m)

    def cell_type(self, cell: Cell) -> GridCellType:
        """Unlisted cells are free per the world contract."""

        return self.cell_types.get(cell, GridCellType.FREE)

    def is_inside_world(self, position: Position2D | tuple[float, float]) -> bool:
        x, y = (position.x, position.y) if isinstance(position, Position2D) else position
        return 0.0 <= x <= self.width_m and 0.0 <= y <= self.height_m

    def is_blocked(self, cell: Cell) -> bool:
        """True when a cell is outside the world, an obstacle, or a dead zone."""

        return not self.contains(cell) or self.cell_type(cell) in BLOCKING_CELL_TYPES

    def stations(self, cell_type: GridCellType) -> tuple[Cell, ...]:
        return self.cells_of_type.get(cell_type, ())

    def nearest_cell_of_type(
        self,
        origin: Position2D,
        cell_type: GridCellType,
        *,
        unavailable: Iterable[Cell] = (),
    ) -> Cell | None:
        """Return the closest cell of ``cell_type`` via a widening ring search.

        Ties break on the lowest ``(y, x)`` so the choice is deterministic for
        a given observation, which keeps seeded runs reproducible.
        """

        blocked = set(unavailable)
        start = self.cell_of(origin)
        for radius in range(0, max(self.columns, self.rows) + 1):
            best: Cell | None = None
            best_distance = 0.0
            for candidate in self._ring(start, radius):
                if candidate in blocked or self.cell_type(candidate) is not cell_type:
                    continue
                center_x, center_y = self.center_of(candidate)
                distance = (center_x - origin.x) ** 2 + (center_y - origin.y) ** 2
                if best is None or distance < best_distance:
                    best, best_distance = candidate, distance
            if best is not None:
                return best
        return None

    def _ring(self, center: Cell, radius: int) -> Iterator[Cell]:
        """Yield the square ring of cells at ``radius`` around ``center``."""

        if radius == 0:
            yield center
            return
        for dx in range(-radius, radius + 1):
            for dy in (-radius, radius):
                yield Cell(center.x + dx, center.y + dy)
        for dy in range(-radius + 1, radius):
            for dx in (-radius, radius):
                yield Cell(center.x + dx, center.y + dy)


@dataclass(slots=True)
class OccupancyGrid:
    """Cell reservations held by robots, with owner lookups for detection.

    Reservations are advisory for planning (a planner pays a cost to cross a
    reserved cell rather than treating it as a wall) and authoritative for
    conflict detection. That distinction is what lets a dense fleet keep
    making progress without any robot freezing permanently.
    """

    reservations: dict[Cell, str] = field(default_factory=dict)
    by_robot: dict[str, frozenset[Cell]] = field(default_factory=dict)
    columns: int = 0
    flat_owners: dict[int, str] = field(default_factory=dict)

    def _flat(self, cell: Cell) -> int:
        return cell.y * self.columns + cell.x

    def clear_robot(self, robot_id: str) -> None:
        """Drop every reservation held by one robot."""

        for cell in self.by_robot.pop(robot_id, frozenset()):
            if self.reservations.get(cell) == robot_id:
                del self.reservations[cell]
                if self.columns:
                    self.flat_owners.pop(self._flat(cell), None)

    def reserve(self, robot_id: str, cells: Iterable[Cell]) -> None:
        """Add reservations for a robot, keeping any cells it already held."""

        new_cells = frozenset(cells)
        if not new_cells:
            return
        self.by_robot[robot_id] = self.by_robot.get(robot_id, frozenset()) | new_cells
        for cell in new_cells:
            self.reservations[cell] = robot_id
            if self.columns:
                self.flat_owners[self._flat(cell)] = robot_id

    def owner_of_flat(self, flat: int) -> str | None:
        """Reservation owner by flat offset — the planner's fast path."""

        return self.flat_owners.get(flat)

    def owner_of(self, cell: Cell) -> str | None:
        return self.reservations.get(cell)

    def is_reserved_by_other(self, cell: Cell, robot_id: str) -> bool:
        """True when a different robot holds the reservation for ``cell``."""

        owner = self.reservations.get(cell)
        return owner is not None and owner != robot_id

    def owner_in(self, cells: Iterable[Cell], robot_id: str) -> str | None:
        """Return the first other robot holding a reservation in ``cells``."""

        for cell in cells:
            owner = self.reservations.get(cell)
            if owner is not None and owner != robot_id:
                return owner
        return None

    def reserved_count(self) -> int:
        return len(self.reservations)
