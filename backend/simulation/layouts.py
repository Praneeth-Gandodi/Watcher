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
    * pass-through racking rows in one quadrant, open at both ends
    * an open plaza in the middle for crossings and negotiations

    Every aisle and doorway is deliberately pass-through. A closed end is a trap:
    a robot can drive in, meet another robot coming the other way, and have
    nowhere to go, which shows up as a task that never completes. A layout for a
    default demo therefore has no dead ends at all -- obstacles here are walls
    with gaps, never pockets.
    """

    obstacles: list[tuple[int, int]] = []
    charging: list[tuple[int, int]] = []
    workstations: list[tuple[int, int]] = []
    resources: list[tuple[int, int]] = []

    # Corner posts fence the world without walling off the usable floor. The
    # two cells beside each post are closed as well, so a robot can never end up
    # in a corner pocket with a single way out.
    for post_x, post_y in ((0, 0), (columns - 1, 0), (0, rows - 1), (columns - 1, rows - 1)):
        for dx, dy in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)):
            obstacles.append((post_x + dx, post_y + dy))

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

    # Long racking wall B below A, so the gap between them is a real corridor
    # and the doorways do not line up into a single highway. The gap is **two**
    # rows: a one-row corridor between two walls is a trap, because a robot in
    # it can be boxed in and cannot pass anything coming the other way. On a
    # small floor there is no room for two rows of clearance, so it is skipped.
    if rows >= 18:
        for column in range(2, columns - 2):
            if column % 13 in (0, 1, 2):
                continue
            obstacles.append((column, racking_a + 3))

    # Vertical spine with a two-cell doorway: every footprint can cross it.
    spine = columns // 2
    doorway_a = rows // 2
    for row in range(2, rows - 2):
        if doorway_a <= row <= doorway_a + 1:
            continue
        obstacles.append((spine, row))

    # Racking rows in the lower right quadrant. Every row is open at BOTH ends,
    # so each one is a pass-through lane rather than a dead end: a robot that
    # enters can always drive out, even if another robot is occupying the cell
    # behind it. The previous aisle here was closed at one end, which is a trap
    # -- a robot could nose in, meet another robot, and have no way out.
    if rows >= 18:
        for index in range(2):
            aisle_row = rows - 3 - index * 2
            start = spine + 4
            stop = columns - 3
            for column in range(start, stop):
                # Leave a doorway at the far end of every row as well as the
                # near one, so the rows cannot chain into a dead end.
                if column == start or column == stop - 1:
                    continue
                obstacles.append((column, aisle_row))

    # Workstation bay on the left, facing the plaza.
    for row in range(doorway_a - 1, doorway_a + 2):
        workstations.append((2, row))
    obstacles.append((1, doorway_a))

    # Resource pallets in the open plaza, sparse enough to stay drivable. They
    # sit in open floor rather than against a wall, so a robot that stops on one
    # can always be driven around.
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
    """A cross-aisle warehouse pinched down to a single-cell throat.

    The previous ring had a bypass everywhere, so two robots meeting in it
    simply drove around each other and the wait graph never closed a cycle.
    This layout keeps the floor drivable on both axes but narrows one aisle to a
    single cell. Two robots travelling in opposite directions along that aisle
    cannot pass, so they block each other face to face -- the smallest possible
    wait cycle, and exactly the shape the detector recognises. The other aisle
    stays open, so every cell remains reachable and nothing is stranded.
    """

    obstacles: list[tuple[int, int]] = [
        (0, 0),
        (columns - 1, 0),
        (0, rows - 1),
        (columns - 1, rows - 1),
    ]
    centre_x, centre_y = columns // 2, rows // 2

    # Everything is solid except a two-cell-wide cross: one horizontal aisle and
    # one vertical aisle. Two lanes are enough to spawn a fleet and to let
    # robots pass, so the only forced conflict is the one designed below.
    for column in range(columns):
        for row in range(rows):
            in_horizontal = abs(row - centre_y) <= 1
            in_vertical = abs(column - centre_x) <= 1
            if not (in_horizontal or in_vertical):
                obstacles.append((column, row))

    # The throat: on the eastern arm, close the lower lane for a few cells so
    # the aisle is exactly one cell wide. Robots coming from opposite directions
    # have to meet here.
    throat_start = centre_x + 3
    throat_end = min(centre_x + 7, columns - 2)
    for column in range(throat_start, throat_end + 1):
        obstacles.append((column, centre_y + 1))

    # Close the aisle ends so the cross is a corridor rather than a crossroads:
    # a robot cannot simply leave the floor and come back around the throat.
    for column in range(columns):
        if abs(column - centre_x) <= 1:
            continue
        obstacles.append((column, 0))
        obstacles.append((column, rows - 1))
    for row in range(rows):
        if abs(row - centre_y) <= 1:
            continue
        obstacles.append((0, row))
        obstacles.append((columns - 1, row))

    return Layout(
        name="deadlock",
        columns=columns,
        rows=rows,
        obstacles=_clean(tuple(obstacles), columns, rows),
        charging=_clean(((throat_start + 1, centre_y),), columns, rows),
        workstations=_clean(((2, centre_y), (columns - 3, centre_y)), columns, rows),
        resources=_clean(((centre_x, 2), (centre_x, rows - 3)), columns, rows),
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
