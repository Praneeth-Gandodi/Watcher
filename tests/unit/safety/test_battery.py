"""Unit tests for battery accounting and charging decisions."""

from __future__ import annotations

import pytest

from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    GridCell,
    GridCellType,
    Position2D,
    RecoveryActionType,
    Robot,
    RobotStatus,
    Task,
    WorldState,
)
from backend.simulation.grid import Cell, WorldIndex
from safety.battery import BatteryManager

COLUMNS = 10
ROWS = 10
CELL_SIZE = 2.0


def build_world() -> WorldState:
    return WorldState(
        width_m=20.0,
        height_m=20.0,
        cell_size_m=CELL_SIZE,
        columns=COLUMNS,
        rows=ROWS,
        cells=(
            GridCell(cell_x=2, cell_y=2, cell_type=GridCellType.CHARGING),
            GridCell(cell_x=7, cell_y=2, cell_type=GridCellType.CHARGING),
            GridCell(cell_x=5, cell_y=7, cell_type=GridCellType.WORKSTATION),
        ),
        revision=1,
    )


def make_robot(
    robot_id: str = "robot-001",
    *,
    battery: float = 80.0,
    x: float = 5.0,
    y: float = 5.0,
    status: RobotStatus = RobotStatus.ACTIVE,
) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=Position2D(x=x, y=y),
        battery_percent=battery,
        capabilities=("transport",),
        workload=0,
        status=status,
        current_task_id=None,
        communication_state=CommunicationState.ONLINE,
        last_updated_at_s=0.0,
    )


def make_task(x: float = 5.0, y: float = 5.0, priority: int = 3) -> Task:
    return Task(
        task_id="task-001",
        target=Position2D(x=x, y=y),
        priority=priority,
        required_capabilities=("transport",),
        estimated_duration_s=30.0,
        status="pending",
        assigned_robot_id=None,
        created_at_s=0.0,
    )


class TestDrain:
    def test_moving_drains_more_than_idling(self) -> None:
        manager = BatteryManager()
        idle = manager.drain(make_robot(), distance_travelled_m=0.0, elapsed_s=1.0, carrying=False)
        moving = manager.drain(make_robot(), distance_travelled_m=5.0, elapsed_s=1.0, carrying=False)
        assert moving < idle < 80.0

    def test_carrying_drains_faster(self) -> None:
        manager = BatteryManager()
        empty = manager.drain(make_robot(), distance_travelled_m=5.0, elapsed_s=1.0, carrying=False)
        loaded = manager.drain(make_robot(), distance_travelled_m=5.0, elapsed_s=1.0, carrying=True)
        assert loaded < empty

    def test_charging_gains_energy(self) -> None:
        manager = BatteryManager()
        charged = manager.drain(
            make_robot(battery=40.0, status=RobotStatus.CHARGING),
            distance_travelled_m=0.0,
            elapsed_s=10.0,
            carrying=False,
        )
        assert charged == 54.0

    def test_charging_caps_at_one_hundred(self) -> None:
        manager = BatteryManager()
        charged = manager.drain(
            make_robot(battery=99.0, status=RobotStatus.CHARGING),
            distance_travelled_m=0.0,
            elapsed_s=10.0,
            carrying=False,
        )
        assert charged == 100.0

    def test_a_failed_robot_does_not_drain(self) -> None:
        manager = BatteryManager()
        failed = make_robot(battery=30.0).model_copy(
            update={
                "status": RobotStatus.FAILED,
                "failure": FailureInfo(
                    kind=FailureKind.ACTUATOR,
                    code="actuator-fault",
                    detected_at_s=0.0,
                    detail=None,
                ),
            }
        )
        assert manager.drain(failed, distance_travelled_m=5.0, elapsed_s=1.0, carrying=False) == 30.0

    def test_drain_never_goes_negative(self) -> None:
        manager = BatteryManager()
        drained = manager.drain(
            make_robot(battery=0.5), distance_travelled_m=1000.0, elapsed_s=100.0, carrying=True
        )
        assert drained == 0.0

    def test_zero_speed_costs_nothing_beyond_idle_draw(self) -> None:
        manager = BatteryManager(idle_drain_per_s=0.0)
        assert manager.drain(make_robot(), distance_travelled_m=0.0, elapsed_s=5.0, carrying=False) == 80.0


class TestAssessment:
    def manager(self) -> BatteryManager:
        return BatteryManager(low_threshold_percent=20.0, critical_threshold_percent=8.0)

    def index(self) -> WorldIndex:
        return WorldIndex.from_world(build_world())

    def test_a_healthy_robot_needs_no_decision(self) -> None:
        decision = self.manager().assess(
            make_robot(battery=80.0), make_task(), index=self.index(), now_s=0.0
        )
        assert decision is None

    def test_a_low_battery_robot_is_told_to_charge(self) -> None:
        decision = self.manager().assess(
            make_robot(battery=12.0), make_task(), index=self.index(), now_s=0.0
        )
        assert decision is not None
        assert decision.should_return_to_charger is True
        assert decision.should_release_task is True
        assert decision.threshold_percent == 20.0

    def test_a_critical_battery_robot_uses_the_critical_threshold(self) -> None:
        decision = self.manager().assess(
            make_robot(battery=5.0), make_task(), index=self.index(), now_s=0.0
        )
        assert decision.threshold_percent == 8.0

    def test_a_robot_that_cannot_finish_still_reports(self) -> None:
        # 21% leaves roughly 11 m of range against a ~19.8 m task distance, so
        # the robot is above the reserve threshold yet cannot complete the work.
        far_task = make_task(x=19.0, y=19.0)
        decision = self.manager().assess(
            make_robot(battery=21.0), far_task, index=self.index(), now_s=0.0
        )
        assert decision is not None
        assert decision.should_return_to_charger is True
        assert decision.should_release_task is False

    def test_a_charging_robot_is_left_alone(self) -> None:
        decision = self.manager().assess(
            make_robot(battery=5.0, status=RobotStatus.CHARGING),
            make_task(),
            index=self.index(),
            now_s=0.0,
        )
        assert decision is None

    def test_a_failed_robot_is_left_alone(self) -> None:
        failed = make_robot(battery=5.0).model_copy(
            update={
                "status": RobotStatus.FAILED,
                "failure": FailureInfo(
                    kind=FailureKind.OTHER, code="injected", detected_at_s=0.0, detail=None
                ),
            }
        )
        assert self.manager().assess(failed, make_task(), index=self.index(), now_s=0.0) is None

    def test_estimated_range_shrinks_with_battery(self) -> None:
        manager = self.manager()
        assert manager.estimate_range_m(80.0) > manager.estimate_range_m(30.0)

    def test_range_is_zero_at_the_reserve(self) -> None:
        assert self.manager().estimate_range_m(20.0) == 0.0

    def test_announces_once_then_stays_quiet(self) -> None:
        manager = self.manager()
        robot = make_robot(battery=12.0)
        assert manager.assess(robot, make_task(), index=self.index(), now_s=0.0) is not None
        assert manager.assess(robot, make_task(), index=self.index(), now_s=1.0) is None

    def test_announces_again_after_the_memory_window(self) -> None:
        manager = BatteryManager(low_threshold_percent=20.0)
        robot = make_robot(battery=12.0)
        manager.assess(robot, make_task(), index=self.index(), now_s=0.0)
        later = manager.assess(robot, make_task(), index=self.index(), now_s=120.0)
        assert later is not None

    def test_reports_the_range_used_for_the_decision(self) -> None:
        decision = self.manager().assess(
            make_robot(battery=21.0), make_task(x=19.0, y=19.0), index=self.index(), now_s=0.0
        )
        assert decision.estimated_range_m == pytest.approx(
            self.manager().estimate_range_m(21.0), rel=1e-3
        )


class TestChargerSelection:
    def test_picks_the_nearest_charger(self) -> None:
        index = WorldIndex.from_world(build_world())
        chosen = BatteryManager().pick_charger(make_robot(x=4.0, y=4.0), index)
        assert chosen == Cell(2, 2)

    def test_skips_a_reserved_charger(self) -> None:
        index = WorldIndex.from_world(build_world())
        chosen = BatteryManager().pick_charger(
            make_robot(x=4.0, y=4.0), index, reserved=frozenset({Cell(2, 2)})
        )
        assert chosen == Cell(7, 2)

    def test_falls_back_when_every_charger_is_reserved(self) -> None:
        index = WorldIndex.from_world(build_world())
        chosen = BatteryManager().pick_charger(
            make_robot(x=4.0, y=4.0),
            index,
            reserved=frozenset({Cell(2, 2), Cell(7, 2)}),
        )
        assert chosen is not None

    def test_returns_none_without_any_charger(self) -> None:
        empty = WorldIndex.from_world(
            WorldState(
                width_m=20.0,
                height_m=20.0,
                cell_size_m=CELL_SIZE,
                columns=COLUMNS,
                rows=ROWS,
                cells=(),
                revision=1,
            )
        )
        assert BatteryManager().pick_charger(make_robot(), empty) is None

    def test_satisfied_only_above_the_charge_target(self) -> None:
        manager = BatteryManager(charge_target_percent=85.0)
        assert manager.is_satisfied(make_robot(battery=90.0)) is True
        assert manager.is_satisfied(make_robot(battery=60.0)) is False

    def test_builds_a_return_to_charger_action(self) -> None:
        action = BatteryManager().build_return_action(
            make_robot(battery=9.0), (5.0, 5.0), now_s=1.0
        )
        assert action.action_type is RecoveryActionType.RETURN_TO_CHARGER
        assert action.target_robot_ids == ("robot-001",)
        assert "9.0%" in action.reason
