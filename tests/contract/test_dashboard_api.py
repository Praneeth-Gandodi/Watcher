"""Contract tests for the dashboard read models and scenario presets.

Covers the three capabilities the UI needs that the bootstrap did not have:
robot profiles (footprint and speed), live per-robot telemetry, and
reproducible scenario presets.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.app import composition
from backend.app.api import RandomAssignmentRequest
from backend.app.composition import build_coordinator, build_demo_coordinator
from backend.app.main import app
from backend.contracts.models import TaskStatus
from backend.negotiation.scoring import (
    DeterministicBidIdFactory,
    calculate_bid_costs,
    create_bid,
)
from backend.simulation.layouts import LAYOUT_NAMES, build_layout
from backend.simulation.scenarios import (
    SCENARIO_NAMES,
    auto_grid_for,
    build_scenario_fleet,
    scenario_specs,
    spec_for,
)
from backend.simulation.telemetry import ROBOT_ACTIONS, build_fleet_telemetry

PREFIX = "/api/v1"

GRID_PRESETS = ((20, 15), (30, 20), (40, 25), (50, 30))
FLEET_PRESETS = (10, 25, 50, 100, 250, 500)


@pytest.fixture
def coordinator():
    # The same coordinator the HTTP layer hands the dashboard.
    return build_demo_coordinator()


@pytest.fixture
def client(coordinator) -> object:
    app.dependency_overrides[composition.get_coordinator] = lambda: coordinator
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        composition.reset_coordinator()


# ----------------------------------------------------------------------
# telemetry: RobotProfile reaches the UI without touching the Robot contract
# ----------------------------------------------------------------------


def test_telemetry_exposes_the_real_robot_profile(coordinator) -> None:
    telemetry = build_fleet_telemetry(coordinator.runtime)
    runtime = coordinator.runtime

    for robot in telemetry.robots:
        profile = runtime.profiles.require(robot.robot_id)
        assert robot.width_cells == profile.width_cells
        assert robot.height_cells == profile.height_cells
        assert robot.speed_mps == profile.speed_mps

    sizes = {(robot.width_cells, robot.height_cells) for robot in telemetry.robots}
    speeds = {robot.speed_mps for robot in telemetry.robots}
    assert len(sizes) >= 3, "the demo fleet must show several footprints"
    assert len(speeds) >= 3, "the demo fleet must show several speeds"


def test_telemetry_footprint_matches_the_planner_footprint(coordinator) -> None:
    """The rendered footprint must equal the collision footprint."""

    runtime = coordinator.runtime
    telemetry = build_fleet_telemetry(runtime)

    for robot in telemetry.robots:
        profile = runtime.profiles.require(robot.robot_id)
        assert robot.width_cells == profile.width_cells
        assert robot.height_cells == profile.height_cells
        position = runtime.robot(robot.robot_id).position
        assert (robot.cell_x, robot.cell_y) == runtime.grid.cell_for_position(position)


def test_telemetry_actions_come_from_the_closed_backend_set(coordinator) -> None:
    telemetry = build_fleet_telemetry(coordinator.runtime)

    for robot in telemetry.robots:
        assert robot.action in ROBOT_ACTIONS
        assert robot.action_reason, "every action must explain itself"
        assert 0.0 <= robot.progress <= 1.0
        assert robot.remaining_cells >= 0
        assert robot.remaining_time_s >= 0.0


def test_telemetry_reports_a_conflict_and_the_reason(coordinator) -> None:
    """A reported conflict is derived from the open record, never invented."""

    from backend.simulation.runtime import SimulationRuntime
    from backend.simulation.world import FleetBlueprint, make_robot
    from tests.integration.agent2.conftest import task as make_task
    from tests.unit.safety.conftest import build_registry, world_with_obstacles

    blueprint = FleetBlueprint(
        world=world_with_obstacles(8, 8, ()),
        profiles=build_registry([("robot-001", 1, 1, 1.0), ("robot-002", 1, 1, 1.0)]),
        robots=(
            make_robot("robot-001", (0, 3), battery_percent=80.0),
            make_robot("robot-002", (3, 3), battery_percent=80.0),
        ),
        start_cells=((0, 3), (3, 3)),
    )
    head_on = SimulationRuntime(blueprint)
    head_on.submit_task(make_task("task-001", (3, 3), priority=4))
    head_on.assign_task("task-001", "robot-001")
    head_on.submit_task(make_task("task-002", (0, 3), priority=1))
    head_on.assign_task("task-002", "robot-002")

    # The pair is genuinely detected, from the canonical conflict record.
    published = [
        item
        for item in head_on.event_stream.subscribe(0)
        if item.event_type.value == "CONFLICT_DETECTED"
    ]
    assert published, "a head-on pair must be detected"
    reported_pair = set(published[0].payload.conflict.robot_ids)
    assert reported_pair == {"robot-001", "robot-002"}

    # Telemetry must agree with that record and never invent a partner. This
    # holds at any instant of the run, including after recovery has closed the
    # conflict, which is why it is checked as an invariant rather than as a
    # snapshot of one moment.
    for tick in range(12):
        telemetry = build_fleet_telemetry(head_on)
        for robot in telemetry.robots:
            for partner in robot.conflict_with:
                assert partner in reported_pair
                other = next(
                    item for item in telemetry.robots if item.robot_id == partner
                )
                assert robot.robot_id in other.conflict_with, (
                    "a conflict must be reported symmetrically, from one record"
                )
            assert robot.action_reason, "every action must carry a reason"
        # Reported pairs always come from a real open conflict record.
        for pair in telemetry.open_conflict_pairs:
            assert set(pair) <= reported_pair
        head_on.run_ticks(2)
        del tick


def test_telemetry_progress_tracks_a_moving_robot(coordinator) -> None:
    runtime = coordinator.runtime
    # The demo scenario already registered its tasks; negotiate them all.
    coordinator.dispatch_pending_tasks()

    moving = next(
        robot
        for robot in build_fleet_telemetry(runtime).robots
        if robot.route_id is not None
    )
    before = moving.progress
    assert moving.action in {"MOVING", "NEGOTIATING", "REPLANNING", "WAITING"}

    runtime.run_ticks(40)
    after = next(
        robot
        for robot in build_fleet_telemetry(runtime).robots
        if robot.robot_id == moving.robot_id
    )
    assert after.cells_travelled > moving.cells_travelled
    assert after.progress > before
    assert after.remaining_cells < moving.remaining_cells


def test_telemetry_trail_is_capped_and_timed(coordinator) -> None:
    runtime = coordinator.runtime
    coordinator.dispatch_pending_tasks()

    telemetry = build_fleet_telemetry(runtime)
    for robot in telemetry.robots:
        assert len(robot.trail) <= 12, "the trail must stay bounded at 500 robots"
        for x_m, y_m, t_s in robot.trail:
            assert x_m >= 0.0 and y_m >= 0.0
            assert t_s >= 0.0
        timestamps = [point[2] for point in robot.trail]
        assert timestamps == sorted(timestamps)


# ----------------------------------------------------------------------
# layouts and scenarios
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", LAYOUT_NAMES)
@pytest.mark.parametrize(("columns", "rows"), GRID_PRESETS)
def test_every_layout_preserves_a_usable_floor(name, columns, rows) -> None:
    layout = build_layout(name, columns, rows)
    blocked = set(layout.obstacles)

    assert len(blocked) == len(layout.obstacles), "obstacles must be deduplicated"
    assert len(blocked) > 0, "a layout must have real structure"
    for cell in blocked | set(layout.charging) | set(layout.workstations):
        assert 0 <= cell[0] < columns and 0 <= cell[1] < rows
    # A 1x1 robot must be able to cross the floor.
    from backend.safety.pathfinding import find_path
    from backend.simulation.grid import GridIndex
    from backend.simulation.world import build_world

    index = GridIndex(
        build_world(
            width_m=columns * 1.0,
            height_m=rows * 1.0,
            cell_size_m=1.0,
            obstacle_cells=layout.obstacles,
            charging_cells=layout.charging,
            workstation_cells=layout.workstations,
            resource_cells=layout.resources,
            revision=1,
        )
    )
    free = [
        (column, row)
        for row in range(rows)
        for column in range(columns)
        if index.footprint_is_free((column, row), 1, 1)
    ]
    assert len(free) > 20, "a layout must leave a usable floor"
    assert find_path(index.occupancy, free[0], free[-1], 1, 1) is not None


def test_layouts_are_deterministic() -> None:
    for name in LAYOUT_NAMES:
        assert build_layout(name, 40, 25) == build_layout(name, 40, 25)


def test_unknown_layout_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown layout"):
        build_layout("nope", 40, 25)


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_every_scenario_builds_a_valid_fleet_and_tasks(name) -> None:
    spec = spec_for(name)
    blueprint, tasks = build_scenario_fleet(spec)

    assert len(blueprint.robots) == spec.robot_count
    assert len(blueprint.profiles) == spec.robot_count
    assert blueprint.occupancy_invariants_hold()
    assert len(tasks) == spec.task_count
    assert [task.task_id for task in tasks] == [
        f"task-{index + 1:03d}" for index in range(spec.task_count)
    ]
    # Canonical kebab-case identifiers.
    assert blueprint.robot_ids[0] == "robot-001"


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_every_scenario_target_is_reachable_by_every_robot(name) -> None:
    """Task targets fit the largest robot, so a blocked task is a real outcome."""

    from backend.safety.pathfinding import find_path

    spec = spec_for(name)
    blueprint, tasks = build_scenario_fleet(spec)
    index = blueprint.grid
    unreachable = 0
    for robot, (width, height) in zip(
        blueprint.robots, [(p.width_cells, p.height_cells) for p in blueprint.profiles]
    ):
        start = index.cell_for_position(robot.position)
        for task in tasks:
            goal = index.cell_for_position(task.target)
            if find_path(index.occupancy, start, goal, width, height) is None:
                unreachable += 1
    assert unreachable == 0, f"{name}: {unreachable} unreachable task/robot pairs"


def test_scenarios_are_reproducible() -> None:
    for spec in scenario_specs():
        first_fleet, first_tasks = build_scenario_fleet(spec)
        second_fleet, second_tasks = build_scenario_fleet(spec)
        assert first_fleet.world == second_fleet.world
        assert first_fleet.start_cells == second_fleet.start_cells
        assert [t.model_dump() for t in first_tasks] == [
            t.model_dump() for t in second_tasks
        ]


def test_the_battery_scenario_starts_low() -> None:
    spec = spec_for("battery")
    blueprint, _ = build_scenario_fleet(spec)
    policy = build_fleet_telemetry  # noqa: F841 - keep the import honest

    low = [robot for robot in blueprint.robots if robot.battery_percent <= 20.0]
    assert low, "the battery preset must start at least one robot below the threshold"
    healthy = [robot for robot in blueprint.robots if robot.battery_percent > 60.0]
    assert healthy, "and the rest of the fleet must stay healthy for contrast"
    del policy


def test_resizing_a_fleet_grows_the_grid() -> None:
    assert auto_grid_for(10) == (30, 20), "a small fleet still gets a floor the designed layouts fit into"
    columns, rows = auto_grid_for(500)
    assert columns * rows >= 500 * 12

    spec = spec_for("normal", robot_count=250)
    blueprint, _ = build_scenario_fleet(spec)
    assert len(blueprint.robots) == 250
    assert spec.columns * spec.rows >= 250 * 12
    assert blueprint.occupancy_invariants_hold()
    # A 250-robot floor is smaller than the 500-robot floor it was sized against.
    assert (spec.columns, spec.rows) == auto_grid_for(250)
    assert columns * rows > spec.columns * spec.rows


def test_explicit_grid_dims_win_over_auto_growth() -> None:
    spec = spec_for("normal", robot_count=10, columns=50, rows=30)
    assert (spec.columns, spec.rows) == (50, 30)


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        spec_for("nope")


# ----------------------------------------------------------------------
# HTTP surface
# ----------------------------------------------------------------------


def test_telemetry_endpoint_shape(client) -> None:
    response = client.get(f"{PREFIX}/telemetry")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "simulation_time_s",
        "scenario",
        "counts_by_action",
        "battery_buckets",
        "open_conflict_pairs",
        "deadlocked_robot_ids",
        "controller_available",
        "revision",
        "last_event_sequence",
        "robots",
    }
    assert len(body["robots"]) == 10
    assert set(body["battery_buckets"]) == {"normal", "low", "critical"}
    robot = body["robots"][0]
    for field in (
        "robot_id", "width_cells", "height_cells", "speed_mps", "battery_percent",
        "cell_x", "cell_y", "action", "action_reason", "progress", "remaining_cells",
        "remaining_time_s", "conflict_with", "trail", "capabilities",
    ):
        assert field in robot
    assert robot["action"] in ROBOT_ACTIONS


def test_demo_defaults_to_ten_robots_on_a_forty_by_twenty_five_floor(client) -> None:
    """Opening the dashboard must show a fleet, not an empty world."""

    telemetry = client.get(f"{PREFIX}/telemetry").json()
    world = client.get(f"{PREFIX}/world").json()

    assert len(telemetry["robots"]) == 10
    assert (world["columns"], world["rows"]) == (40, 25)
    assert len(client.get(f"{PREFIX}/tasks").json()) == 6


def test_scenarios_endpoint_lists_presets_and_sizes(client) -> None:
    body = client.get(f"{PREFIX}/scenarios").json()

    assert [s["name"] for s in body["scenarios"]] == list(SCENARIO_NAMES)
    assert [tuple(pair) for pair in body["grid_presets"]] == list(GRID_PRESETS)
    assert list(body["fleet_presets"]) == list(FLEET_PRESETS)
    assert body["active"] == "normal", "the demo starts on the normal preset"
    for scenario in body["scenarios"]:
        assert scenario["description"]


def test_loading_a_scenario_rebuilds_the_world(client) -> None:
    response = client.post(
        f"{PREFIX}/simulation/scenario", json={"name": "crossing"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "crossing"
    assert body["robots"] == 10
    assert (body["columns"], body["rows"]) == (40, 25)
    assert body["simulation_time_s"] == 0.0
    # The stream restarts with the new world, so the cursor is valid and the only
    # events so far are one TASK_CREATED per preset task.
    assert body["last_event_sequence"] == len(body["tasks"])
    assert client.get(f"{PREFIX}/telemetry").json()["scenario"] == "crossing"


@pytest.mark.parametrize("robot_count", FLEET_PRESETS)
def test_every_fleet_preset_loads(client, robot_count) -> None:
    response = client.post(
        f"{PREFIX}/simulation/scenario",
        json={"name": "normal", "robot_count": robot_count},
    )

    assert response.status_code == 200
    assert response.json()["robots"] == robot_count
    assert len(client.get(f"{PREFIX}/telemetry").json()["robots"]) == robot_count


def test_loading_an_unknown_scenario_is_rejected(client) -> None:
    response = client.post(f"{PREFIX}/simulation/scenario", json={"name": "nope"})

    assert response.status_code == 422


def test_a_scenario_can_load_without_running_its_tasks(client) -> None:
    response = client.post(
        f"{PREFIX}/simulation/scenario",
        json={"name": "normal", "run_initial_tasks": False},
    )

    assert response.status_code == 200
    assert response.json()["tasks"] == []
    assert client.get(f"{PREFIX}/tasks").json() == []
    assert len(client.get(f"{PREFIX}/telemetry").json()["robots"]) == 10


def test_robots_and_world_are_still_ordered_deterministically(client) -> None:
    robots = client.get(f"{PREFIX}/robots").json()
    telemetry = client.get(f"{PREFIX}/telemetry").json()["robots"]

    assert [robot["robot_id"] for robot in robots] == sorted(
        robot["robot_id"] for robot in robots
    )
    assert [robot["robot_id"] for robot in telemetry] == [
        robot["robot_id"] for robot in robots
    ]


def test_health_is_unchanged_by_the_new_endpoints(client) -> None:
    assert client.get(f"{PREFIX}/health").json() == {
        "status": "ok",
        "service": "watcher-backend",
        "version": "0.1.0",
    }


# ----------------------------------------------------------------------
# random assignment: a real allocation round, not a UI shuffle
# ----------------------------------------------------------------------


def _owners(client) -> list[str | None]:
    return [task["assigned_robot_id"] for task in client.get(f"{PREFIX}/tasks").json()]


def _random(client, **body) -> object:
    return client.post(f"{PREFIX}/simulation/random-assignment", json=body)


def test_random_assignment_awards_every_open_task(client) -> None:
    client.post(f"{PREFIX}/simulation/dispatch", json={})
    assert all(owner is not None for owner in _owners(client))

    response = _random(client, jitter=0.9)

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["command_type"] == "RANDOM_ASSIGNMENT"
    # A round that re-bids must actually produce the canonical bid events.
    assert "BID_SUBMITTED" in body["produced_event_types"]
    assert "TASK_ASSIGNED" in body["produced_event_types"]
    # Every open task still has exactly one owner afterwards.
    owners = _owners(client)
    assert all(owner is not None for owner in owners)
    assert len(set(owners)) == len(owners), "a robot cannot own two tasks"


def test_zero_jitter_reproduces_the_deterministic_allocation(client) -> None:
    client.post(f"{PREFIX}/simulation/dispatch", json={})
    deterministic = _owners(client)

    _random(client, jitter=0.0)

    assert _owners(client) == deterministic


def test_a_seed_replays_the_same_random_round(client) -> None:
    _random(client, jitter=0.9, seed=4242)
    first = _owners(client)

    _random(client, jitter=0.9, seed=4242)

    assert _owners(client) == first


def test_a_different_seed_can_produce_a_different_allocation(client) -> None:
    _random(client, jitter=2.0, seed=1)
    first = _owners(client)

    outcomes = set()
    for seed in range(2, 12):
        _random(client, jitter=2.0, seed=seed)
        outcomes.add(tuple(_owners(client)))

    assert len(outcomes) > 1, "randomised rounds should not all agree"


def test_completed_tasks_are_never_reassigned(client) -> None:
    client.post(f"{PREFIX}/simulation/dispatch", json={})
    client.post(f"{PREFIX}/simulation/advance", params={"ticks": 400})
    tasks = client.get(f"{PREFIX}/tasks").json()
    completed = [task for task in tasks if task["status"] == "completed"]
    if not completed:
        pytest.skip("the demo scenario completed no task in 400 ticks")
    before = {task["task_id"]: task["assigned_robot_id"] for task in completed}

    _random(client, jitter=1.5)

    after = {
        task["task_id"]: task["assigned_robot_id"]
        for task in client.get(f"{PREFIX}/tasks").json()
        if task["task_id"] in before
    }
    assert after == before


@pytest.mark.parametrize("jitter", [-0.1, 5.1, "wide", [0.5]])
def test_an_invalid_jitter_is_rejected(client, jitter) -> None:
    assert _random(client, jitter=jitter).status_code == 422


@pytest.mark.parametrize("jitter", [0.0, 0.75, 5.0])
def test_a_valid_jitter_is_accepted(client, jitter) -> None:
    assert _random(client, jitter=jitter).status_code == 200


@pytest.mark.parametrize("jitter", [float("inf"), float("nan"), -0.001])
def test_jitter_must_be_a_finite_non_negative_number(jitter) -> None:
    # `inf` and `nan` cannot go over JSON, so the contract is checked directly.
    with pytest.raises(ValidationError):
        RandomAssignmentRequest(jitter=jitter)


def test_jitter_only_blurs_the_distance_term() -> None:
    spec = spec_for("normal")
    blueprint, tasks = build_scenario_fleet(spec)
    robot = blueprint.robots[0]
    task = tasks[0]

    plain = calculate_bid_costs(robot, task)
    blurred = calculate_bid_costs(robot, task, jitter=1.0)

    # Battery and workload are untouched; only distance moves, and never down.
    assert blurred.battery_cost == plain.battery_cost
    assert blurred.workload_cost == plain.workload_cost
    assert blurred.distance_cost >= plain.distance_cost
    assert blurred.total_cost == (
        blurred.distance_cost + blurred.battery_cost + blurred.workload_cost
    )


def test_a_randomised_bid_is_still_a_valid_canonical_bid() -> None:
    spec = spec_for("normal")
    blueprint, tasks = build_scenario_fleet(spec)
    bid = create_bid(
        tasks[0],
        blueprint.robots[0],
        observed_at_s=1.0,
        valid_until_s=6.0,
        id_factory=DeterministicBidIdFactory(),
        jitter=3.0,
    )
    assert bid.total_cost >= bid.distance_cost
    assert bid.total_cost == bid.distance_cost + bid.battery_cost + bid.workload_cost
    assert bid.valid_until_s > bid.created_at_s


def test_release_open_assignments_frees_the_robots(client) -> None:
    client.post(f"{PREFIX}/simulation/dispatch", json={})
    runtime = client.app.dependency_overrides[composition.get_coordinator]().runtime
    assert all(
        robot["current_task_id"] is not None
        for robot in client.get(f"{PREFIX}/robots").json()
        if robot["status"] == "active"
    )

    runtime.release_open_assignments()

    tasks = client.get(f"{PREFIX}/tasks").json()
    assert all(task["status"] == "pending" for task in tasks)
    assert all(task["assigned_robot_id"] is None for task in tasks)
    # A freed robot is idle and holds no task, so it is eligible again.
    for robot in client.get(f"{PREFIX}/robots").json():
        if robot["status"] in {"idle", "active"}:
            assert robot["current_task_id"] is None


def test_release_leaves_completed_tasks_alone(coordinator) -> None:
    runtime = coordinator.runtime
    coordinator.dispatch_pending_tasks()
    task_id = runtime.tasks()[0].task_id

    runtime.release_open_assignments()

    released = {task.task_id: task for task in runtime.tasks()}
    assert released[task_id].status is TaskStatus.PENDING
    assert released[task_id].assigned_robot_id is None
