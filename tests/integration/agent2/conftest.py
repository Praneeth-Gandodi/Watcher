"""Shared deterministic worlds and fleets for the Agent 2 integration scenarios."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from backend.app.composition import FleetCoordinator, build_coordinator
from backend.contracts.models import RobotCapability, Task, TaskStatus
from backend.simulation.grid import Cell, cell_to_world_position
from backend.simulation.world import FleetBlueprint, make_robot
from tests.unit.safety.conftest import (
    build_registry,
    wall_column_obstacles,
    world_with_obstacles,
)

#: Only robot-003 can perform a tug task, and only robot-004 an inspection.
#: Using ``required_capabilities`` is what lets the battery-low robot win work
#: despite its large battery-deficit bid cost.
ROBOT_001_CAPABILITIES = (RobotCapability.TRANSPORT, RobotCapability.PICK)
ROBOT_002_CAPABILITIES = (RobotCapability.TRANSPORT, RobotCapability.PICK)
ROBOT_003_CAPABILITIES = (
    RobotCapability.TRANSPORT,
    RobotCapability.PICK,
    RobotCapability.TUG,
)
ROBOT_004_CAPABILITIES = (RobotCapability.INSPECT, RobotCapability.PICK)
ROBOT_005_CAPABILITIES = (RobotCapability.DELIVER,)

#: A wall on column 5 whose only gap is the single cell (5, 3). Every
#: east-west route funnels through it, and no height-2 body can pass, which is
#: what makes fleet size, speed, and route availability interact.
NECK_OBSTACLES: tuple[Cell, ...] = wall_column_obstacles(10, 8, 5, 3)


def crossing_blueprint(
    *,
    battery_percent: float = 80.0,
) -> FleetBlueprint:
    """Two same-speed robots whose only routes cross at (2, 2)."""

    return FleetBlueprint(
        world=world_with_obstacles(10, 8, NECK_OBSTACLES),
        profiles=build_registry(
            [("robot-001", 1, 1, 1.0), ("robot-002", 1, 1, 1.0)]
        ),
        robots=(
            make_robot("robot-001", (1, 2), battery_percent=battery_percent),
            make_robot("robot-002", (2, 0), battery_percent=battery_percent),
        ),
        start_cells=((1, 2), (2, 0)),
    )


def five_robot_blueprint() -> FleetBlueprint:
    """Five robots, three body shapes, four speeds, and one forced conflict.

    All robots start with the same battery, so Agent 1's bid cost reduces to
    Euclidean distance plus a constant and the nearest candidate wins. That makes
    the whole scenario deterministic:

    ==============  ====  ======  ========  ==================================
    robot           body  speed   battery   role
    ==============  ====  ======  ========  ==================================
    ``robot-001``   1x1   0.5     70%       wins ``task-001`` -> (8, 3)
    ``robot-002``   1x1   2.0     70%       wins ``task-002`` -> (7, 5)
    ``robot-003``   3x2   0.5     19%       battery-low scenario
    ``robot-004``   1x2   1.5     70%       recovery after a fault
    ``robot-005``   2x1   0.75    70%       reassignment candidate
    ==============  ====  ======  ========  ==================================

    The wall on column 5 has a **single-cell** gap on row 3, so every
    east-west route funnels through (5, 3) and no height-2 body can pass.
    ``robot-001`` and ``robot-002`` are the two that can cross, they both cross,
    and at very different speeds their arrivals at the neck overlap, so they
    genuinely conflict. A timing delay resolves it because the two end up at
    different targets, so neither parks on a cell the other must cross.
    """

    return FleetBlueprint(
        world=world_with_obstacles(10, 8, NECK_OBSTACLES),
        profiles=build_registry(
            [
                ("robot-001", 1, 1, 0.5),
                ("robot-002", 1, 1, 2.0),
                ("robot-003", 3, 2, 0.5),
                ("robot-004", 1, 2, 1.5),
                ("robot-005", 2, 1, 0.75),
            ]
        ),
        robots=(
            make_robot(
                "robot-001",
                (4, 3),
                battery_percent=70.0,
                capabilities=ROBOT_001_CAPABILITIES,
            ),
            make_robot(
                "robot-002",
                (1, 3),
                battery_percent=70.0,
                capabilities=ROBOT_002_CAPABILITIES,
            ),
            make_robot(
                "robot-003",
                (0, 6),
                battery_percent=19.0,
                capabilities=ROBOT_003_CAPABILITIES,
            ),
            make_robot(
                "robot-004",
                (0, 0),
                battery_percent=70.0,
                capabilities=ROBOT_004_CAPABILITIES,
            ),
            make_robot(
                "robot-005",
                (2, 0),
                battery_percent=70.0,
                capabilities=ROBOT_005_CAPABILITIES,
            ),
        ),
        start_cells=((4, 3), (1, 3), (0, 6), (0, 0), (2, 0)),
    )


def ten_robot_blueprint() -> FleetBlueprint:
    """Ten canonical robot IDs with mixed sizes, four speeds, and a wall."""

    sizes = [(1, 1), (2, 2), (1, 2), (2, 1), (3, 2)]
    speeds = [0.5, 1.0, 1.5, 2.0]
    start_cells: tuple[Cell, ...] = (
        (0, 1), (1, 3), (2, 5), (0, 7),
        (7, 1), (8, 3), (7, 5), (8, 7),
        (3, 0), (4, 6),
    )
    return FleetBlueprint(
        world=world_with_obstacles(10, 8, NECK_OBSTACLES),
        profiles=build_registry(
            [
                (
                    f"robot-{offset + 1:03d}",
                    sizes[offset % len(sizes)][0],
                    sizes[offset % len(sizes)][1],
                    speeds[offset % len(speeds)],
                )
                for offset in range(10)
            ]
        ),
        robots=tuple(
            make_robot(
                f"robot-{offset + 1:03d}",
                start_cells[offset],
                battery_percent=float(55 + 5 * offset),
            )
            for offset in range(10)
        ),
        start_cells=start_cells,
    )


def task(
    task_id: str,
    cell: Cell,
    priority: int = 3,
    *,
    cell_size_m: float = 1.0,
    created_at_s: float = 0.0,
    required_capabilities: tuple[RobotCapability, ...] = (),
) -> Task:
    """Return a canonical task targeting the centre of ``cell``."""

    return Task(
        task_id=task_id,
        target=cell_to_world_position(cell, cell_size_m),
        priority=priority,
        required_capabilities=required_capabilities,
        estimated_duration_s=20.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=created_at_s,
    )


def tasks(items: Sequence[tuple[str, Cell, int]]) -> list[Task]:
    """Return canonical tasks from ``(task_id, cell, priority)`` triples."""

    return [task(task_id, cell, priority) for task_id, cell, priority in items]


def coordinator_for(blueprint: FleetBlueprint, **kwargs) -> FleetCoordinator:
    """Build a coordinator over an explicit blueprint."""

    return build_coordinator(blueprint, **kwargs)


__all__ = [
    "NECK_OBSTACLES",
    "coordinator_for",
    "crossing_blueprint",
    "five_robot_blueprint",
    "task",
    "tasks",
    "ten_robot_blueprint",
]


def assert_world_invariants(runtime, blueprint: FleetBlueprint) -> None:
    """Assert the invariants every scenario must preserve.

    No robot is out of the world, no robot sits on an obstacle, and every robot
    reports the cell the runtime believes it is in.
    """

    index = runtime.grid
    for state in runtime.robot_states():
        cell = index.cell_for_position(state.robot.position)
        assert cell == state.current_cell, (
            f"{state.robot_id} reports {cell} but is tracked at {state.current_cell}"
        )
        assert index.contains(*cell), f"{state.robot_id} left the world at {cell}"
        assert index.footprint_is_free(
            cell, state.profile.width_cells, state.profile.height_cells
        ), f"{state.robot_id} occupies a blocked or out-of-bounds cell at {cell}"
    del blueprint


CoordinatorFactory = Callable[..., FleetCoordinator]
