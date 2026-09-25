"""Deterministic world and fleet construction.

Everything the simulation needs to boot is built here from explicit inputs, so
two runs with the same inputs produce byte-identical worlds. No randomness is
used at module level: fixtures that want variation take a ``seed`` and derive
every value from a local ``random.Random`` instance.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import isqrt
from random import Random

from backend.contracts.models import (
    CommunicationState,
    GridCell,
    GridCellType,
    Position2D,
    Robot,
    RobotCapability,
    RobotStatus,
    WorldState,
)
from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
from backend.simulation.grid import Cell, GridIndex

__all__ = [
    "DEMO_CELL_SIZE_M",
    "FleetBlueprint",
    "build_charging_cells",
    "build_fleet",
    "build_obstacle_cells",
    "build_workstation_cells",
    "build_world",
    "free_start_cells",
    "make_robot",
    "robot_id_for",
]

DEMO_CELL_SIZE_M = 1.0

_ROBOT_CAPABILITIES: tuple[RobotCapability, ...] = (
    RobotCapability.TRANSPORT,
    RobotCapability.PICK,
    RobotCapability.TUG,
    RobotCapability.INSPECT,
    RobotCapability.DELIVER,
)


def robot_id_for(index: int) -> str:
    """Return the canonical kebab-case robot ID for a one-based fleet index."""

    if index < 1:
        raise ValueError("robot index must be one-based")
    return f"robot-{index:03d}"


def build_obstacle_cells(
    columns: int,
    rows: int,
    *,
    seed: int = 2026,
    density: float = 0.12,
) -> tuple[Cell, ...]:
    """Return a deterministic, connected obstacle field.

    A wall at the horizontal middle keeps the two halves of the world reachable
    only through a gap, which guarantees that route planning actually has to
    negotiate geometry instead of walking in a straight line.
    """

    rng = Random(seed)
    blocked: set[Cell] = set()
    gap_row = max(1, rows // 2)
    wall_column = max(2, columns // 2)
    for row in range(rows):
        if row == gap_row:
            continue
        blocked.add((wall_column, row))
    target = int(columns * rows * density)
    attempts = 0
    while len(blocked) < target and attempts < columns * rows * 20:
        attempts += 1
        cell = (rng.randrange(columns), rng.randrange(rows))
        if cell[0] in (0, columns - 1) or cell[1] in (0, rows - 1):
            continue
        blocked.add(cell)
    blocked.add((wall_column, gap_row - 1) if gap_row - 1 >= 0 else (wall_column, 0))
    blocked.discard((wall_column, gap_row))
    return tuple(sorted(blocked))


def build_charging_cells(cells: Sequence[Cell]) -> tuple[Cell, ...]:
    """Return the cells nearest the bottom-left corner as charging stations."""

    if not cells:
        return ()
    anchor = min(cells, key=lambda cell: (cell[1], cell[0]))
    return (anchor,)


def build_workstation_cells(cells: Sequence[Cell]) -> tuple[Cell, ...]:
    """Return the cells nearest the top-right corner as workstations."""

    if not cells:
        return ()
    anchor = max(cells, key=lambda cell: (cell[1], -cell[0]))
    return (anchor,)


def build_world(
    *,
    width_m: float = 20.0,
    height_m: float = 16.0,
    cell_size_m: float = DEMO_CELL_SIZE_M,
    obstacle_cells: Sequence[Cell] = (),
    charging_cells: Sequence[Cell] = (),
    workstation_cells: Sequence[Cell] = (),
    resource_cells: Sequence[Cell] = (),
    deadzone_cells: Sequence[Cell] = (),
    revision: int = 1,
) -> WorldState:
    """Build a canonical ``WorldState`` from sparse typed cells.

    ``width_m``/``height_m`` are derived from the cell grid when they are not
    supplied, so the contract can never disagree with the geometry.
    """

    columns = int(round(width_m / cell_size_m))
    rows = int(round(height_m / cell_size_m))
    if columns < 1 or rows < 1:
        raise ValueError("the world must contain at least one cell")

    typed: dict[Cell, GridCellType] = {}
    for cell in obstacle_cells:
        typed[cell] = GridCellType.OBSTACLE
    for cell in resource_cells:
        typed[cell] = GridCellType.RESOURCE
    for cell in charging_cells:
        typed[cell] = GridCellType.CHARGING
    for cell in workstation_cells:
        typed[cell] = GridCellType.WORKSTATION
    for cell in deadzone_cells:
        typed[cell] = GridCellType.DEADZONE

    cells = tuple(
        GridCell(cell_x=cell[0], cell_y=cell[1], cell_type=typed[cell])
        for cell in sorted(typed)
    )
    return WorldState(
        width_m=columns * cell_size_m,
        height_m=rows * cell_size_m,
        cell_size_m=cell_size_m,
        columns=columns,
        rows=rows,
        cells=cells,
        revision=revision,
    )


def make_robot(
    robot_id: str,
    cell: Cell,
    *,
    cell_size_m: float = DEMO_CELL_SIZE_M,
    battery_percent: float = 90.0,
    capabilities: Sequence[RobotCapability] = _ROBOT_CAPABILITIES,
    workload: int = 0,
    status: RobotStatus = RobotStatus.IDLE,
    current_task_id: str | None = None,
    communication_state: CommunicationState = CommunicationState.ONLINE,
    last_updated_at_s: float = 0.0,
) -> Robot:
    """Build a canonical ``Robot`` positioned at the centre of ``cell``."""

    from backend.simulation.grid import cell_to_world_position

    return Robot(
        robot_id=robot_id,
        position=cell_to_world_position(cell, cell_size_m),
        battery_percent=battery_percent,
        capabilities=tuple(capabilities),
        workload=workload,
        status=status,
        current_task_id=current_task_id,
        communication_state=communication_state,
        failure=None,
        last_updated_at_s=last_updated_at_s,
    )


@dataclass(frozen=True, slots=True)
class FleetBlueprint:
    """Deterministic world + fleet description for a demo or a scale fixture."""

    world: WorldState
    profiles: RobotProfileRegistry
    robots: tuple[Robot, ...]
    start_cells: tuple[Cell, ...]

    @property
    def grid(self) -> GridIndex:
        return GridIndex(self.world)

    @property
    def robot_ids(self) -> tuple[str, ...]:
        return tuple(robot.robot_id for robot in self.robots)

    def robot(self, robot_id: str) -> Robot:
        for robot in self.robots:
            if robot.robot_id == robot_id:
                return robot
        raise KeyError(robot_id)

    def occupancy_invariants_hold(self) -> bool:
        """Return whether every robot footprint fits inside a free world."""

        index = self.grid
        return all(
            index.footprint_is_free(
                cell, self.profiles.get(robot.robot_id).width_cells,
                self.profiles.get(robot.robot_id).height_cells,
            )
            for robot, cell in zip(self.robots, self.start_cells)
        )


def free_start_cells(
    index: GridIndex,
    count: int,
    footprints: Sequence[tuple[int, int]],
) -> tuple[Cell, ...]:
    """Return ``count`` deterministically spaced, non-overlapping start cells.

    Footprint-aware: each robot is only placed where its whole body fits inside
    the world and clear of obstacles, and no two robots start on top of each
    other. ``footprints`` is ordered by robot, so slot *i* is validated against
    the body that will actually occupy it.

    Raises ``ValueError`` when the world is too small for the requested fleet,
    rather than silently placing a robot outside the grid.
    """

    if count < 1:
        raise ValueError("count must be positive")
    if not footprints:
        raise ValueError("footprints must not be empty")
    columns, rows = index.columns, index.rows
    candidates = [
        (column, row)
        for row in range(rows)
        for column in range(columns)
        if index.footprint_is_free((column, row), 1, 1)
    ]
    if not candidates:
        raise ValueError("the world has no traversable cells")

    stride = max(1, len(candidates) // max(count, 1))
    # Interleaved sweeps keep the placement spread over the whole world and
    # still cover every candidate, so greedy packing converges instead of
    # leaving a dense band unused.
    ordered: list[Cell] = []
    for offset in range(stride):
        ordered.extend(candidates[offset::stride])

    chosen: list[Cell] = []
    occupied: set[Cell] = set()
    for candidate in ordered:
        if len(chosen) >= count:
            break
        # The footprint must be the one the robot filling this slot will use.
        width_cells, height_cells = footprints[len(chosen) % len(footprints)]
        if not index.footprint_is_free(candidate, width_cells, height_cells):
            continue
        footprint = set(
            (candidate[0] + offset_x, candidate[1] + offset_y)
            for offset_y in range(height_cells)
            for offset_x in range(width_cells)
        )
        if footprint & occupied:
            continue
        chosen.append(candidate)
        occupied |= footprint
    if len(chosen) < count:
        raise ValueError(
            f"cannot place {count} robots in a {columns}x{rows} world "
            f"({len(chosen)} placed); reduce the fleet size or enlarge the world"
        )
    return tuple(chosen)


def build_fleet(
    columns: int = 20,
    rows: int = 16,
    *,
    robot_count: int = 5,
    cell_size_m: float = DEMO_CELL_SIZE_M,
    obstacle_density: float = 0.12,
    seed: int = 2026,
    sizes: Sequence[tuple[int, int]] = ((1, 1), (2, 2), (3, 2), (1, 2), (2, 1)),
    speeds_mps: Sequence[float] = (1.0, 2.0, 0.5, 1.5, 0.75),
    battery_percentages: Sequence[float] | None = None,
) -> FleetBlueprint:
    """Build a deterministic world and fleet for demos, tests, and benchmarks.

    ``sizes`` and ``speeds_mps`` cycle across the fleet, so a five-robot demo
    already exercises 1x1, 2x2, and 3x2 bodies at four different speeds.
    """

    if robot_count < 1:
        raise ValueError("robot_count must be positive")
    if not sizes or not speeds_mps:
        raise ValueError("sizes and speeds_mps must not be empty")

    obstacles = build_obstacle_cells(columns, rows, seed=seed, density=obstacle_density)
    world = build_world(
        width_m=columns * cell_size_m,
        height_m=rows * cell_size_m,
        cell_size_m=cell_size_m,
        obstacle_cells=obstacles,
        charging_cells=build_charging_cells(obstacles),
        workstation_cells=build_workstation_cells(obstacles),
        revision=1,
    )
    index = GridIndex(world)

    profiles: list[RobotProfile] = []
    for offset in range(robot_count):
        width_cells, height_cells = sizes[offset % len(sizes)]
        profiles.append(
            RobotProfile(
                robot_id=robot_id_for(offset + 1),
                width_cells=width_cells,
                height_cells=height_cells,
                speed_mps=speeds_mps[offset % len(speeds_mps)],
            )
        )
    footprints = [
        (profile.width_cells, profile.height_cells) for profile in profiles
    ]
    start_cells = free_start_cells(index, robot_count, footprints)

    robots: list[Robot] = []
    for offset, profile in enumerate(profiles):
        battery = (
            battery_percentages[offset % len(battery_percentages)]
            if battery_percentages
            else float(45 + 10 * (offset % 6))
        )
        robots.append(
            make_robot(
                profile.robot_id,
                start_cells[offset],
                cell_size_m=cell_size_m,
                battery_percent=battery,
            )
        )

    return FleetBlueprint(
        world=world,
        profiles=RobotProfileRegistry(profiles),
        robots=tuple(robots),
        start_cells=start_cells,
    )


def build_scale_fleet(robot_count: int = 500, *, seed: int = 2026) -> FleetBlueprint:
    """Build a lightweight, deterministic fixture for a large fleet.

    The world grows with the fleet so 500 robots still have room to move. This
    is intentionally a fixture, not a tuned benchmark world: the point is to
    measure the backend's behaviour and cost, not to optimise it.
    """

    if robot_count < 1:
        raise ValueError("robot_count must be positive")
    # Budget roughly twelve cells per robot: the largest body is 3x2 and greedy
    # placement also needs elbow room between footprints.
    cell_budget = robot_count * 12
    columns = max(20, isqrt(cell_budget) + 1)
    rows = max(16, -(-cell_budget // columns))
    return build_fleet(
        columns=columns,
        rows=rows,
        robot_count=robot_count,
        obstacle_density=0.08,
        seed=seed,
    )


def profiles_from_mapping(
    mapping: Mapping[str, RobotProfile],
) -> RobotProfileRegistry:
    """Return a registry built from a plain mapping."""

    return RobotProfileRegistry(mapping)


def profiles_from_iterable(
    profiles: Iterable[RobotProfile],
) -> RobotProfileRegistry:
    """Return a registry built from an iterable of profiles."""

    return RobotProfileRegistry(profiles)


def default_start_position() -> Position2D:
    """Centre of the origin cell, used as a neutral fallback position."""

    return Position2D(x=DEMO_CELL_SIZE_M / 2.0, y=DEMO_CELL_SIZE_M / 2.0)
