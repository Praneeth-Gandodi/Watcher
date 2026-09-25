"""Backend world/grid indexing, coordinate conversion, and footprint checks.

This module is the single source of truth for how the backend maps between the
canonical ``WorldState``/``Position2D`` contracts and integer grid cells. Every
Agent 2 algorithm (path planning, trajectory, collision, deadlock) uses it so
that grid coordinates and world meters can never drift apart.

Coordinate convention (authoritative for the whole backend)
----------------------------------------------------------

* ``cell_x`` is a **column** index that increases left to right.
* ``cell_y`` is a **row** index that increases bottom to top
  (see ``docs/architecture/visualization.md``).
* Python indexing is therefore ``grid[cell_y][cell_x]``: the first index is the
  row, the second index is the column.
* A robot cell position is the **top-left anchor** of its footprint. A robot of
  ``width_cells x height_cells`` anchored at ``(x, y)`` occupies
  ``grid[y + h][x + w]`` for ``0 <= h < height_cells`` and ``0 <= w < width_cells``.
* World positions use **cell centres**::

      x_m = (cell_x + 0.5) * cell_size_m
      y_m = (cell_y + 0.5) * cell_size_m

  ``Robot.position`` and ``RoutePlan.waypoints`` are cell-centre world
  positions, so ``position -> cell -> position`` round-trips exactly.
"""

from __future__ import annotations

from math import floor, isfinite

from backend.contracts.models import GridCellType, Position2D, WorldState

__all__ = [
    "Cell",
    "GridIndex",
    "BLOCKING_CELL_TYPES",
    "cell_to_world_position",
    "footprint_cells",
    "world_to_cell",
]

#: A grid cell address. ``x`` is the column, ``y`` is the row.
Cell = tuple[int, int]

#: ``grid[cell_y][cell_x]`` is ``True`` when the cell cannot be traversed.
OccupancyGrid = tuple[tuple[bool, ...], ...]

#: Cell types a robot footprint may never overlap.
BLOCKING_CELL_TYPES = frozenset({GridCellType.OBSTACLE, GridCellType.DEADZONE})

# Tolerance used when mapping world meters back onto a cell so that a value
# produced by :func:`cell_to_world_position` never lands in the wrong cell
# because of binary floating point representation.
_CELL_EPSILON = 1e-9


def cell_to_world_position(cell: Cell, cell_size_m: float) -> Position2D:
    """Return the world position of the centre of ``cell``."""

    if not isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be a positive finite number")
    cell_x, cell_y = cell
    return Position2D(
        x=(cell_x + 0.5) * cell_size_m,
        y=(cell_y + 0.5) * cell_size_m,
    )


def world_to_cell(position: Position2D, cell_size_m: float) -> Cell:
    """Return the cell that contains ``position`` (its cell index).

    The caller must still check the result against the world bounds. This
    function performs no bounds checking because it has no ``WorldState``.
    """

    if not isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be a positive finite number")
    column = floor(position.x / cell_size_m + _CELL_EPSILON)
    row = floor(position.y / cell_size_m + _CELL_EPSILON)
    return (int(column), int(row))


def footprint_cells(
    anchor: Cell,
    width_cells: int,
    height_cells: int,
) -> tuple[Cell, ...]:
    """Return every cell covered by a footprint anchored at ``anchor``.

    The result is ordered row-major (``y`` outer, ``x`` inner) so that it is
    deterministic and directly usable as ``grid[y][x]`` lookups.
    """

    if width_cells < 1 or height_cells < 1:
        raise ValueError("a robot footprint must cover at least one cell")
    anchor_x, anchor_y = anchor
    return tuple(
        (anchor_x + offset_x, anchor_y + offset_y)
        for offset_y in range(height_cells)
        for offset_x in range(width_cells)
    )


class GridIndex:
    """Read-only occupancy view over a canonical ``WorldState``."""

    __slots__ = (
        "_cell_size_m",
        "_charging_cells",
        "_columns",
        "_occupancy",
        "_revision",
        "_rows",
        "_types",
        "_width_m",
        "_height_m",
        "_world",
    )

    def __init__(self, world: WorldState) -> None:
        if not isinstance(world, WorldState):
            raise TypeError("GridIndex requires a WorldState contract instance")
        self._world = world
        self._cell_size_m = world.cell_size_m
        self._columns = world.columns
        self._rows = world.rows
        self._width_m = world.width_m
        self._height_m = world.height_m
        self._revision = world.revision

        types: dict[Cell, GridCellType] = {}
        for cell in world.cells:
            types[(cell.cell_x, cell.cell_y)] = cell.cell_type
        self._types = types

        self._occupancy: OccupancyGrid = tuple(
            tuple(
                cell_type in BLOCKING_CELL_TYPES
                for cell_type in (
                    types.get((column, row), GridCellType.FREE)
                    for column in range(world.columns)
                )
            )
            for row in range(world.rows)
        )
        self._charging_cells = tuple(
            sorted(
                cell
                for cell, cell_type in types.items()
                if cell_type is GridCellType.CHARGING
            )
        )

    @property
    def world(self) -> WorldState:
        return self._world

    @property
    def columns(self) -> int:
        return self._columns

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cell_size_m(self) -> float:
        return self._cell_size_m

    @property
    def width_m(self) -> float:
        return self._width_m

    @property
    def height_m(self) -> float:
        return self._height_m

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def occupancy(self) -> OccupancyGrid:
        """``grid[cell_y][cell_x]`` where ``True`` means the cell is blocked."""

        return self._occupancy

    @property
    def charging_cells(self) -> tuple[Cell, ...]:
        return self._charging_cells

    def __repr__(self) -> str:
        return (
            f"GridIndex(columns={self._columns}, rows={self._rows}, "
            f"cell_size_m={self._cell_size_m}, revision={self._revision})"
        )

    def contains(self, cell_x: int, cell_y: int) -> bool:
        """Return whether a single cell address is inside the world."""

        return 0 <= cell_x < self._columns and 0 <= cell_y < self._rows

    def cell_type(self, cell_x: int, cell_y: int) -> GridCellType:
        """Return the type of a cell, defaulting to ``FREE`` when unlisted."""

        if not self.contains(cell_x, cell_y):
            raise IndexError(f"cell ({cell_x}, {cell_y}) is outside the world")
        return self._types.get((cell_x, cell_y), GridCellType.FREE)

    def is_blocked(self, cell_x: int, cell_y: int) -> bool:
        """Return whether a cell address is outside the world or impassable."""

        if not self.contains(cell_x, cell_y):
            return True
        return self._occupancy[cell_y][cell_x]

    def is_traversable(self, cell_x: int, cell_y: int) -> bool:
        return not self.is_blocked(cell_x, cell_y)

    def footprint_within_bounds(
        self,
        anchor: Cell,
        width_cells: int = 1,
        height_cells: int = 1,
    ) -> bool:
        """Return whether the whole footprint fits inside the world."""

        if width_cells < 1 or height_cells < 1:
            raise ValueError("a robot footprint must cover at least one cell")
        if width_cells > self._columns or height_cells > self._rows:
            return False
        anchor_x, anchor_y = anchor
        if anchor_x < 0 or anchor_y < 0:
            return False
        return (
            anchor_x + width_cells <= self._columns
            and anchor_y + height_cells <= self._rows
        )

    def footprint_blocked_cells(
        self,
        anchor: Cell,
        width_cells: int = 1,
        height_cells: int = 1,
    ) -> tuple[Cell, ...]:
        """Return the footprint cells that are outside the world or blocked."""

        if not self.footprint_within_bounds(anchor, width_cells, height_cells):
            return footprint_cells(anchor, width_cells, height_cells)
        return tuple(
            cell
            for cell in footprint_cells(anchor, width_cells, height_cells)
            if self._occupancy[cell[1]][cell[0]]
        )

    def footprint_is_free(
        self,
        anchor: Cell,
        width_cells: int = 1,
        height_cells: int = 1,
    ) -> bool:
        """Return whether the whole footprint is in bounds and obstacle free."""

        if not self.footprint_within_bounds(anchor, width_cells, height_cells):
            return False
        return not self.footprint_blocked_cells(anchor, width_cells, height_cells)

    def cell_for_position(self, position: Position2D) -> Cell:
        """Return the cell that contains a world position, bounds checked."""

        cell = world_to_cell(position, self._cell_size_m)
        if not self.contains(*cell):
            raise ValueError(
                f"position ({position.x}, {position.y}) maps to cell {cell}, "
                "which is outside the world"
            )
        return cell

    def try_cell_for_position(self, position: Position2D) -> Cell | None:
        """Like :meth:`cell_for_position` but returns ``None`` when outside."""

        cell = world_to_cell(position, self._cell_size_m)
        return cell if self.contains(*cell) else None

    def position_for_cell(self, cell: Cell) -> Position2D:
        """Return the world centre position of a cell, bounds checked."""

        if not self.contains(*cell):
            raise ValueError(f"cell {cell} is outside the world")
        return cell_to_world_position(cell, self._cell_size_m)

    def nearest_traversable_cell(self, cell: Cell) -> Cell | None:
        """Return the closest in-bounds obstacle-free cell to ``cell``.

        Used only as a defensive fallback: the runtime must never need it
        during normal operation.
        """

        if self.footprint_is_free(cell):
            return cell
        for radius in range(1, max(self._columns, self._rows)):
            candidates = [
                (cell[0] + dx, cell[1] + dy)
                for dx in range(-radius, radius + 1)
                for dy in range(-radius, radius + 1)
                if max(abs(dx), abs(dy)) == radius
            ]
            for candidate in sorted(
                candidates,
                key=lambda item: (abs(item[0] - cell[0]) + abs(item[1] - cell[1]), item),
            ):
                if self.contains(*candidate):
                    return candidate
        return None
