"""Integration scenario G: the mission continues without the coordinator.

Flow: the coordination service becomes unavailable -> ``controller_available``
flips to false in the snapshot -> robots keep executing their work and claim new
tasks locally -> the service returns and normal negotiation resumes.

There is no coordinator-outage command in the canonical command set, so the
outage is a seeded, documented fault schedule rather than an invented verb.
That constraint is itself part of what this scenario demonstrates: the
simulation degrades without anyone inventing a new interface.
"""

from __future__ import annotations

from backend.contracts.events import EventType
from backend.contracts.models import TaskStatus
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime

OUTAGE_START_S = 5.0
OUTAGE_DURATION_S = 20.0


def build_runtime() -> SimulationRuntime:
    return SimulationRuntime(
        RuntimeConfig.for_fleet(
            30,
            initial_task_count=10,
            task_arrival_interval_s=0.5,
            controller_outage_at_s=OUTAGE_START_S,
            controller_outage_duration_s=OUTAGE_DURATION_S,
            controller_outage_repeat_s=0.0,
        )
    )


def test_scenario_g_controller_starts_available() -> None:
    runtime = build_runtime()
    assert runtime.snapshot().controller_available is True


def test_scenario_g_controller_becomes_unavailable_on_schedule() -> None:
    runtime = build_runtime()
    runtime.run_ticks(100)
    assert runtime.snapshot().controller_available is False


def test_scenario_g_controller_returns_after_the_window() -> None:
    runtime = build_runtime()
    runtime.run_ticks(400)
    snapshot = runtime.snapshot()
    assert snapshot.controller_available is True
    assert snapshot.metrics.extra_metrics["controller_outages"] >= 1.0


def test_scenario_g_tasks_are_still_assigned_during_the_outage() -> None:
    runtime = build_runtime()
    completed_during_outage: list[int] = []
    for _ in range(12):
        runtime.run_ticks(25)
        if not runtime.snapshot().controller_available:
            completed_during_outage.append(runtime.snapshot().metrics.completed_tasks)
    assert completed_during_outage, "the outage window was never observed"
    # Work completes while the coordinator is down; the fleet does not stall.
    assert max(completed_during_outage) > 0


def test_scenario_g_local_claiming_is_identified_as_such() -> None:
    runtime = build_runtime()
    runtime.run_ticks(300)
    local = [
        event
        for event in runtime.events
        if event.producer == "agent-2-local-claim"
    ]
    assert local, "no task was claimed while the coordinator was unavailable"
    assignment = next(
        event.payload.assignment
        for event in local
        if event.event_type is EventType.TASK_ASSIGNED
    )
    assert "unavailable" in assignment.reason


def test_scenario_g_robots_keep_their_work_through_the_outage() -> None:
    runtime = build_runtime()
    runtime.run_ticks(60)
    assert not runtime.snapshot().controller_available
    assert any(
        task.status is TaskStatus.ASSIGNED for task in runtime.snapshot().tasks
    ), "in-flight work was dropped when the coordinator went away"


def test_scenario_g_negotiation_resumes_after_the_outage() -> None:
    runtime = build_runtime()
    runtime.run_ticks(400)
    assert runtime.snapshot().controller_available is True
    assert any(event.producer == "agent-1-negotiation" for event in runtime.events)


def test_scenario_g_no_outage_command_was_invented() -> None:
    """The documented command set has no outage verb, and none was added."""

    from backend.contracts.commands import CommandType

    published = {command.value for command in CommandType}
    assert "INJECT_COORDINATOR_OUTAGE" not in published
    assert published == {
        "CREATE_TASK",
        "INJECT_ROBOT_FAILURE",
        "INJECT_COMMUNICATION_LOSS",
        "RESTORE_ROBOT",
        "PAUSE_SIMULATION",
        "RESUME_SIMULATION",
        "RESET_SIMULATION",
        "SET_SIMULATION_SPEED",
    }
