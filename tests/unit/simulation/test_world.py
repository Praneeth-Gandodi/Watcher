"""Deterministic world and fleet construction."""

from __future__ import annotations

import pytest
from backend.contracts.models import GridCellType, RobotStatus
from backend.safety.robot_profile import RobotProfile
from backend.simulation.grid import GridIndex
from backend.simulation.world import (
    build_charging_cells,
    build_fleet,
    build_obstacle_cells,
    build_scale_fleet,
    build_world,
    build_workstation_cells,
    make_robot,
    robot_id_for,
)


def test_robot_ids_follow_the_kebab_case_convention() -> None:
    assert robot_id_for(1) == "robot-001"
    assert robot_id_for(10) == "robot-010"
    assert robot_id_for(500) == "robot-500"
    with pytest.raises(ValueError):
        robot_id_for(0)


def test_a_built_world_derives_its_dimensions_from_the_cell_grid() -> None:
    world = build_world(width_m=20.0, height_m=12.0, cell_size_m=2.0, obstacle_cells=[(1, 1)])

    assert (world.columns, world.rows) == (10, 6)
    assert (world.width_m, world.height_m) == (20.0, 12.0)
    assert world.cells[0].cell_x == 1
    assert world.cells[0].cell_type is GridCellType.OBSTACLE
    assert GridIndex(world).is_blocked(1, 1)


def test_typed_cells_are_all_supported() -> None:
    world = build_world(
        width_m=6.0,
        height_m=4.0,
        cell_size_m=1.0,
        obstacle_cells=[(0, 0)],
        resource_cells=[(1, 0)],
        charging_cells=[(2, 0)],
        workstation_cells=[(3, 0)],
        deadzone_cells=[(4, 0)],
    )

    # FREE is never a sparse cell: unlisted cells are implicitly free.
    cell_types = {cell.cell_type for cell in world.cells}
    assert cell_types == set(GridCellType) - {GridCellType.FREE}
    index = GridIndex(world)
    assert index.is_blocked(0, 0) and index.is_blocked(4, 0)
    assert index.is_traversable(1, 0) and index.is_traversable(2, 0)
    assert index.cell_type(5, 0) is GridCellType.FREE


def test_a_world_must_contain_at_least_one_cell() -> None:
    with pytest.raises(ValueError):
        build_world(width_m=1.0, height_m=1.0, cell_size_m=2.0)


def test_the_obstacle_field_is_deterministic_and_connected() -> None:
    first = build_obstacle_cells(20, 16, seed=2026)
    second = build_obstacle_cells(20, 16, seed=2026)
    different = build_obstacle_cells(20, 16, seed=7)

    assert first == second
    assert first != different
    assert first == tuple(sorted(first)), "obstacles must be sorted"
    # A full wall with one gap: every row but one on the wall column.
    wall_column = 10
    gap_row = 8
    for row in range(16):
        expected = row != gap_row
        assert ((wall_column, row) in first) is expected


def test_chargers_and_workstations_sit_at_opposite_corners() -> None:
    obstacles = build_obstacle_cells(20, 16, seed=2026)

    charging = build_charging_cells(obstacles)
    workstations = build_workstation_cells(obstacles)

    assert charging == (min(obstacles, key=lambda cell: (cell[1], cell[0])),)
    assert workstations == (max(obstacles, key=lambda cell: (cell[1], -cell[0])),)
    assert build_charging_cells(()) == ()
    assert build_workstation_cells(()) == ()


def test_a_built_robot_sits_at_the_centre_of_its_cell() -> None:
    robot = make_robot("robot-001", (3, 4), cell_size_m=2.0, battery_percent=55.0)

    assert robot.position.x == 7.0
    assert robot.position.y == 9.0
    assert robot.battery_percent == 55.0
    assert robot.status is RobotStatus.IDLE
    assert robot.communication_state.value == "online"
    assert robot.failure is None


def test_a_default_fleet_is_heterogeneous_and_deterministic() -> None:
    first = build_fleet()
    second = build_fleet()

    assert [robot.robot_id for robot in first.robots] == [
        "robot-001",
        "robot-002",
        "robot-003",
        "robot-004",
        "robot-005",
    ]
    assert first.start_cells == second.start_cells
    assert first.world == second.world
    assert [p.battery_percent_per_cell for p in first.profiles] == [1.0] * 5
    # Different body sizes and different speeds across the fleet.
    assert len({(p.width_cells, p.height_cells) for p in first.profiles}) >= 3
    assert len({p.speed_mps for p in first.profiles}) >= 4


def test_every_robot_starts_on_a_free_footprint_and_no_two_overlap() -> None:
    blueprint = build_fleet(robot_count=10)
    index = blueprint.grid
    occupied: dict[tuple[int, int], str] = {}

    for robot, cell in zip(blueprint.robots, blueprint.start_cells):
        profile = blueprint.profiles.get(robot.robot_id)
        assert index.footprint_is_free(cell, profile.width_cells, profile.height_cells)
        for footprint_cell in index.footprint_blocked_cells(
            cell, profile.width_cells, profile.height_cells
        ):
            assert footprint_cell not in occupied, "start footprints must not overlap"
            occupied[footprint_cell] = robot.robot_id

    assert blueprint.occupancy_invariants_hold()


def test_the_fleet_robots_report_their_start_cells() -> None:
    blueprint = build_fleet()

    for robot, cell in zip(blueprint.robots, blueprint.start_cells):
        assert blueprint.grid.cell_for_position(robot.position) == cell
    assert blueprint.robot("robot-001").robot_id == "robot-001"
    with pytest.raises(KeyError):
        blueprint.robot("robot-999")


def test_a_fleet_validates_its_inputs() -> None:
    with pytest.raises(ValueError):
        build_fleet(robot_count=0)
    with pytest.raises(ValueError):
        build_fleet(sizes=())
    with pytest.raises(ValueError):
        build_fleet(speeds_mps=())
    with pytest.raises(ValueError, match="cannot place"):
        build_fleet(columns=6, rows=4, robot_count=50)


@pytest.mark.parametrize("robot_count", [10, 50, 200, 500])
def test_a_scale_fixture_places_every_robot_safely(robot_count: int) -> None:
    blueprint = build_scale_fleet(robot_count)

    assert len(blueprint.robots) == robot_count
    assert len(blueprint.profiles) == robot_count
    assert len(set(blueprint.start_cells)) == robot_count
    assert blueprint.occupancy_invariants_hold()
    assert blueprint.world.columns * blueprint.world.rows >= robot_count * 6


def test_a_scale_fixture_is_reproducible() -> None:
    assert build_scale_fleet(50).start_cells == build_scale_fleet(50).start_cells
    assert build_scale_fleet(50).world == build_scale_fleet(50).world
    assert build_scale_fleet(50, seed=1).world != build_scale_fleet(50, seed=2).world
    with pytest.raises(ValueError):
        build_scale_fleet(0)


def test_custom_sizes_and_speeds_are_honoured() -> None:
    blueprint = build_fleet(
        robot_count=3,
        sizes=[(2, 3)],
        speeds_mps=[4.0],
        battery_percentages=[11.0, 22.0, 33.0],
    )

    assert all(
        (profile.width_cells, profile.height_cells, profile.speed_mps)
        == (2, 3, 4.0)
        for profile in blueprint.profiles
    )
    assert [robot.battery_percent for robot in blueprint.robots] == [
        11.0,
        22.0,
        33.0,
    ]


def test_profiles_default_when_a_robot_has_none() -> None:
    assert build_fleet().profiles.get("robot-001") == RobotProfile(
        "robot-001", 1, 1, 1.0
    )
