"""Integration scenario E: wait-for cycles are detected and broken.

Flow: robots wait on cells held by each other -> ``DEADLOCK_DETECTED`` names
the cycle -> a ``RECOVERY_STARTED`` action migrates a task and replans -> the
fleet makes progress again.

The simulation runs once per module and is shared, because every assertion
reads the event log rather than driving the runtime.
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

    Heterogeneous capabilities create long crossing routes: a robot is often the
    only one able to take a job, so it is selected from across the floor and its
    path intersects others. A single-capability fleet always picks the nearest
    robot, routes stay short, and no wait-for cycle ever forms.
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


def reports_of(runtime: SimulationRuntime):
    return [
        event.payload.report
        for event in runtime.events
        if event.event_type is EventType.DEADLOCK_DETECTED
    ]


def deadlock_actions_of(runtime: SimulationRuntime):
    return [
        event.payload.action
        for event in runtime.events
        if event.event_type is EventType.RECOVERY_STARTED
        and "deadlock" in event.payload.action.reason
    ]


def test_scenario_e_deadlocks_are_detected(runtime: SimulationRuntime) -> None:
    reports = reports_of(runtime)
    assert reports, "a dense fleet never produced a wait-for cycle"
    report = reports[0]
    assert len(report.cycle_robot_ids) >= 2
    assert len(set(report.cycle_robot_ids)) == len(report.cycle_robot_ids)
    assert 0.0 <= report.confidence <= 1.0


def test_scenario_e_every_reported_cycle_is_named_and_timestamped(
    runtime: SimulationRuntime,
) -> None:
    for report in reports_of(runtime):
        assert report.deadlock_id
        assert report.detected_at_s >= 0


def test_scenario_e_a_detected_cycle_triggers_recovery(runtime: SimulationRuntime) -> None:
    sequences = [
        event.sequence
        for event in runtime.events
        if event.event_type is EventType.DEADLOCK_DETECTED
    ]
    assert sequences
    later = [
        event.sequence
        for event in runtime.events
        if event.sequence > sequences[0] and event.event_type is EventType.RECOVERY_STARTED
    ]
    assert later, "a detected deadlock produced no recovery action"


def test_scenario_e_recovery_breaks_the_cycle_by_migration_or_replan(
    runtime: SimulationRuntime,
) -> None:
    actions = deadlock_actions_of(runtime)
    assert actions
    kinds = {action.action_type.value for action in actions}
    assert kinds & {"task_migration", "replan"}


def test_scenario_e_recovery_targets_a_single_participant(runtime: SimulationRuntime) -> None:
    for action in deadlock_actions_of(runtime):
        assert len(action.target_robot_ids) == 1
        assert action.reason


def test_scenario_e_deadlocks_are_resolved(runtime: SimulationRuntime) -> None:
    assert runtime.snapshot().metrics.extra_metrics["deadlocks_resolved"] > 0


def test_scenario_e_the_fleet_keeps_completing_work(runtime: SimulationRuntime) -> None:
    snapshot = runtime.snapshot()
    assert snapshot.metrics.completed_tasks > 0
    assert any(task.status is TaskStatus.ASSIGNED for task in snapshot.tasks)
