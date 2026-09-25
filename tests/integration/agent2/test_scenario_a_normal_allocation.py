"""Integration scenario A: normal allocation from command to completion.

Covers the documented flow ``task -> candidates -> bids -> negotiation ->
assignment -> route -> movement -> completion`` end to end, asserting both the
emitted canonical events and the resulting robot and task state.
"""

from __future__ import annotations

from uuid import UUID

from backend.contracts.commands import CreateTaskCommand
from backend.contracts.events import EventType
from backend.contracts.models import Position2D, Task, TaskStatus
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime


def build_runtime() -> SimulationRuntime:
    return SimulationRuntime(
        RuntimeConfig.for_fleet(
            10,
            initial_task_count=0,
            task_arrival_interval_s=1e9,
            controller_outage_at_s=None,
        )
    )


def make_task(task_id: str, target: tuple[float, float]) -> Task:
    return Task(
        task_id=task_id,
        target=Position2D(x=target[0], y=target[1]),
        priority=3,
        required_capabilities=("transport",),
        estimated_duration_s=20.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def test_scenario_a_normal_allocation() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=1),
            issued_at_s=0.0,
            task=make_task("task-a001", (40.0, 30.0)),
        )
    )
    runtime.run_ticks(3)

    types = [event.event_type for event in runtime.events]
    assert EventType.TASK_CREATED in types
    assert EventType.NEGOTIATION_STARTED in types
    assert EventType.BID_SUBMITTED in types
    assert EventType.TASK_ASSIGNED in types
    assert EventType.ROUTE_REQUESTED in types
    assert EventType.ROUTE_PLANNED in types

    task = runtime.snapshot().tasks[0]
    assert task.status is TaskStatus.ASSIGNED
    assert task.assigned_robot_id is not None

    assigned = next(
        robot for robot in runtime.snapshot().robots if robot.robot_id == task.assigned_robot_id
    )
    assert assigned.current_task_id == task.task_id
    assert assigned.status.value in {"active", "blocked"}


def test_scenario_a_assignment_goes_to_an_eligible_robot() -> None:
    runtime = build_runtime()
    task = make_task("task-a002", (60.0, 20.0))
    runtime.apply_command(
        CreateTaskCommand(command_id=UUID(int=2), issued_at_s=0.0, task=task)
    )
    runtime.run_ticks(3)

    assigned_id = runtime.snapshot().tasks[0].assigned_robot_id
    assert assigned_id is not None
    robots = {robot.robot_id: robot for robot in runtime.snapshot().robots}
    winner = robots[assigned_id]
    assert set(task.required_capabilities).issubset(set(winner.capabilities))


def test_scenario_a_task_completes_after_travel() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=3),
            issued_at_s=0.0,
            task=make_task("task-a003", (20.0, 10.0)),
        )
    )
    runtime.run_ticks(1500)

    assert runtime.snapshot().metrics.completed_tasks >= 1
    types = [event.event_type for event in runtime.events]
    assert EventType.TASK_COMPLETED in types
    completed = next(
        task for task in runtime.snapshot().tasks if task.status is TaskStatus.COMPLETED
    )
    assert completed.assigned_robot_id is not None


def test_scenario_a_robot_actually_travels_to_its_work() -> None:
    runtime = build_runtime()
    target = (20.0, 10.0)
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=6),
            issued_at_s=0.0,
            task=make_task("task-a006", target),
        )
    )
    runtime.run_ticks(3)
    robot_id = runtime.snapshot().tasks[0].assigned_robot_id
    start = next(
        robot.position for robot in runtime.snapshot().robots if robot.robot_id == robot_id
    )
    runtime.run_ticks(1500)
    finish = next(
        robot.position for robot in runtime.snapshot().robots if robot.robot_id == robot_id
    )

    def distance_to_target(position) -> float:
        return ((position.x - target[0]) ** 2 + (position.y - target[1]) ** 2) ** 0.5

    # The robot crossed the floor rather than standing still.
    travelled = ((finish.x - start.x) ** 2 + (finish.y - start.y) ** 2) ** 0.5
    assert travelled > 10.0
    # It ended up at the requested work rather than where it started. The exact
    # cell can differ from the requested target when the target falls inside an
    # obstacle; the planner delivers to the closest reachable cell instead.
    assert distance_to_target(finish) < distance_to_target(start)
    assert not runtime.index.is_blocked(runtime.index.cell_of(finish))


def test_scenario_a_plans_a_route_with_at_least_two_waypoints() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=4),
            issued_at_s=0.0,
            task=make_task("task-a004", (150.0, 90.0)),
        )
    )
    runtime.run_ticks(3)
    routes = runtime.snapshot().routes
    assert routes
    assert all(len(route.waypoints) >= 2 for route in routes)
    assert all(route.version >= 1 for route in routes)


def test_scenario_a_every_event_carries_a_correlation_id() -> None:
    runtime = build_runtime()
    runtime.apply_command(
        CreateTaskCommand(
            command_id=UUID(int=5),
            issued_at_s=0.0,
            task=make_task("task-a005", (30.0, 30.0)),
        )
    )
    runtime.run_ticks(5)
    assert all(event.correlation_id for event in runtime.events)
    assert all(event.schema_version == 1 for event in runtime.events)
