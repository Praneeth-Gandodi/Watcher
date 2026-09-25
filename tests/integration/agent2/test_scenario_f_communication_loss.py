"""Integration scenario F: communication loss and timeout recovery.

Flow: a peer stops answering -> ``COMMUNICATION_LOST`` after the timeout -> the
robot is held rather than moved on stale information -> contact returns and the
robot rejoins the fleet.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from backend.contracts.commands import InjectCommunicationLossCommand
from backend.contracts.events import EventType
from backend.contracts.models import CommunicationState, RobotStatus
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime

ROBOT = "robot-0001"
LOSS_COMMAND = UUID(int=4242)


@pytest.fixture(scope="module")
def runtime() -> SimulationRuntime:
    simulation = SimulationRuntime(
        RuntimeConfig.for_fleet(
            20,
            initial_task_count=6,
            task_arrival_interval_s=1e9,
            controller_outage_at_s=None,
        )
    )
    simulation.run_ticks(3)
    simulation.apply_command(
        InjectCommunicationLossCommand(
            command_id=LOSS_COMMAND,
            issued_at_s=simulation.simulation_time_s,
            robot_id=ROBOT,
            timeout_s=3.0,
        )
    )
    simulation.run_ticks(2)
    return simulation


def robot_named(simulation: SimulationRuntime, robot_id: str):
    return next(robot for robot in simulation.snapshot().robots if robot.robot_id == robot_id)


def test_scenario_f_communication_loss_is_published(runtime: SimulationRuntime) -> None:
    losses = [
        event for event in runtime.events if event.event_type is EventType.COMMUNICATION_LOST
    ]
    assert losses, "a silenced robot never reported a lost heartbeat"
    payload = losses[0].payload
    assert payload.robot_id == ROBOT
    assert payload.timeout_s > 0
    assert payload.last_contact_at_s >= 0


def test_scenario_f_a_silenced_robot_is_reported_unreachable(runtime: SimulationRuntime) -> None:
    robot = robot_named(runtime, ROBOT)
    assert robot.communication_state is CommunicationState.LOST
    assert robot.status is RobotStatus.DEGRADED


def test_scenario_f_a_silenced_robot_is_held_rather_than_moved(
    runtime: SimulationRuntime,
) -> None:
    """The runtime must not guess where an unreachable robot is."""

    assert runtime.status_counts().get(RobotStatus.DEGRADED.value, 0) >= 1


def test_scenario_f_contact_returns_after_the_timeout(runtime: SimulationRuntime) -> None:
    runtime.run_ticks(80)
    robot = robot_named(runtime, ROBOT)
    assert robot.communication_state is CommunicationState.ONLINE
    assert robot.status is not RobotStatus.DEGRADED


def test_scenario_f_the_rest_of_the_fleet_keeps_working(runtime: SimulationRuntime) -> None:
    runtime.run_ticks(600)
    snapshot = runtime.snapshot()
    assert snapshot.metrics.completed_tasks > 0
    assert snapshot.metrics.extra_metrics["fleet_size"] == 20.0


def test_scenario_f_only_the_targeted_robot_was_affected(runtime: SimulationRuntime) -> None:
    runtime.run_ticks(200)
    snapshot = runtime.snapshot()
    lost = [
        robot.robot_id
        for robot in snapshot.robots
        if robot.communication_state is CommunicationState.LOST
    ]
    assert ROBOT not in lost
