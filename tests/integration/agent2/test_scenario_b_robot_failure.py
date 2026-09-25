"""Integration scenario B: a failed robot hands its task to another robot.

Flow: failure -> available task -> reassignment, asserting both the emitted
canonical events and the resulting robot and task state.
"""

from __future__ import annotations

from uuid import UUID

from backend.contracts.commands import CreateTaskCommand, InjectRobotFailureCommand
from backend.contracts.events import EventType
from backend.contracts.models import (
    FailureInfo,
    FailureKind,
    Position2D,
    RobotStatus,
    Task,
    TaskStatus,
)
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime


def build_runtime(fleet_size: int = 16) -> SimulationRuntime:
    return SimulationRuntime(
        RuntimeConfig.for_fleet(
            fleet_size,
            initial_task_count=0,
            task_arrival_interval_s=1e9,
            controller_outage_at_s=None,
        )
    )


def make_task(task_id: str, target: tuple[float, float]) -> Task:
    return Task(
        task_id=task_id,
        target=Position2D(x=target[0], y=target[1]),
        priority=4,
        required_capabilities=("transport",),
        estimated_duration_s=30.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def submit(runtime: SimulationRuntime, task: Task, identifier: int) -> None:
    runtime.apply_command(
        CreateTaskCommand(command_id=UUID(int=identifier), issued_at_s=0.0, task=task)
    )
    runtime.run_ticks(3)


def inject_failure(runtime: SimulationRuntime, robot_id: str, identifier: int) -> None:
    runtime.apply_command(
        InjectRobotFailureCommand(
            command_id=UUID(int=identifier),
            issued_at_s=runtime.simulation_time_s,
            robot_id=robot_id,
            failure=FailureInfo(
                kind=FailureKind.ACTUATOR,
                code="scenario-b-fault",
                detected_at_s=runtime.simulation_time_s,
                detail="injected by scenario B",
            ),
        )
    )
    runtime.run_ticks(3)


def test_scenario_b_failure_is_published_and_the_robot_stops() -> None:
    runtime = build_runtime()
    submit(runtime, make_task("task-b001", (40.0, 40.0)), 1)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert robot_id is not None

    inject_failure(runtime, robot_id, 2)

    robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
    assert robot.status is RobotStatus.FAILED
    assert robot.failure is not None
    assert any(event.event_type is EventType.ROBOT_FAILED for event in runtime.events)


def test_scenario_b_failed_robot_releases_its_task() -> None:
    runtime = build_runtime()
    submit(runtime, make_task("task-b002", (40.0, 40.0)), 3)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert robot_id is not None

    inject_failure(runtime, robot_id, 4)

    failed = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
    assert failed.current_task_id is None
    task = next(t for t in runtime.snapshot().tasks if t.task_id == "task-b002")
    assert task.status is not TaskStatus.ASSIGNED or task.assigned_robot_id != robot_id


def test_scenario_b_task_is_reassigned_to_another_robot() -> None:
    runtime = build_runtime(fleet_size=20)
    submit(runtime, make_task("task-b003", (40.0, 40.0)), 5)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert robot_id is not None

    inject_failure(runtime, robot_id, 6)
    runtime.run_ticks(5)

    assert any(event.event_type is EventType.TASK_REASSIGNED for event in runtime.events)
    task = next(t for t in runtime.snapshot().tasks if t.task_id == "task-b003")
    if task.assigned_robot_id is not None:
        assert task.assigned_robot_id != robot_id


def test_scenario_b_reassignment_names_both_robots_and_a_reason() -> None:
    runtime = build_runtime(fleet_size=20)
    submit(runtime, make_task("task-b004", (60.0, 30.0)), 7)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert robot_id is not None

    inject_failure(runtime, robot_id, 8)
    runtime.run_ticks(5)

    reassignments = [
        event
        for event in runtime.events
        if event.event_type is EventType.TASK_REASSIGNED
    ]
    assert reassignments
    payload = reassignments[-1].payload
    assert payload.previous_robot_id == robot_id
    assert payload.new_robot_id
    assert payload.reason


def test_scenario_b_a_restored_robot_rejoins_the_fleet() -> None:
    from backend.contracts.commands import RestoreRobotCommand

    runtime = build_runtime()
    submit(runtime, make_task("task-b005", (40.0, 40.0)), 9)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert robot_id is not None

    inject_failure(runtime, robot_id, 10)
    runtime.apply_command(
        RestoreRobotCommand(
            command_id=UUID(int=11),
            issued_at_s=runtime.simulation_time_s,
            robot_id=robot_id,
        )
    )
    runtime.run_ticks(3)

    robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
    assert robot.status is not RobotStatus.FAILED
    assert robot.failure is None
