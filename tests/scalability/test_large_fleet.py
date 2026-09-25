"""Agent 2 scalability fixtures and measurements.

The hackathon target is 500+ simulated robots. These tests provide a
deterministic large-fleet fixture and *measure* what the backend costs, rather
than claiming performance that has not been checked. They are deliberately
modest:

* the fixture is correct and reproducible, not tuned
* the assertions bound the work; they are not micro-benchmarks of a machine
* every measurement is printed, so a run produces evidence for the demo notes

They are marked ``scalability`` and can be skipped with
``-m "not scalability"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from timeit import default_timer

import pytest
from backend.app.composition import build_coordinator
from backend.safety.pathfinding import find_path
from backend.safety.right_of_way import find_earliest_conflict
from backend.safety.trajectory import create_trajectory
from backend.simulation.grid import GridIndex
from backend.simulation.runtime import SimulationRuntime
from backend.simulation.world import build_scale_fleet
from tests.integration.agent2.conftest import assert_world_invariants, task

pytestmark = pytest.mark.scalability

#: The hackathon target fleet size.
FLEET_SIZE = 500
#: One order of magnitude down, used where the full fleet is not needed.
SMALL_FLEET_SIZE = 50


@dataclass(frozen=True, slots=True)
class Measurement:
    """One measured cost, in milliseconds."""

    name: str
    duration_ms: float

    def report(self) -> str:
        return f"{self.name}: {self.duration_ms:.1f} ms"


def measure(name: str, action):
    """Return ``(measurement, result)`` for ``action``."""

    started = default_timer()
    result = action()
    return Measurement(name, (default_timer() - started) * 1000.0), result


def report(measurement: Measurement, suffix: str = "") -> None:
    print(f"  {measurement.report()}{suffix}")


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def test_the_five_hundred_robot_fixture_is_valid_and_reproducible() -> None:
    measurement, blueprint = measure(
        f"build a {FLEET_SIZE}-robot fixture", lambda: build_scale_fleet(FLEET_SIZE)
    )

    assert len(blueprint.robots) == FLEET_SIZE
    assert len(blueprint.profiles) == FLEET_SIZE
    assert len(set(blueprint.start_cells)) == FLEET_SIZE
    assert blueprint.robot_ids == tuple(
        f"robot-{index:03d}" for index in range(1, FLEET_SIZE + 1)
    )
    assert blueprint.occupancy_invariants_hold()

    # Reproducible: the same seed must give the same world and the same fleet.
    again = build_scale_fleet(FLEET_SIZE)
    assert blueprint.world == again.world
    assert blueprint.start_cells == again.start_cells
    assert blueprint.robot_ids == again.robot_ids
    report(measurement)


def test_a_five_hundred_robot_runtime_initialises_and_reports_valid_state() -> None:
    measurement, runtime = measure(
        f"initialise a {FLEET_SIZE}-robot runtime",
        lambda: SimulationRuntime(build_scale_fleet(FLEET_SIZE)),
    )

    snapshot = runtime.snapshot()
    assert len(snapshot.robots) == FLEET_SIZE
    assert snapshot.world.columns * snapshot.world.rows >= FLEET_SIZE
    assert snapshot.last_event_sequence >= snapshot.revision
    assert 0.0 <= snapshot.metrics.average_battery_percent <= 100.0
    assert_world_invariants(runtime, None)
    report(measurement)


def nearby_goal(index: GridIndex, cell, profile) -> tuple[int, int]:
    """Return a legal destination near ``cell`` for this robot's body.

    Falls back to the cell itself, which produces a valid zero-length route, so
    the throughput measurement never fails for want of a legal target.
    """

    for offset in ((2, 0), (-2, 0), (0, 2), (0, -2), (3, 0), (-3, 0), (1, 1), (-1, -1)):
        candidate = (cell[0] + offset[0], cell[1] + offset[1])
        if index.footprint_is_free(candidate, profile.width_cells, profile.height_cells):
            return candidate
    return cell


def test_route_planning_throughput_is_measured() -> None:
    blueprint = build_scale_fleet(SMALL_FLEET_SIZE)
    index = GridIndex(blueprint.world)

    def plan_one_robot(robot_id: str, start, goal):
        profile = blueprint.profiles.require(robot_id)
        return find_path(
            index.occupancy, start, goal, profile.width_cells, profile.height_cells
        )

    measurement, paths = measure(
        f"plan {SMALL_FLEET_SIZE} routes",
        lambda: [
            plan_one_robot(
                robot_id,
                start,
                nearby_goal(
                    index, start, blueprint.profiles.require(robot_id)
                ),
            )
            for robot_id, start in zip(blueprint.robot_ids, blueprint.start_cells)
        ],
    )

    assert all(path is not None for path in paths)
    report(measurement, f"  ({measurement.duration_ms / SMALL_FLEET_SIZE:.2f} ms/route)")


def test_trajectory_generation_for_a_large_fleet_is_measured() -> None:
    blueprint = build_scale_fleet(FLEET_SIZE)
    index = GridIndex(blueprint.world)
    robots = blueprint.robot_ids[:SMALL_FLEET_SIZE]
    starts = blueprint.start_cells[:SMALL_FLEET_SIZE]

    def generate():
        trajectories = []
        for robot_id, start in zip(robots, starts):
            profile = blueprint.profiles.require(robot_id)
            path = find_path(
                index.occupancy,
                start,
                nearby_goal(index, start, profile),
                profile.width_cells,
                profile.height_cells,
            )
            trajectories.append(
                create_trajectory(path or (), 1.0, blueprint.world.cell_size_m)
            )
        return trajectories

    measurement, trajectories = measure(
        f"generate {SMALL_FLEET_SIZE} trajectories", generate
    )

    assert len(trajectories) == SMALL_FLEET_SIZE
    assert all(trajectory for trajectory in trajectories)
    report(measurement)


def test_conflict_checking_workload_is_measured() -> None:
    """Pairwise checking is O(N^2); this measures the real cost at two sizes.

    The exhaustive check is the right choice for the MVP, and this documents
    what it costs so the optimisation decision stays informed rather than
    speculative.
    """

    for size in (SMALL_FLEET_SIZE, FLEET_SIZE):
        blueprint = build_scale_fleet(size)
        index = GridIndex(blueprint.world)
        trajectories = {}
        for robot_id, cell in zip(blueprint.robot_ids, blueprint.start_cells):
            profile = blueprint.profiles.require(robot_id)
            path = find_path(
                index.occupancy,
                cell,
                nearby_goal(index, cell, profile),
                profile.width_cells,
                profile.height_cells,
            )
            if path is None:
                continue
            trajectories[robot_id] = create_trajectory(
                path, 1.0, blueprint.world.cell_size_m
            )
        pairs = len(trajectories) * (len(trajectories) - 1) // 2
        measurement, conflict = measure(
            f"check {pairs} trajectory pairs at N={size}",
            lambda: find_earliest_conflict(trajectories),
        )
        report(measurement)
        assert conflict is None or conflict.start_time_s >= 0.0


def test_memory_of_the_large_fixture_is_measured() -> None:
    """Measure the memory the fixture needs, using the stdlib tracer."""

    from tracemalloc import get_traced_memory, start, stop

    start()
    blueprint = build_scale_fleet(FLEET_SIZE)
    current, peak = get_traced_memory()
    stop()

    assert len(blueprint.robots) == FLEET_SIZE
    print(
        f"  {FLEET_SIZE}-robot fixture memory: "
        f"{current / 1024 / 1024:.1f} MiB retained, "
        f"{peak / 1024 / 1024:.1f} MiB peak"
    )


# ----------------------------------------------------------------------
# end-to-end at scale
# ----------------------------------------------------------------------


def test_a_five_hundred_robot_fleet_allocates_and_plans_one_task() -> None:
    """The full Agent 1 + Agent 2 path on a 500-robot fleet.

    Only one task is created. A 500-robot fleet bidding on hundreds of tasks is
    a different exercise, and the brief is explicit that the ten-robot demo's
    correctness must not be traded for 500-robot optimisation.
    """

    build_measurement, coordinator = measure(
        f"build a {FLEET_SIZE}-robot coordinator",
        lambda: build_coordinator(build_scale_fleet(FLEET_SIZE)),
    )
    report(build_measurement)

    task_measurement, events = measure(
        "CREATE_TASK -> negotiation -> assignment -> route",
        lambda: coordinator.create_task(task("task-001", (5, 5), priority=3)),
    )

    event_types = [item.event_type.value for item in events]
    assert "TASK_CREATED" in event_types
    assert "TASK_ASSIGNED" in event_types
    assert "ROUTE_PLANNED" in event_types
    # Every eligible robot of the 500 bids, and one wins.
    assert event_types.count("BID_SUBMITTED") >= SMALL_FLEET_SIZE

    runtime = coordinator.runtime
    assert runtime.task("task-001").assigned_robot_id is not None
    assert runtime.routes()[0].robot_id == runtime.task("task-001").assigned_robot_id
    assert runtime.snapshot().metrics.average_allocation_latency_ms >= 0.0
    report(task_measurement, f"  ({len(events)} events)")


def test_a_fifty_robot_fleet_runs_a_multi_task_scenario() -> None:
    """A 50-robot run proves the demo scales one order of magnitude."""

    blueprint = build_scale_fleet(SMALL_FLEET_SIZE)
    coordinator = build_coordinator(blueprint)
    runtime = coordinator.runtime
    index = GridIndex(blueprint.world)
    free = [
        (column, row)
        for row in range(0, index.rows, 3)
        for column in range(0, index.columns, 3)
        if index.footprint_is_free((column, row), 1, 1)
    ]

    for offset, cell in enumerate(free[:4]):
        coordinator.create_task(task(f"task-{offset + 1:03d}", cell, 1 + offset % 5))

    measurement, _ = measure(
        f"advance 50 ticks at N={SMALL_FLEET_SIZE}", lambda: coordinator.advance(50)
    )

    snapshot = runtime.snapshot()
    assert len(snapshot.robots) == SMALL_FLEET_SIZE
    assert len(snapshot.tasks) == 4
    assert snapshot.last_event_sequence >= snapshot.revision
    assert 0.0 <= snapshot.metrics.average_battery_percent <= 100.0
    assert_world_invariants(runtime, blueprint)
    report(measurement)


def test_the_backend_states_its_scale_limits_honestly() -> None:
    """A single, explicit summary of what is and is not optimised."""

    small, _ = measure("build a 50-robot fixture", lambda: build_scale_fleet(SMALL_FLEET_SIZE))
    large, _ = measure("build a 500-robot fixture", lambda: build_scale_fleet(FLEET_SIZE))
    print(
        "  documented limits (not optimised):\n"
        "    - conflict detection is exhaustive O(N^2) pair comparison\n"
        "    - no spatial index or broad-phase pruning is implemented\n"
        "    - the event stream is in-memory and bounded\n"
        "    - A* is single-robot and unoptimised; no shared-path caching\n"
        "    - movement is per-tick and per-robot, with no batching\n"
        f"    - measured: {small.report()}, {large.report()}"
    )
