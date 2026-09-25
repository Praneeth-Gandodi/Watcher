"""Integration scenario D: predicted conflicts and right-of-way resolution.

Flow: two robots' predicted paths overlap -> ``CONFLICT_DETECTED`` -> the
documented priority order picks a side -> the loser yields and replans, and
both robots keep moving.

The simulation is run once per module and shared. Every assertion here reads the
event log and the final snapshot, so re-running an identical fleet for each
test would only cost time, not coverage.
"""

from __future__ import annotations

import pytest

from backend.contracts.events import EventType
from backend.contracts.models import TaskStatus
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime

FLEET_SIZE = 150
TICKS = 400


def build_runtime() -> SimulationRuntime:
    """A dense, capability-specialised fleet.

    Heterogeneous capabilities produce long crossing routes, which is what puts
    two robots' predicted paths in the same aisle at the same time. A
    single-capability fleet always picks the nearest robot and never contends.
    """

    return SimulationRuntime(
        RuntimeConfig.for_fleet(
            FLEET_SIZE,
            initial_task_count=FLEET_SIZE,
            task_arrival_interval_s=0.5,
            controller_outage_at_s=None,
        )
    )


@pytest.fixture(scope="module")
def runtime() -> SimulationRuntime:
    simulation = build_runtime()
    simulation.run_ticks(TICKS)
    return simulation


def conflicts_of(runtime: SimulationRuntime):
    return [
        event.payload.conflict
        for event in runtime.events
        if event.event_type is EventType.CONFLICT_DETECTED
    ]


def yields_of(runtime: SimulationRuntime):
    return [
        event.payload.action
        for event in runtime.events
        if event.event_type is EventType.RECOVERY_STARTED
        and event.payload.action.action_type.value == "yield"
    ]


def test_scenario_d_conflicts_are_detected_and_published(runtime: SimulationRuntime) -> None:
    conflicts = conflicts_of(runtime)
    assert conflicts, "a dense fleet never produced a predicted conflict"
    conflict = conflicts[0]
    assert len(conflict.robot_ids) >= 2
    assert conflict.kind.value in {"collision_risk", "right_of_way"}
    assert conflict.position.x >= 0 and conflict.position.y >= 0


def test_scenario_d_a_conflict_names_distinct_robots(runtime: SimulationRuntime) -> None:
    for conflict in conflicts_of(runtime):
        assert len(set(conflict.robot_ids)) == len(conflict.robot_ids)
        assert conflict.detected_at_s >= 0


def test_scenario_d_right_of_way_picks_one_robot_to_yield(runtime: SimulationRuntime) -> None:
    yields = yields_of(runtime)
    assert yields, "a conflict never produced a right-of-way yield"
    action = yields[0]
    assert len(action.target_robot_ids) == 1
    # The reason must name a robot, otherwise the decision is unauditable.
    assert any(robot_id in action.reason for robot_id in action.target_robot_ids)


def test_scenario_d_a_conflict_forces_a_replan(runtime: SimulationRuntime) -> None:
    types = [event.event_type for event in runtime.events]
    assert EventType.CONFLICT_DETECTED in types
    assert EventType.ROUTE_REPLANNED in types


def test_scenario_d_published_conflicts_are_unresolved_only(runtime: SimulationRuntime) -> None:
    for conflict in runtime.snapshot().conflicts:
        assert conflict.status.value in {"open", "resolving"}


def test_scenario_d_the_fleet_keeps_working_despite_conflicts(runtime: SimulationRuntime) -> None:
    snapshot = runtime.snapshot()
    assert snapshot.metrics.completed_tasks > 0
    assert snapshot.metrics.extra_metrics["conflict_yields"] > 0
    assert any(task.status is TaskStatus.ASSIGNED for task in snapshot.tasks)
