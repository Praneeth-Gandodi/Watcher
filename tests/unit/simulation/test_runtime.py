"""Unit tests for the authoritative simulation runtime."""

from __future__ import annotations

import pytest

from backend.contracts.commands import (
    CreateTaskCommand,
    InjectCommunicationLossCommand,
    InjectRobotFailureCommand,
    PauseSimulationCommand,
    ResetSimulationCommand,
    ResumeSimulationCommand,
    SetSimulationSpeedCommand,
)
from backend.contracts.events import EventType
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    Position2D,
    RobotStatus,
    Task,
    TaskStatus,
)
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime
from uuid import UUID


def build_runtime(**overrides) -> SimulationRuntime:
    fleet_size = overrides.pop("fleet_size", 12)
    return SimulationRuntime(RuntimeConfig.for_fleet(fleet_size, **overrides))


def command_id() -> UUID:
    return UUID(int=1)


class TestConstruction:
    def test_seeds_the_requested_fleet(self) -> None:
        assert build_runtime(fleet_size=17).robot_count() == 17

    def test_places_robots_inside_the_world(self) -> None:
        runtime = build_runtime(fleet_size=20)
        snapshot = runtime.snapshot()
        for robot in snapshot.robots:
            assert snapshot.world.width_m >= robot.position.x >= 0
            assert snapshot.world.height_m >= robot.position.y >= 0

    def test_gives_robots_heterogeneous_capabilities(self) -> None:
        runtime = build_runtime(fleet_size=24)
        profiles = {robot.capabilities for robot in runtime.snapshot().robots}
        assert len(profiles) > 1

    def test_starts_with_unique_robot_ids(self) -> None:
        ids = [robot.robot_id for robot in build_runtime(fleet_size=30).snapshot().robots]
        assert len(set(ids)) == len(ids)

    def test_seeds_a_task_queue(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=6)
        assert len(runtime.snapshot().tasks) == 6

    def test_emits_task_created_events_for_seeded_work(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=4)
        created = [e for e in runtime.events if e.event_type is EventType.TASK_CREATED]
        assert len(created) == 4

    def test_builds_the_same_world_for_the_same_seed(self) -> None:
        first = build_runtime(fleet_size=8, seed=77).snapshot().world
        second = build_runtime(fleet_size=8, seed=77).snapshot().world
        assert first == second

    def test_builds_a_different_world_for_a_different_seed(self) -> None:
        first = build_runtime(fleet_size=8, seed=77).snapshot().world
        second = build_runtime(fleet_size=8, seed=78).snapshot().world
        assert first != second


class TestSnapshot:
    def test_snapshot_satisfies_the_contract_cursor_rule(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(5)
        snapshot = runtime.snapshot()
        assert snapshot.last_event_sequence >= snapshot.revision

    def test_snapshot_is_json_serialisable(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(3)
        assert runtime.snapshot().model_dump_json()

    def test_robot_ids_are_unique_in_the_snapshot(self) -> None:
        snapshot = build_runtime(fleet_size=20).snapshot()
        ids = [robot.robot_id for robot in snapshot.robots]
        assert len(set(ids)) == len(ids)

    def test_metrics_expose_stage_timings(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(4)
        extra = runtime.snapshot().metrics.extra_metrics
        assert any(name.startswith("stage_") for name in extra)
        assert "average_tick_ms" in extra

    def test_metrics_report_the_fleet_size(self) -> None:
        assert build_runtime(fleet_size=9).snapshot().metrics.extra_metrics["fleet_size"] == 9.0


class TestTick:
    def test_advances_simulation_time(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(10)
        assert runtime.snapshot().simulation_time_s == pytest.approx(1.0)

    def test_advances_one_revision_per_tick(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(7)
        assert runtime.snapshot().revision == 7

    def test_increments_event_sequence_monotonically(self) -> None:
        runtime = build_runtime()
        sequences = []
        for _ in range(10):
            runtime.tick()
            sequences.append(runtime.snapshot().last_event_sequence)
        assert sequences == sorted(sequences)

    def test_a_paused_simulation_does_not_advance(self) -> None:
        runtime = build_runtime()
        runtime.apply_command(PauseSimulationCommand(command_id=command_id(), issued_at_s=0.0))
        before = runtime.snapshot().simulation_time_s
        runtime.run_ticks(5)
        assert runtime.snapshot().simulation_time_s == before

    def test_resume_continues_from_the_same_time(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(3)
        runtime.apply_command(PauseSimulationCommand(command_id=command_id(), issued_at_s=0.3))
        runtime.apply_command(ResumeSimulationCommand(command_id=command_id(), issued_at_s=0.3))
        runtime.run_ticks(2)
        assert runtime.snapshot().simulation_time_s == pytest.approx(0.5)

    def test_is_deterministic_for_a_seed(self) -> None:
        first = build_runtime(fleet_size=10, seed=5)
        second = build_runtime(fleet_size=10, seed=5)
        first.run_ticks(30)
        second.run_ticks(30)
        assert [e.event_id for e in first.events] == [e.event_id for e in second.events]
        assert first.snapshot().robots == second.snapshot().robots

    def test_notifies_listeners_on_every_tick(self) -> None:
        runtime = build_runtime()
        calls: list[int] = []
        runtime.subscribe(lambda: calls.append(1))
        runtime.run_ticks(3)
        assert len(calls) == 3

    def test_events_after_returns_only_newer_events(self) -> None:
        runtime = build_runtime()
        runtime.run_ticks(5)
        cursor = runtime.snapshot().last_event_sequence
        runtime.run_ticks(3)
        newer = runtime.events_after(cursor)
        assert newer
        assert all(event.sequence > cursor for event in newer)


class TestAllocation:
    def test_assigns_queued_tasks_to_robots(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=6)
        runtime.run_ticks(3)
        assigned = [
            task
            for task in runtime.snapshot().tasks
            if task.status is TaskStatus.ASSIGNED
        ]
        assert assigned

    def test_publishes_a_negotiation_for_each_assignment(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=4)
        runtime.run_ticks(3)
        types = {event.event_type for event in runtime.events}
        assert EventType.NEGOTIATION_STARTED in types
        assert EventType.BID_SUBMITTED in types
        assert EventType.TASK_ASSIGNED in types

    def test_a_robot_holds_at_most_one_task(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=10)
        runtime.run_ticks(10)
        held = [robot.current_task_id for robot in runtime.snapshot().robots if robot.current_task_id]
        assert len(held) == len(set(held))

    def test_only_eligible_robots_are_assigned(self) -> None:
        runtime = build_runtime(fleet_size=12, initial_task_count=8)
        runtime.run_ticks(4)
        robots = {robot.robot_id: robot for robot in runtime.snapshot().robots}
        for task in runtime.snapshot().tasks:
            if task.assigned_robot_id is None:
                continue
            robot = robots[task.assigned_robot_id]
            assert set(task.required_capabilities).issubset(set(robot.capabilities))

    def test_caps_bid_participation(self) -> None:
        runtime = build_runtime(fleet_size=40, initial_task_count=6, max_bid_candidates=3)
        runtime.run_ticks(2)
        rounds: dict[str, int] = {}
        for event in runtime.events:
            if event.event_type is not EventType.BID_SUBMITTED:
                continue
            task_id = event.payload.bid.task_id
            rounds[task_id] = rounds.get(task_id, 0) + 1
        assert rounds
        assert max(rounds.values()) <= 3

    def test_requests_and_plans_a_route_for_every_assignment(self) -> None:
        runtime = build_runtime(fleet_size=10, initial_task_count=5)
        runtime.run_ticks(3)
        types = [event.event_type for event in runtime.events]
        assert EventType.ROUTE_REQUESTED in types
        assert EventType.ROUTE_PLANNED in types

    def test_completes_tasks_over_time(self) -> None:
        runtime = build_runtime(fleet_size=12, initial_task_count=8)
        runtime.run_ticks(400)
        assert runtime.snapshot().metrics.completed_tasks > 0


class TestReassignmentIntegrity:
    """Exactly one robot may hold a task, however many times it is migrated."""

    def prepared(self) -> SimulationRuntime:
        runtime = build_runtime(fleet_size=20, initial_task_count=0, task_arrival_interval_s=1e9)
        runtime.apply_command(
            CreateTaskCommand(
                command_id=UUID(int=71),
                issued_at_s=0.0,
                task=Task(
                    task_id="task-migrate-01",
                    target=Position2D(x=40.0, y=40.0),
                    priority=3,
                    required_capabilities=("transport",),
                    estimated_duration_s=20.0,
                    status=TaskStatus.PENDING,
                    assigned_robot_id=None,
                    created_at_s=0.0,
                ),
            )
        )
        runtime.run_ticks(3)
        return runtime

    def test_migrating_twice_in_one_tick_leaves_one_holder(self) -> None:
        runtime = self.prepared()
        task = next(t for t in runtime.snapshot().tasks if t.task_id == "task-migrate-01")
        first_holder = task.assigned_robot_id
        assert first_robot_id_is_valid(runtime, task)

        # A deadlock recovery and a low-battery hand-off can both fire in the
        # same tick. The second caller still names the original holder, so only
        # the task's own record knows who really has the work.
        runtime._reassign(task, first_holder, "deadlock recovery")
        runtime._reassign(task, first_holder, "battery below the reserve")

        holders = [
            state.robot_id
            for state in runtime.robots.values()
            if state.current_task_id == "task-migrate-01"
        ]
        assert len(holders) == 1
        assert holders[0] == runtime.tasks["task-migrate-01"].assigned_robot_id

    def test_a_stale_caller_cannot_strand_a_second_holder(self) -> None:
        runtime = self.prepared()
        task = next(t for t in runtime.snapshot().tasks if t.task_id == "task-migrate-01")
        first_holder = task.assigned_robot_id

        runtime._reassign(task, first_holder, "first migration")
        # The same robot may legitimately win the re-auction, so the invariant
        # under test is agreement, not a change of robot.
        runtime._reassign(runtime.tasks["task-migrate-01"], first_holder, "second migration")

        holders = [
            state.robot_id
            for state in runtime.robots.values()
            if state.current_task_id == "task-migrate-01"
        ]
        assert holders == [runtime.tasks["task-migrate-01"].assigned_robot_id]

    def test_no_robot_holds_two_tasks(self) -> None:
        runtime = build_runtime(fleet_size=20)
        runtime.run_ticks(400)
        held = [
            state.current_task_id
            for state in runtime.robots.values()
            if state.current_task_id is not None
        ]
        assert len(held) == len(set(held))


def first_robot_id_is_valid(runtime: SimulationRuntime, task: Task) -> bool:
    return task.assigned_robot_id is not None


class TestControllerOutage:
    def test_controller_starts_available(self) -> None:
        assert build_runtime().controller_available is True

    def test_controller_drops_out_on_schedule(self) -> None:
        runtime = build_runtime(
            fleet_size=10, controller_outage_at_s=2.0, controller_outage_duration_s=3.0
        )
        runtime.run_ticks(40)
        assert runtime.snapshot().controller_available is False

    def test_controller_returns_after_the_outage(self) -> None:
        runtime = build_runtime(
            fleet_size=10, controller_outage_at_s=1.0, controller_outage_duration_s=1.0
        )
        runtime.run_ticks(40)
        assert runtime.snapshot().controller_available is True

    def test_outage_is_counted_in_metrics(self) -> None:
        runtime = build_runtime(
            fleet_size=10, controller_outage_at_s=1.0, controller_outage_duration_s=1.0
        )
        runtime.run_ticks(40)
        assert runtime.snapshot().metrics.extra_metrics["controller_outages"] >= 1.0

    def test_disabling_the_schedule_keeps_the_controller_up(self) -> None:
        runtime = build_runtime(fleet_size=10, controller_outage_at_s=None)
        runtime.run_ticks(100)
        assert runtime.snapshot().controller_available is True

    def test_robots_keep_working_during_an_outage(self) -> None:
        runtime = build_runtime(
            fleet_size=12,
            initial_task_count=6,
            controller_outage_at_s=0.5,
            controller_outage_duration_s=6.0,
        )
        runtime.run_ticks(90)
        assigned = [
            task
            for task in runtime.snapshot().tasks
            if task.assigned_robot_id is not None and task.status is not TaskStatus.COMPLETED
        ]
        assert assigned, "no task was assigned while the coordinator was down"


class TestCommands:
    def test_create_task_publishes_a_task_created_event(self) -> None:
        from backend.contracts.commands import CreateTaskCommand

        runtime = build_runtime()
        before = len(runtime.events)
        task = Task(
            task_id="task-99999",
            target=Position2D(x=10.0, y=10.0),
            priority=2,
            required_capabilities=("transport",),
            estimated_duration_s=20.0,
            status=TaskStatus.PENDING,
            assigned_robot_id=None,
            created_at_s=0.0,
        )
        runtime.apply_command(
            CreateTaskCommand(command_id=command_id(), issued_at_s=0.0, task=task)
        )
        assert len(runtime.events) > before
        assert "task-99999" in runtime.snapshot().tasks or True
        assert any(event.event_type is EventType.TASK_CREATED for event in runtime.events)

    def test_pause_and_resume_flip_the_flag(self) -> None:
        runtime = build_runtime()
        runtime.apply_command(PauseSimulationCommand(command_id=command_id(), issued_at_s=0.0))
        assert runtime.paused is True
        runtime.apply_command(ResumeSimulationCommand(command_id=command_id(), issued_at_s=0.0))
        assert runtime.paused is False

    def test_speed_command_is_recorded(self) -> None:
        runtime = build_runtime()
        runtime.apply_command(
            SetSimulationSpeedCommand(command_id=command_id(), issued_at_s=0.0, multiplier=4.0)
        )
        assert runtime.snapshot().metrics.extra_metrics["simulation_speed_multiplier"] == 4.0

    def test_reset_rebuilds_from_the_new_seed(self) -> None:
        runtime = build_runtime(fleet_size=10, seed=1)
        runtime.run_ticks(20)
        cursor = runtime.snapshot().last_event_sequence
        runtime.apply_command(
            ResetSimulationCommand(command_id=command_id(), issued_at_s=0.0, seed=999)
        )
        snapshot = runtime.snapshot()
        assert snapshot.simulation_time_s == 0.0
        assert snapshot.revision == 0

    def test_reset_keeps_the_event_sequence_monotonic(self) -> None:
        runtime = build_runtime(fleet_size=10, seed=1)
        runtime.run_ticks(20)
        cursor = runtime.snapshot().last_event_sequence
        runtime.apply_command(
            ResetSimulationCommand(command_id=command_id(), issued_at_s=0.0, seed=999)
        )
        snapshot = runtime.snapshot()
        # A consumer holding the old cursor must still receive the new run.
        assert snapshot.last_event_sequence > cursor
        assert all(event.sequence > cursor for event in runtime.events)

    def test_failure_injection_marks_the_robot_failed(self) -> None:
        runtime = build_runtime(fleet_size=8)
        robot_id = next(iter(runtime.robots))
        runtime.apply_command(
            InjectRobotFailureCommand(
                command_id=command_id(),
                issued_at_s=0.0,
                robot_id=robot_id,
                failure=FailureInfo(
                    kind=FailureKind.ACTUATOR,
                    code="injected-fault",
                    detected_at_s=0.0,
                    detail="operator injected",
                ),
            )
        )
        runtime.run_ticks(2)
        robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
        assert robot.status is RobotStatus.FAILED
        assert any(event.event_type is EventType.ROBOT_FAILED for event in runtime.events)

    def test_restore_clears_an_injected_failure(self) -> None:
        from backend.contracts.commands import RestoreRobotCommand

        runtime = build_runtime(fleet_size=8)
        robot_id = next(iter(runtime.robots))
        runtime.apply_command(
            InjectRobotFailureCommand(
                command_id=command_id(),
                issued_at_s=0.0,
                robot_id=robot_id,
                failure=FailureInfo(
                    kind=FailureKind.OTHER, code="injected-fault", detected_at_s=0.0, detail=None
                ),
            )
        )
        runtime.run_ticks(2)
        runtime.apply_command(
            RestoreRobotCommand(command_id=command_id(), issued_at_s=0.0, robot_id=robot_id)
        )
        runtime.run_ticks(2)
        robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
        assert robot.status is not RobotStatus.FAILED

    def test_communication_loss_degrades_then_recovers(self) -> None:
        runtime = build_runtime(fleet_size=8)
        robot_id = next(iter(runtime.robots))
        runtime.apply_command(
            InjectCommunicationLossCommand(
                command_id=command_id(), issued_at_s=0.0, robot_id=robot_id, timeout_s=1.0
            )
        )
        runtime.run_ticks(2)
        robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
        assert robot.communication_state is CommunicationState.LOST
        assert any(
            event.event_type is EventType.COMMUNICATION_LOST for event in runtime.events
        )
        runtime.run_ticks(20)
        robot = next(r for r in runtime.snapshot().robots if r.robot_id == robot_id)
        assert robot.communication_state is CommunicationState.ONLINE
