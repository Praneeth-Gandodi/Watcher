"""Deterministic industrial world construction for Agent 2.

The generator builds a high-density industrial floor from a single seed: a
perimeter wall, racking aisles that force route planning to weave, resource
depots on the aisles, workstation cells at the ends of aisles, charging pads
along the perimeter, and dead zones that must never be entered. The layout is
a pure function of the seed so scale tests and failure scenarios reproduce
exactly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import floor

from backend.contracts.models import GridCell, GridCellType, WorldState

PERIMETER_THICKNESS = 1


@dataclass(frozen=True, slots=True)
class WorldSpec:
    """Physical and layout parameters for a generated world."""

    seed: int
    width_m: float = 200.0
    height_m: float = 120.0
    cell_size_m: float = 2.0
    aisle_count: int = 5
    rack_rows: int = 3
    workstation_count: int = 6
    charger_count: int = 8
    resource_count: int = 10
    dead_zone_count: int = 2

    @property
    def columns(self) -> int:
        return int(floor(self.width_m / self.cell_size_m))

    @property
    def rows(self) -> int:
        return int(floor(self.height_m / self.cell_size_m))


def build_world(spec: WorldSpec, *, revision: int = 1) -> WorldState:
    """Construct a seeded ``WorldState`` with deterministic cell placement.

    Layout order is perimeter, racking, workstations, chargers, resources, then
    dead zones. Each stage only writes into cells that are still free, so a
    later stage can never overwrite an earlier one and the output is stable for
    a given seed regardless of how the stages are tuned.
    """

    if spec.columns < 8 or spec.rows < 8:
        raise ValueError("world must be at least 8x8 cells to host a layout")
    if spec.aisle_count < 1 or spec.rack_rows < 1:
        raise ValueError("world layout requires at least one aisle and one rack row")

    rng = random.Random(spec.seed)
    cells: dict[tuple[int, int], GridCellType] = {}

    def place(x: int, y: int, cell_type: GridCellType) -> bool:
        if not (0 <= x < spec.columns and 0 <= y < spec.rows):
            return False
        if (x, y) in cells:
            return False
        cells[(x, y)] = cell_type
        return True

    _place_perimeter(place, spec)
    _place_racking(place, rng, spec)
    _place_workstations(place, rng, spec)
    _place_chargers(place, rng, spec)
    _place_resources(place, rng, spec)
    _place_dead_zones(place, rng, spec)

    grid_cells = tuple(
        GridCell(
            cell_x=x,
            cell_y=y,
            cell_type=cells[(x, y)],
        )
        for x, y in sorted(cells)
    )
    return WorldState(
        width_m=spec.width_m,
        height_m=spec.height_m,
        cell_size_m=spec.cell_size_m,
        columns=spec.columns,
        rows=spec.rows,
        cells=grid_cells,
        revision=revision,
    )


def _place_perimeter(place, spec: WorldSpec) -> None:
    """Wall the world in so robots cannot leave the industrial envelope."""

    for x in range(spec.columns):
        for y in range(PERIMETER_THICKNESS):
            place(x, y, GridCellType.OBSTACLE)
            place(x, spec.rows - 1 - y, GridCellType.OBSTACLE)
    for y in range(spec.rows):
        for x in range(PERIMETER_THICKNESS):
            place(x, y, GridCellType.OBSTACLE)
            place(spec.columns - 1 - x, y, GridCellType.OBSTACLE)


def _place_racking(place, rng: random.Random, spec: WorldSpec) -> None:
    """Lay down vertical rack blocks separated by traversable aisles.

    The inner floor is split into ``aisle_count`` rack blocks divided by
    fixed-width aisles, and each block is cut by horizontal cross-aisles. That
    forces routes to weave between blocks instead of running one straight line,
    which is what produces genuine right-of-way pressure between robots. A
    one-cell ring corridor inside the perimeter is always left clear so the
    whole floor stays connected regardless of how the shelves fall.
    """

    cross_aisle_height = 3
    aisle_width = 3
    ring = PERIMETER_THICKNESS + 1
    span_start = ring
    span_end = spec.columns - ring
    span = span_end - span_start
    block_count = spec.aisle_count
    while block_count > 0 and span - aisle_width * (block_count + 1) < block_count:
        block_count -= 1
    if block_count == 0:
        return

    total_block_width = span - aisle_width * (block_count + 1)
    base_width, extra = divmod(total_block_width, block_count)

    shelf_span_start = ring
    shelf_span_end = spec.rows - ring
    shelf_span = shelf_span_end - shelf_span_start
    segment_count = spec.rack_rows + 1
    segment_height = (shelf_span - cross_aisle_height * spec.rack_rows) // segment_count

    cursor = span_start
    for block_index in range(block_count):
        if block_index > 0:
            cursor += aisle_width
        block_width = base_width + (1 if block_index < extra else 0)
        block_start = cursor
        block_end = min(span_end, block_start + block_width)
        if block_end <= block_start:
            break

        segment_start = shelf_span_start
        for segment_index in range(segment_count):
            jitter = rng.randint(0, 1)
            segment_top = segment_start + jitter
            segment_bottom = min(shelf_span_end, segment_top + segment_height)
            for x in range(block_start, block_end):
                for y in range(segment_top, segment_bottom):
                    place(x, y, GridCellType.OBSTACLE)
            segment_start = segment_bottom + cross_aisle_height
            if segment_start >= shelf_span_end:
                break
        cursor = block_end


def _place_workstations(place, rng: random.Random, spec: WorldSpec) -> None:
    """Attach workstations to the left and right ring corridor.

    Workstation cells are not blocking, so placing them on the corridor keeps
    the floor connected while still forcing a real traversal to reach a
    workstation target.
    """

    placed = 0
    attempts = 0
    while placed < spec.workstation_count and attempts < spec.workstation_count * 40:
        attempts += 1
        y = rng.randrange(PERIMETER_THICKNESS, spec.rows - PERIMETER_THICKNESS)
        x = PERIMETER_THICKNESS if placed % 2 == 0 else spec.columns - 1 - PERIMETER_THICKNESS
        if place(x, y, GridCellType.WORKSTATION):
            placed += 1


def _place_chargers(place, rng: random.Random, spec: WorldSpec) -> None:
    """Distribute charging pads along the top and bottom ring corridor."""

    placed = 0
    attempts = 0
    while placed < spec.charger_count and attempts < spec.charger_count * 40:
        attempts += 1
        x = rng.randrange(PERIMETER_THICKNESS, spec.columns - PERIMETER_THICKNESS)
        y = (
            PERIMETER_THICKNESS
            if placed % 2 == 0
            else spec.rows - 1 - PERIMETER_THICKNESS
        )
        if place(x, y, GridCellType.CHARGING):
            placed += 1


def _place_resources(place, rng: random.Random, spec: WorldSpec) -> None:
    """Scatter resource depots away from the perimeter so travel is real."""

    placed = 0
    attempts = 0
    while placed < spec.resource_count and attempts < spec.resource_count * 60:
        attempts += 1
        x = rng.randrange(PERIMETER_THICKNESS + 1, spec.columns - PERIMETER_THICKNESS - 1)
        y = rng.randrange(PERIMETER_THICKNESS + 1, spec.rows - PERIMETER_THICKNESS - 1)
        if place(x, y, GridCellType.RESOURCE):
            placed += 1


def _place_dead_zones(place, rng: random.Random, spec: WorldSpec) -> None:
    """Place circular no-go zones well inside the floor."""

    placed = 0
    attempts = 0
    radius = max(2, min(spec.columns, spec.rows) // 14)
    while placed < spec.dead_zone_count and attempts < spec.dead_zone_count * 60:
        attempts += 1
        low = PERIMETER_THICKNESS + radius + 1
        high_x = spec.columns - PERIMETER_THICKNESS - radius - 1
        high_y = spec.rows - PERIMETER_THICKNESS - radius - 1
        if high_x <= low or high_y <= low:
            return
        center_x = rng.randrange(low, high_x)
        center_y = rng.randrange(low, high_y)
        added = 0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    if place(center_x + dx, center_y + dy, GridCellType.DEADZONE):
                        added += 1
        if added:
            placed += 1
