"""Deterministic industrial warehouse layouts.

The default demo world must be a *designed* warehouse, not scattered noise.
Every layout here is a pure function of ``(columns, rows)``, so a scenario is
byte-identical on every run and a demo is reproducible.

Design rules that the layouts respect:

* Long walls create structure; they are never random confetti.
* Open plazas, narrow corridors, intersections, and dead ends all appear, so
  robots must genuinely negotiate their way around.
* A one-cell corridor stays one cell wide. Footprint-aware planning then means a
  2x2 or 3x2 robot genuinely cannot enter it, which is what makes robot size
  observable in the UI.
* Charging stations, workstations, and resource cells are placed in dedicated
  bays rather than sprinkled, so they read as a facility on the map.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "LAYOUT_NAMES",
    "Layout",
    "build_layout",
    "crossing_layout",
    "deadlock_layout",
    "high_traffic_layout",
    "warehouse_layout",
]


@dataclass(frozen=True, slots=True)
class Layout:
    """Sparse typed cell geometry for a world."""

    name: str
    columns: int
    rows: int
    obstacles: tuple[tuple[int, int], ...]
    charging: tuple[tuple[int, int], ...] = ()
    workstations: tuple[tuple[int, int], ...] = ()
    resources: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.columns < 6 or self.rows < 6:
            raise ValueError("a layout needs at least 6x6 cells to be meaningful")

def _sorted_unique(cells: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(set(cells)))


def _in_bounds(cell: tuple[int, int], columns: int, rows: int) -> bool:
    return 0 <= cell[0] < columns and 0 <= cell[1] < rows


def _clean(
    cells: tuple[tuple[int, int], ...], columns: int, rows: int
) -> tuple[tuple[int, int], ...]:
    return _sorted_unique(
        tuple(cell for cell in cells if _in_bounds(cell, columns, rows))
    )


def warehouse_layout(columns: int = 40, rows: int = 25) -> Layout:
    """The default production layout: a working industrial warehouse.

    Structure:

    * a charging bay along the bottom wall
    * two long horizontal racking walls with doorways, forcing east-west travel
      through specific gaps
    * a vertical spine wall splitting the floor in two, with a **two-cell**
      doorway so every robot size can cross and the whole floor stays usable
    * a one-cell *express corridor* in each half, a shortcut that only robots
      with a one-cell height can take. A 2x2 or 3x2 genuinely cannot enter it,
      which is what makes robot footprint visible on the map
    * dead-end storage aisles in the upper right
    * an open plaza in the middle for crossings and negotiations
    """

    obstacles: list[tuple[int, int]] = []
    charging: list[tuple[int, int]] = []
    workstations: list[tuple[int, int]] = []
    resources: list[tuple[int, int]] = []

    # Corner posts fence the world without walling off the usable floor.
    obstacles.append((0, 0))
    obstacles.append((columns - 1, 0))
    obstacles.append((0, rows - 1))
    obstacles.append((columns - 1, rows - 1))

    # Charging bay along the bottom: a row of pads with a service gap.
    for column in range(3, columns - 3, 5):
        charging.append((column, 1))
        charging.append((column + 1, 1))
    obstacles.append((1, 1))
    obstacles.append((columns - 2, 1))

    # Long racking wall A with three-cell doorways. The doorway must be at
    # least as wide as the largest robot, otherwise a 3x2 body needs three free
    # columns in the wall's row and cannot squeeze through a one-cell gap.
    racking_a = max(4, rows // 3)
    for column in range(2, columns - 2):
        if column % 11 in (0, 1, 2):
            continue
        obstacles.append((column, racking_a))

    # Long racking wall B just below A, so the gap between them is a real
    # corridor and the doorways do not line up into a single highway. On a small
    # floor the gap would be too thin to be drivable, so it is skipped.
    if rows >= 18:
        for column in range(2, columns - 2):
            if column % 13 in (0, 1, 2):
                continue
            obstacles.append((column, racking_a + 2))

    # Vertical spine with a two-cell doorway: every footprint can cross it.
    spine = columns // 2
    doorway_a = rows // 2
    for row in range(2, rows - 2):
        if doorway_a <= row <= doorway_a + 1:
            continue
        obstacles.append((spine, row))

    # One-cell express corridor: a vertical shortcut walled on both sides. Only
    # robots with a one-cell height can use it, which is what makes footprint
    # visibly matter. It needs room to be a shortcut at all, so small floors
    # keep an open middle instead.
    if columns >= 30 and rows >= 18:
        express = spine - 5
        for row in range(2, rows - 2):
            obstacles.append((express - 1, row))
            obstacles.append((express + 1, row))

    # Two dead-end storage aisles in the upper right quadrant. Kept few and
    # shallow so they add warehouse character without fragmenting the floor.
    if rows >= 18:
        aisle_top = rows - 3
        for index in range(2):
            aisle_row = aisle_top - index * 2
            for column in range(spine + 4, columns - 3):
                obstacles.append((column, aisle_row))
        obstacles.append((spine + 6, aisle_top))
        for row in range(aisle_top - 2, aisle_top + 1):
            obstacles.append((spine + 6, row))

    # Workstation bay on the left, facing the plaza.
    for row in range(doorway_a - 1, doorway_a + 2):
        workstations.append((2, row))
    obstacles.append((1, doorway_a))

    # Resource pallets in the open plaza, sparse enough to stay drivable.
    resources.append((spine - 3, doorway_a + 4))
    resources.append((spine + 3, doorway_a - 4))
    resources.append((max(3, columns // 4), doorway_a - 1))

    return Layout(
        name="normal",
        columns=columns,
        rows=rows,
        obstacles=_clean(tuple(obstacles), columns, rows),
        charging=_clean(tuple(charging), columns, rows),
        workstations=_clean(tuple(workstations), columns, rows),
        resources=_clean(tuple(resources), columns, rows),
    )


def high_traffic_layout(columns: int = 40, rows: int = 25) -> Layout:
    """Open floor with periodic pillars: many robots, many crossings.

    A wide-open middle maximises the number of route pairs that intersect, so
    space-time conflicts and right-of-way decisions become frequent and visible.
    The pillars are placed on a lattice, which keeps the pattern deterministic
    while still producing a realistic obstacle field.
    """

    obstacles: list[tuple[int, int]] = [
        (0, 0),
        (columns - 1, 0),
        (0, rows - 1),
        (columns - 1, rows - 1),
    ]
    for column in range(6, columns - 4, 7):
        for row in range(5, rows - 4, 6):
            obstacles.append((column, row))
            obstacles.append((column + 1, row))
    # Two long walls to keep traffic funnelled through gaps.
    for column in range(3, columns - 3):
        if column % 17 in (0, 1):
            continue
        obstacles.append((column, rows // 2 - 3))
    return Layout(
        name="high-traffic",
        columns=columns,
        rows=rows,
        obstacles=_clean(tuple(obstacles), columns, rows),
        charging=_clean(((3, 1), (columns - 4, 1)), columns, rows),
        workstations=_clean(((1, rows // 2), (columns - 2, rows // 2)), columns, rows),
        resources=_clean(
            ((columns // 2, rows // 2 + 4), (columns // 2 + 6, rows // 2 - 5)),
            columns,
            rows,
        ),
    )


def crossing_layout(columns: int = 40, rows: int = 25) -> Layout:
    """A four-way intersection at the centre, sized to force path crossings.

    Four open approach lanes meet at one plaza. Robots entering from opposite
    lanes must share the same cells, so a conflict is a natural consequence of
    the geometry rather than a contrivance.
    """

    obstacles: list[tuple[int, int]] = [
        (0, 0),
        (columns - 1, 0),
        (0, rows - 1),
        (columns - 1, rows - 1),
    ]
    centre_x, centre_y = columns // 2, rows // 2

    # Guard rails around the plaza, broken on all four approach lanes.
    for column in range(centre_x - 4, centre_x + 5):
        for row in (centre_y - 3, centre_y + 3):
            if abs(column - centre_x) <= 1:
                continue  # lane openings
            obstacles.append((column, row))
    for row in range(centre_y - 2, centre_y + 3):
        for column in (centre_x - 4, centre_x + 4):
            if abs(row - centre_y) <= 1:
                continue
            obstacles.append((column, row))

    # Diagonal racking in two corners to keep the map from being a bare cross.
    for index in range(4):
        obstacles.append((4 + index, 4 + index))
        obstacles.append((columns - 5 - index, 4 + index))

    return Layout(
        name="crossing",
        columns=columns,
        rows=rows,
        obstacles=_clean(tuple(obstacles), columns, rows),
        charging=_clean(((2, 1), (columns - 3, 1)), columns, rows),
        workstations=_clean(((1, centre_y), (columns - 2, centre_y)), columns, rows),
        resources=_clean(
            ((centre_x, centre_y), (centre_x - 6, centre_y - 6), (centre_x + 6, centre_y + 6)),
            columns,
            rows,
        ),
    )


def deadlock_layout(columns: int = 40, rows: int = 25) -> Layout:
    """A ring corridor with a closed centre: mutual waiting is easy to reach.

    A single-width ring means robots entering from four directions meet in the
    middle of the loop. A grid of one-cell lanes creates a directed cycle of
    waiting, which is exactly the shape the backend's wait-graph detector
    recognises.
    """

    obstacles: list[tuple[int, int]] = [
        (0, 0),
        (columns - 1, 0),
        (0, rows - 1),
        (columns - 1, rows - 1),
    ]
    centre_x, centre_y = columns // 2, rows // 2
    inner = 4

    # Solid block in the middle: only the surrounding ring is drivable. A small
    # floor keeps a thinner core so the ring is still wide enough to drive.
    core = inner if (columns >= 30 and rows >= 18) else inner - 1
    for column in range(centre_x - inner, centre_x + inner + 1):
        for row in range(centre_y - core, centre_y + core + 1):
            if abs(column - centre_x) == inner or abs(row - centre_y) == core:
                continue  # the ring itself stays open
            obstacles.append((column, row))

    # Four one-cell approach stubs that all feed the same ring.
    for offset in (inner - 1, -(inner - 1)):
        obstacles.append((centre_x + offset, centre_y + inner + 1))
        obstacles.append((centre_x + offset, centre_y - inner - 1))
        obstacles.append((centre_x + inner + 1, centre_y + offset))
        obstacles.append((centre_x - inner - 1, centre_y + offset))

    return Layout(
        name="deadlock",
        columns=columns,
        rows=rows,
        obstacles=_clean(tuple(obstacles), columns, rows),
        charging=_clean(((2, 1), (columns - 3, 1)), columns, rows),
        workstations=_clean(((1, centre_y), (columns - 2, centre_y)), columns, rows),
        resources=_clean(
            ((centre_x, centre_y - inner - 2), (centre_x, centre_y + inner + 2)),
            columns,
            rows,
        ),
    )


#: Scenario presets the UI can load. Keys are the canonical preset ids.
LAYOUT_NAMES: tuple[str, ...] = (
    "normal",
    "crossing",
    "high-traffic",
    "deadlock",
)

_BUILDERS = {
    "normal": warehouse_layout,
    "crossing": crossing_layout,
    "high-traffic": high_traffic_layout,
    "deadlock": deadlock_layout,
}


def build_layout(name: str, columns: int = 40, rows: int = 25) -> Layout:
    """Return a named layout, or raise for an unknown id."""

    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise ValueError(
            f"unknown layout {name!r}; expected one of {sorted(_BUILDERS)}"
        ) from None
    return builder(columns, rows)
