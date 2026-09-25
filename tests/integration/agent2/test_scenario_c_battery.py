"""Integration scenario C: low battery triggers a charging decision.

Flow: energy margin falls -> ``BATTERY_LOW`` -> the task migrates -> the robot
returns to a pad, recharges, and rejoins the fleet.

These assertions observe *transitions* rather than end state. A robot that
charges and comes back is the success case, so a test that only looked at the
final snapshot would miss the behaviour it claims to check.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from backend.contracts.commands import CreateTaskCommand
from backend.contracts.events import EventType
from backend.contracts.models import (
    GridCellType,
    Position2D,
    RobotCapability,
    RobotStatus,
    Task,
    TaskStatus,
)
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime
from backend.simulation.state import FleetProfile

TARGET = (60.0, 60.0)
CHARGE_TASK = "task-c001"


def build_runtime(fleet_size: int = 12) -> SimulationRuntime:
    """A fleet seeded just above the 20% reserve, so the decision triggers fast.

    At 100% a robot would need a quarter of an hour of simulated idling to reach
    its reserve, which would make the scenario test the clock rather than the
    behaviour. At 21% the range left is about 11 m, so a robot that is handed a
    longer job immediately cannot finish it and must hand the work on.
    """

    return SimulationRuntime(
        RuntimeConfig.for_fleet(
            fleet_size,
            initial_task_count=0,
            task_arrival_interval_s=1e9,
            controller_outage_at_s=None,
            fleet=FleetProfile(
                min_battery_percent=21.0,
                max_battery_percent=23.0,
                capability_pool=(RobotCapability.TRANSPORT,),
                min_capabilities=1,
                max_capabilities=1,
            ),
        )
    )


def make_task(task_id: str, target: tuple[float, float]) -> Task:
    return Task(
        task_id=task_id,
        target=Position2D(x=target[0], y=target[1]),
        priority=3,
        required_capabilities=("transport",),
        estimated_duration_s=30.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def run_until(
    runtime: SimulationRuntime, predicate: Callable[[], bool], *, max_ticks: int = 6000
) -> bool:
    """Tick in small steps until ``predicate`` holds, so transitions are seen."""

    for _ in range(max_ticks // 10):
        if predicate():
            return True
        runtime.run_ticks(10)
    return predicate()


def test_scenario_c_low_battery_is_announced_before_the_robot_is_stranded() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=1),
            issued_at_s=0.0,
            task=make_task(CHARGE_TASK, TARGET),
        )
    )

    def announced() -> bool:
        return any(
            event.event_type is EventType.BATTERY_LOW for event in runtime.events
        )

    assert run_until(runtime, announced)
    low = next(event for event in runtime.events if event.event_type is EventType.BATTERY_LOW)
    assert low.payload.battery_percent <= low.payload.threshold_percent
    assert low.payload.estimated_range_m >= 0
    assert low.correlation_id


def test_scenario_c_a_low_battery_robot_goes_to_charge() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=2),
            issued_at_s=0.0,
            task=make_task(CHARGE_TASK, TARGET),
        )
    )

    def charging() -> bool:
        return runtime.status_counts().get(RobotStatus.CHARGING.value, 0) > 0

    assert run_until(runtime, charging)
    assert runtime.snapshot().metrics.extra_metrics["returns_to_charger"] > 0


def test_scenario_c_returning_to_charge_is_a_documented_recovery_action() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=3),
            issued_at_s=0.0,
            task=make_task(CHARGE_TASK, TARGET),
        )
    )

    def acted() -> bool:
        return any(
            event.event_type is EventType.RECOVERY_STARTED
            and event.payload.action.action_type.value == "return_to_charger"
            for event in runtime.events
        )

    assert run_until(runtime, acted)
    action = next(
        event.payload.action
        for event in runtime.events
        if event.event_type is EventType.RECOVERY_STARTED
        and event.payload.action.action_type.value == "return_to_charger"
    )
    assert action.target_robot_ids
    assert "battery" in action.reason


def test_scenario_c_a_charging_robot_gains_energy() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=4),
            issued_at_s=0.0,
            task=make_task(CHARGE_TASK, TARGET),
        )
    )

    def charging() -> bool:
        return runtime.status_counts().get(RobotStatus.CHARGING.value, 0) > 0

    assert run_until(runtime, charging)
    charging_states = [
        state for state in runtime.robots.values() if state.status is RobotStatus.CHARGING
    ]
    before = {state.robot_id: state.battery_percent for state in charging_states}
    runtime.run_ticks(100)
    for state in runtime.robots.values():
        if state.robot_id in before:
            assert state.battery_percent >= before[state.robot_id]


def test_scenario_c_a_charged_robot_rejoins_the_fleet() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=5),
            issued_at_s=0.0,
            task=make_task(CHARGE_TASK, TARGET),
        )
    )

    def someone_charging() -> bool:
        return runtime.status_counts().get(RobotStatus.CHARGING.value, 0) > 0

    assert run_until(runtime, someone_charging)
    charging_id = next(
        state.robot_id
        for state in runtime.robots.values()
        if state.status is RobotStatus.CHARGING
    )
    battery_when_it_left = runtime.robots[charging_id].battery_percent

    def recharged() -> bool:
        return runtime.robots[charging_id].status is not RobotStatus.CHARGING

    assert run_until(runtime, recharged)
    released = runtime.robots[charging_id]
    # Released back into the working fleet rather than parked on the pad, and
    # only after a substantial top-up. The exact 85% target is asserted in the
    # battery unit tests: here the release is observed a few idle ticks after
    # the threshold was crossed, so the value has already ticked down slightly.
    assert released.battery_percent > battery_when_it_left + 50.0
    assert released.status in {RobotStatus.IDLE, RobotStatus.ACTIVE}


def test_scenario_c_battery_aware_scheduling_under_a_continuous_queue() -> None:
    """Work keeps flowing while robots rotate through the chargers."""

    runtime = SimulationRuntime(
        RuntimeConfig.for_fleet(
            16,
            initial_task_count=4,
            task_arrival_interval_s=1.0,
            controller_outage_at_s=None,
            fleet=FleetProfile(
                min_battery_percent=21.0,
                max_battery_percent=26.0,
                capability_pool=(RobotCapability.TRANSPORT,),
                min_capabilities=1,
                max_capabilities=1,
            ),
        )
    )
    runtime.run_ticks(8000)
    metrics = runtime.snapshot().metrics
    extra = metrics.extra_metrics

    assert metrics.completed_tasks > 0, "no task finished while robots were charging"
    assert extra["returns_to_charger"] > 0, "no robot ever returned to a charger"
    assert metrics.task_reassignments > 0, "low battery never migrated a task"
    assert any(
        event.event_type is EventType.TASK_REASSIGNED for event in runtime.events
    )


def test_scenario_c_charger_cells_exist_in_the_generated_world() -> None:
    assert build_runtime().index.stations(GridCellType.CHARGING)
