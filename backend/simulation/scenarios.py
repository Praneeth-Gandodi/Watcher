"""Scenario presets for the demo and the dashboard.

A scenario is a fully deterministic description of a run: which layout, how
big the grid is, how many robots, what shape and speed each one is, and where the
tasks are. Loading a scenario rebuilds the fleet from scratch, so switching
presets in the UI is reproducible rather than "whatever the simulation happened
to be doing".

Design choices that keep the demo honest and legible:

* Task targets are chosen from cells that fit the **largest** robot in the
  fleet. Any robot can therefore serve any task, so a blocked task always means
  a real safety outcome rather than an unreachable target. Conflicts still
  happen because routes *cross*, which is the interesting part.
* Robot footprints and speeds are mixed on purpose: a judge should be able to
  see at a glance that a 3x2 body moves differently from a 1x1.
* Battery levels are varied, including a low one, so the ``BATTERY_LOW`` path is
  visible without injecting anything.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import isqrt

from backend.contracts.models import (
    RobotCapability,
    Task,
    TaskStatus,
)
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.simulation.grid import Cell, GridIndex, cell_to_world_position
from backend.simulation.layouts import Layout, build_layout
from backend.simulation.world import FleetBlueprint, build_world, robot_id_for

__all__ = [
    "SCENARIO_NAMES",
    "auto_grid_for",
    "ScenarioSpec",
    "build_scenario_fleet",
    "scenario_specs",
    "spec_for",
    "task_targets_for",
]

#: Mixed body shapes: 1x1, 2x1, 2x2, and 3x2 all appear in the default fleet.
DEFAULT_SIZES: tuple[tuple[int, int], ...] = (
    (1, 1), (2, 1), (1, 1), (2, 2), (1, 1),
    (3, 2), (1, 1), (2, 1), (1, 1), (2, 2),
)

#: Four distinct speeds so movement rate is visibly different.
DEFAULT_SPEEDS: tuple[float, ...] = (1.0, 1.5, 0.75, 1.0, 2.0, 0.5, 1.25, 1.0, 0.75, 1.5)

#: Batteries that span healthy, low, and critical.
DEFAULT_BATTERIES: tuple[float, ...] = (88.0, 74.0, 61.0, 95.0, 19.0, 82.0, 68.0, 91.0, 55.0, 77.0)

#: Every robot can move; a few add the capabilities a preset task may require.
CAPABILITY_SETS: tuple[tuple[RobotCapability, ...], ...] = tuple(
    tuple(
        (
            RobotCapability.TRANSPORT,
            RobotCapability.PICK,
            RobotCapability.TUG if index % 4 == 0 else RobotCapability.DELIVER,
            RobotCapability.INSPECT if index % 5 == 0 else RobotCapability.PICK,
        )
    )
    for index in range(10)
)


#: Cells budgeted per robot when a fleet is resized. Each robot needs its own
#: footprint plus elbow room, and the designed layouts consume a further slice
#: of the floor with walls and corridors, so the budget is generous on purpose.
_CELLS_PER_ROBOT = 18


def auto_grid_for(
    robot_count: int,
    *,
    minimum_columns: int = 30,
    minimum_rows: int = 20,
) -> tuple[int, int]:
    """Return a grid big enough to place ``robot_count`` robots.

    A scale request only needs to name a fleet size, so the grid grows to suit
    instead of failing with "cannot place N robots". The aspect ratio stays
    roughly 1:1 so the map still looks like a warehouse rather than a corridor.
    The floor is kept comfortably larger than the robot budget, because the
    designed layouts also need room for their walls and doorways.
    """

    budget = max(robot_count, 1) * _CELLS_PER_ROBOT
    columns = max(minimum_columns, isqrt(budget) + 1)
    rows = max(minimum_rows, -(-budget // columns))
    return columns, rows


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """A fully deterministic scenario description."""

    name: str
    layout: str
    columns: int = 40
    rows: int = 25
    robot_count: int = 10
    seed: int = 2026
    task_count: int = 6
    sizes: tuple[tuple[int, int], ...] = DEFAULT_SIZES
    speeds: tuple[float, ...] = DEFAULT_SPEEDS
    batteries: tuple[float, ...] = DEFAULT_BATTERIES
    #: Task target cells pinned by a preset; empty means "choose open cells".
    pinned_targets: tuple[Cell, ...] = ()
    #: Task priorities, highest first, cycled over the task count.
    priorities: tuple[int, ...] = (5, 2, 3, 1, 4, 2)
    #: Robots that start with a battery level chosen for the battery preset.
    low_battery_robot_ids: tuple[str, ...] = ()
    required_capability: RobotCapability | None = None
    speed_multiplier: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if self.robot_count < 1:
            raise ValueError("robot_count must be positive")
        if not 0 < self.speed_multiplier <= 10:
            raise ValueError("speed_multiplier must be within (0, 10]")
        if self.task_count < 0:
            raise ValueError("task_count must not be negative")


def _fleet_specs(spec: ScenarioSpec) -> tuple[RobotProfile, ...]:
    return tuple(
        RobotProfile(
            robot_id=robot_id_for(offset + 1),
            width_cells=spec.sizes[offset % len(spec.sizes)][0],
            height_cells=spec.sizes[offset % len(spec.sizes)][1],
            speed_mps=spec.speeds[offset % len(spec.speeds)],
        )
        for offset in range(spec.robot_count)
    )


def _largest_footprint(spec: ScenarioSpec) -> tuple[int, int]:
    return max(
        (size for size in spec.sizes), key=lambda size: size[0] * size[1]
    )


def open_task_cells(
    index: GridIndex,
    count: int,
    footprint: tuple[int, int],
) -> tuple[Cell, ...]:
    """Return ``count`` spread-out cells where ``footprint`` fits.

    Picking task targets that fit the largest robot is what guarantees every
    robot can serve every task. A blocked task then means a genuine safety or
    negotiation outcome, not a target nobody could ever stand on.
    """

    if count < 1:
        return ()
    width, height = footprint
    candidates = [
        (column, row)
        for row in range(1, index.rows - 1)
        for column in range(1, index.columns - 1)
        if index.footprint_is_free((column, row), width, height)
    ]
    if not candidates:
        return ()

    # Spread the picks over the floor: step by a stride derived from the grid so
    # the result is deterministic and covers the whole map rather than one corner.
    stride = max(1, len(candidates) // max(count, 1))
    ordered = candidates[::stride] + candidates[::-stride]
    chosen: list[Cell] = []
    seen: set[Cell] = set()
    for cell in ordered:
        if len(chosen) >= count:
            break
        if cell in seen:
            continue
        # Keep targets apart so routes are long enough to cross and conflict.
        if any(
            abs(cell[0] - other[0]) + abs(cell[1] - other[1]) < max(
                index.columns // max(count, 1), 3
            )
            for other in chosen
        ):
            continue
        seen.add(cell)
        chosen.append(cell)
    for cell in candidates:
        if len(chosen) >= count:
            break
        if cell not in seen:
            seen.add(cell)
            chosen.append(cell)
    return tuple(chosen[:count])


def task_targets_for(spec: ScenarioSpec, index: GridIndex) -> tuple[Cell, ...]:
    """Return the task target cells for a scenario."""

    footprint = _largest_footprint(spec)
    pinned = tuple(
        cell
        for cell in spec.pinned_targets
        if index.footprint_is_free(cell, footprint[0], footprint[1])
    )
    if len(pinned) >= spec.task_count or spec.task_count == 0:
        return pinned[: spec.task_count]
    remainder = open_task_cells(
        index, spec.task_count - len(pinned), footprint
    )
    return pinned + remainder


def reachable_cells(
    index: GridIndex,
    start: Cell,
    width: int,
    height: int,
) -> frozenset[Cell]:
    """Return every cell a ``width x height`` body anchored there can drive to.

    One breadth-first flood fill answers the reachability question for a whole
    body at once, which is far cheaper than a path search per target and is what
    makes picking shared task targets practical at 500 robots.
    """

    if not index.footprint_is_free(start, width, height):
        return frozenset()
    occupied = index.occupancy
    seen: set[Cell] = {start}
    queue = deque([start])
    while queue:
        cell_x, cell_y = queue.popleft()
        for delta_x, delta_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbour = (cell_x + delta_x, cell_y + delta_y)
            if neighbour in seen:
                continue
            if neighbour[0] < 0 or neighbour[1] < 0:
                continue
            if neighbour[0] + width > index.columns or neighbour[1] + height > index.rows:
                continue
            # A move is legal only if the whole footprint is clear at the target.
            if any(
                occupied[neighbour[1] + dy][neighbour[0] + dx]
                for dy in range(height)
                for dx in range(width)
            ):
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return frozenset(seen)


def _component_index(
    index: GridIndex,
    width: int,
    height: int,
) -> dict[Cell, frozenset[Cell]]:
    """Map every valid anchor cell to its connected component.

    A body's reachable set *is* the connected component it starts in, so one
    labelled sweep per distinct footprint answers the reachability question for
    the whole fleet. That is what keeps loading a 500-robot scenario fast:
    a flood fill per robot would be 500 sweeps over the same grid.
    """

    occupied = index.occupancy
    components: dict[Cell, frozenset[Cell]] = {}

    def body_is_clear(cell: Cell) -> bool:
        return not any(
            occupied[cell[1] + dy][cell[0] + dx]
            for dy in range(height)
            for dx in range(width)
        )

    for row in range(index.rows - height + 1):
        for column in range(index.columns - width + 1):
            start = (column, row)
            if start in components or not body_is_clear(start):
                continue
            component: set[Cell] = set()
            queue = deque([start])
            component.add(start)
            while queue:
                cell_x, cell_y = queue.popleft()
                for delta_x, delta_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    neighbour = (cell_x + delta_x, cell_y + delta_y)
                    if neighbour in component or neighbour in components:
                        continue
                    if neighbour[0] < 0 or neighbour[1] < 0:
                        continue
                    if (
                        neighbour[0] + width > index.columns
                        or neighbour[1] + height > index.rows
                    ):
                        continue
                    if not body_is_clear(neighbour):
                        continue
                    component.add(neighbour)
                    queue.append(neighbour)
            frozen = frozenset(component)
            for cell in component:
                components[cell] = frozen
    return components


def _shared_target_cells(
    index: GridIndex,
    footprints: Sequence[tuple[int, int]],
    start_cells: Sequence[Cell],
) -> frozenset[Cell]:
    """Return the cells reachable by *every* robot in the fleet.

    Reachability is checked by intersecting each body's connected component. If
    the intersection is empty the fleet spans disconnected pockets -- for
    example a small robot sitting inside a one-cell express corridor -- and the
    caller is expected to have placed everyone in one region first.
    """

    by_size: dict[tuple[int, int], dict[Cell, frozenset[Cell]]] = {}
    shared: set[Cell] | None = None
    for size, start in zip(footprints, start_cells):
        if size not in by_size:
            by_size[size] = _component_index(index, size[0], size[1])
        component = by_size[size].get(start)
        if not component:
            raise ValueError(
                f"a {size[0]}x{size[1]} robot cannot move from cell {start}"
            )
        shared = set(component) if shared is None else (shared & component)
        if not shared:
            raise ValueError(
                "no task target is reachable by every robot; the fleet spans "
                "disconnected regions of the floor"
            )
    return frozenset(shared or frozenset())


def _largest_region(
    index: GridIndex,
    footprints: Sequence[tuple[int, int]],
) -> frozenset[Cell]:
    """Return the largest region the biggest body in the fleet can drive in.

    Placing every robot inside this one region is what guarantees they can all
    reach the same task targets: a smaller footprint can traverse everything a
    larger one can, so the intersection of their reachable sets is the region
    itself.
    """

    biggest = max(footprints, key=lambda size: size[0] * size[1])
    components = _component_index(index, biggest[0], biggest[1])
    if not components:
        raise ValueError(
            f"a {biggest[0]}x{biggest[1]} robot cannot be placed on this floor"
        )
    return max(set(components.values()), key=len)


def _start_cells_for(
    index: GridIndex,
    footprints: Sequence[tuple[int, int]],
    allowed: frozenset[Cell] | None = None,
) -> tuple[Cell, ...]:
    """Spread robots over open cells, keeping start footprints disjoint.

    ``allowed`` restricts candidates to one connected region, so a scenario
    does not accidentally seed half the fleet inside a pocket no large body can
    leave.
    """

    chosen: list[Cell] = []
    occupied: set[Cell] = set()
    candidates = [
        (column, row)
        for row in range(index.rows)
        for column in range(index.columns)
        if index.footprint_is_free((column, row), 1, 1)
        and (allowed is None or (column, row) in allowed)
    ]
    if not candidates:
        raise ValueError("a scenario needs a usable floor")
    stride = max(1, len(candidates) // max(len(footprints), 1))
    ordered = candidates[::stride] + candidates[::-stride]
    for width, height in footprints:
        placed: Cell | None = None
        for cell in ordered:
            if not index.footprint_is_free(cell, width, height):
                continue
            footprint = {
                (cell[0] + dx, cell[1] + dy)
                for dy in range(height)
                for dx in range(width)
            }
            if footprint & occupied:
                continue
            placed = cell
            break
        if placed is None:
            raise ValueError(
                f"cannot place a {width}x{height} robot on a floor this size; "
                "use a larger grid or a smaller fleet"
            )
        chosen.append(placed)
        occupied |= {
            (placed[0] + dx, placed[1] + dy)
            for dy in range(height)
            for dx in range(width)
        }
    return tuple(chosen)


def _spread(cells: Sequence[Cell], count: int, min_gap: int) -> tuple[Cell, ...]:
    """Pick ``count`` well-separated cells from ``cells``, deterministically."""

    if count < 1 or not cells:
        return ()
    ordered = list(cells)
    stride = max(1, len(ordered) // count)
    ordered = ordered[::stride] + ordered[::-stride]
    chosen: list[Cell] = []
    seen: set[Cell] = set()
    for cell in ordered:
        if len(chosen) >= count:
            break
        if cell in seen:
            continue
        if any(
            abs(cell[0] - other[0]) + abs(cell[1] - other[1]) < min_gap
            for other in chosen
        ):
            continue
        seen.add(cell)
        chosen.append(cell)
    for cell in cells:
        if len(chosen) >= count:
            break
        if cell not in seen:
            seen.add(cell)
            chosen.append(cell)
    return tuple(chosen[:count])


def build_scenario_fleet(spec: ScenarioSpec) -> tuple[FleetBlueprint, tuple[Task, ...]]:
    """Build the fleet and the initial tasks for a scenario.

    Task targets are chosen from the cells that **every** robot in the fleet can
    reach, because negotiation scores bids on distance, battery, and workload and
    is blind to reachability. Sharing the target set means allocation cannot
    hand a robot a job it structurally cannot do, so a blocked task later on is
    a real safety or negotiation outcome rather than a spawn artefact. Conflicts
    still happen, because it is the *routes* that cross, not the targets.
    """

    layout: Layout = build_layout(spec.layout, spec.columns, spec.rows)
    world = build_world(
        width_m=spec.columns * 1.0,
        height_m=spec.rows * 1.0,
        cell_size_m=1.0,
        obstacle_cells=layout.obstacles,
        charging_cells=layout.charging,
        workstation_cells=layout.workstations,
        resource_cells=layout.resources,
        revision=1,
    )
    index = GridIndex(world)
    profiles = _fleet_specs(spec)
    footprints = [(profile.width_cells, profile.height_cells) for profile in profiles]
    # Everyone starts in the region the biggest body can drive, so every robot
    # can serve every task. A blocked task later is then a real outcome.
    region = _largest_region(index, footprints)
    start_cells = _start_cells_for(index, footprints, region)

    # Targets must be reachable by every robot, so intersect the reachable sets.
    shared = _shared_target_cells(index, footprints, start_cells)
    min_gap = max(4, min(spec.columns, spec.rows) // max(spec.task_count, 1))
    targets = _spread(sorted(shared), spec.task_count, min_gap)
    if len(targets) < spec.task_count:
        targets = _spread(sorted(shared), spec.task_count, 1)

    robots = tuple(
        _make_scenario_robot(
            profile.robot_id,
            start_cells[offset],
            battery=spec.batteries[offset % len(spec.batteries)],
            capabilities=CAPABILITY_SETS[offset % len(CAPABILITY_SETS)],
        )
        for offset, profile in enumerate(profiles)
    )

    blueprint = FleetBlueprint(
        world=world,
        profiles=RobotProfileRegistry(profiles),
        robots=robots,
        start_cells=start_cells,
    )

    tasks = tuple(
        Task(
            task_id=f"task-{offset + 1:03d}",
            target=cell_to_world_position(cell, 1.0),
            priority=spec.priorities[offset % len(spec.priorities)],
            required_capabilities=(
                (spec.required_capability,) if spec.required_capability else ()
            ),
            estimated_duration_s=20.0,
            status=TaskStatus.PENDING,
            assigned_robot_id=None,
            created_at_s=0.0,
        )
        for offset, cell in enumerate(targets)
    )
    return blueprint, tasks


def _make_scenario_robot(
    robot_id: str,
    cell: Cell,
    *,
    battery: float,
    capabilities: Sequence[RobotCapability],
):
    from backend.simulation.world import make_robot

    return make_robot(
        robot_id,
        cell,
        cell_size_m=1.0,
        battery_percent=battery,
        capabilities=tuple(capabilities),
    )


_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        name="normal",
        layout="normal",
        description="Warehouse layout, 10 mixed robots, 6 spread tasks.",
    ),
    ScenarioSpec(
        name="crossing",
        layout="crossing",
        description="Four-way intersection: routes cross, conflicts are frequent.",
        task_count=8,
    ),
    ScenarioSpec(
        name="deadlock",
        layout="deadlock",
        description="Cross-aisle pinched to one cell: robots deadlock head-on, then one backs off.",
        task_count=6,
    ),
    ScenarioSpec(
        name="battery",
        layout="normal",
        description="Two robots start below the low-battery threshold.",
        task_count=5,
        batteries=(9.0, 17.0, 61.0, 95.0, 82.0, 74.0, 68.0, 91.0, 55.0, 77.0),
    ),
    ScenarioSpec(
        name="failure",
        layout="normal",
        description="Normal layout; fail a robot to watch task reassignment.",
        task_count=6,
    ),
    ScenarioSpec(
        name="communication",
        layout="high-traffic",
        description="High traffic; drop a robot's link to watch work migration.",
        task_count=6,
    ),
    ScenarioSpec(
        name="high-traffic",
        layout="high-traffic",
        description="Open floor with pillars: many crossing routes.",
        task_count=8,
    ),
)

#: Preset ids the UI can request, in menu order.
SCENARIO_NAMES: tuple[str, ...] = tuple(spec.name for spec in _SCENARIOS)


def scenario_specs() -> tuple[ScenarioSpec, ...]:
    """Return every preset, for the scenario editor to list."""

    return _SCENARIOS


def spec_for(
    name: str,
    *,
    robot_count: int | None = None,
    columns: int | None = None,
    rows: int | None = None,
    seed: int | None = None,
) -> ScenarioSpec:
    """Return a preset, optionally resized for a scale test.

    Resizing never changes the layout family, so a 500-robot run is still the
    same designed warehouse with more of it.
    """

    for spec in _SCENARIOS:
        if spec.name == name:
            break
    else:
        raise ValueError(
            f"unknown scenario {name!r}; expected one of {list(SCENARIO_NAMES)}"
        )
    updates: dict[str, object] = {}
    if robot_count is not None:
        updates["robot_count"] = robot_count
        if columns is None and rows is None:
            # A scale request names a fleet size; grow the floor to suit so the
            # caller does not have to compute a grid size as well.
            grown_columns, grown_rows = auto_grid_for(robot_count)
            updates["columns"] = grown_columns
            updates["rows"] = grown_rows
    if columns is not None:
        updates["columns"] = columns
    if rows is not None:
        updates["rows"] = rows
    if seed is not None:
        updates["seed"] = seed
    if not updates:
        return spec
    # A resized fleet needs more body shapes than the default list provides, so
    # the size and speed cycles are simply extended by repetition.
    return replace(spec, **updates)  # type: ignore[arg-type]

