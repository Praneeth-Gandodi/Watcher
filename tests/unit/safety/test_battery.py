"""Unit tests for battery accounting and charging decisions."""

from __future__ import annotations

import pytest

from backend.contracts.models import (
    GridCell,
    GridCellType,
    Position2D,
    RecoveryActionType,
    RobotStatus,
    Task,
    WorldState,
)
from backend.simulation.grid import Cell, WorldIndex
from safety.battery import BatteryManager

COLUMNS = 10
ROWS = 10
CELL_SIZE = 2.0
ORIGIN = Position2D(x=5.0, y=5.0)


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
        ),
        revision=1,
    )


def make_task(x: float = 5.0, y: float = 5.0) -> Task:
    return Task(
        task_id="task-001",
        target=Position2D(x=x, y=y),
        priority=3,
        required_capabilities=("transport",),
        estimated_duration_s=30.0,
        status="pending",
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def index() -> WorldIndex:
    return WorldIndex.from_world(build_world())


class TestDrain:
    def test_moving_drains_more_than_idling(self) -> None:
        manager = BatteryManager()
        idle = manager.drain(
            80.0, RobotStatus.IDLE, distance_travelled_m=0.0, elapsed_s=1.0, carrying=False
        )
        moving = manager.drain(
            80.0, RobotStatus.IDLE, distance_travelled_m=5.0, elapsed_s=1.0, carrying=False
        )
        assert moving < idle < 80.0

    def test_carrying_drains_faster(self) -> None:
        manager = BatteryManager()
        empty = manager.drain(
            80.0, RobotStatus.IDLE, distance_travelled_m=5.0, elapsed_s=1.0, carrying=False
        )
        loaded = manager.drain(
            80.0, RobotStatus.IDLE, distance_travelled_m=5.0, elapsed_s=1.0, carrying=True
        )
        assert loaded < empty

    def test_charging_gains_energy(self) -> None:
        charged = BatteryManager().drain(
            40.0, RobotStatus.CHARGING, distance_travelled_m=0.0, elapsed_s=10.0, carrying=False
        )
        assert charged == 54.0

    def test_charging_caps_at_one_hundred(self) -> None:
        charged = BatteryManager().drain(
            99.0, RobotStatus.CHARGING, distance_travelled_m=0.0, elapsed_s=10.0, carrying=False
        )
        assert charged == 100.0

    def test_a_failed_robot_does_not_drain(self) -> None:
        assert (
            BatteryManager().drain(
                30.0, RobotStatus.FAILED, distance_travelled_m=5.0, elapsed_s=1.0, carrying=False
            )
            == 30.0
        )

    def test_drain_never_goes_negative(self) -> None:
        drained = BatteryManager().drain(
            0.5, RobotStatus.IDLE, distance_travelled_m=1000.0, elapsed_s=100.0, carrying=True
        )
        assert drained == 0.0

    def test_a_single_tick_drain_is_below_wire_precision(self) -> None:
        """The regression this signature exists to prevent.

        One tick of idle draw is 0.002%, which is finer than the two decimal
        places the ``Robot`` contract keeps. Draining from a projected contract
        would therefore return the same value forever.
        """

        manager = BatteryManager()
        battery = 80.0
        for _ in range(100):
            battery = manager.drain(
                battery, RobotStatus.IDLE, distance_travelled_m=0.0, elapsed_s=0.1, carrying=False
            )
        assert battery < 79.81


class TestAssessment:
    def manager(self) -> BatteryManager:
        return BatteryManager(low_threshold_percent=20.0, critical_threshold_percent=8.0)

    def assess(self, manager: BatteryManager, battery: float, **kwargs):
        return manager.assess(
            "robot-001",
            battery,
            kwargs.pop("status", RobotStatus.ACTIVE),
            kwargs.pop("position", ORIGIN),
            kwargs.pop("task", make_task()),
            index=self.world_index,
            now_s=kwargs.pop("now_s", 0.0),
        )

    world_index = index()

    def test_a_healthy_robot_needs_no_decision(self) -> None:
        assert self.assess(self.manager(), 80.0) is None

    def test_a_low_battery_robot_is_told_to_charge(self) -> None:
        decision = self.assess(self.manager(), 12.0)
        assert decision is not None
        assert decision.should_return_to_charger is True
        assert decision.should_release_task is True
        assert decision.threshold_percent == 20.0

    def test_a_critical_battery_robot_uses_the_critical_threshold(self) -> None:
        assert self.assess(self.manager(), 5.0).threshold_percent == 8.0

    def test_a_robot_that_cannot_finish_still_reports(self) -> None:
        # 21% leaves roughly 11 m of range against a ~19.8 m task distance, so
        # the robot is above the reserve yet cannot complete the work.
        decision = self.assess(self.manager(), 21.0, task=make_task(x=19.0, y=19.0))
        assert decision is not None
        assert decision.should_return_to_charger is True
        assert decision.should_release_task is False

    def test_a_charging_robot_is_left_alone(self) -> None:
        assert self.assess(self.manager(), 5.0, status=RobotStatus.CHARGING) is None

    def test_a_failed_robot_is_left_alone(self) -> None:
        assert self.assess(self.manager(), 5.0, status=RobotStatus.FAILED) is None

    def test_estimated_range_shrinks_with_battery(self) -> None:
        manager = self.manager()
        assert manager.estimate_range_m(80.0) > manager.estimate_range_m(30.0)

    def test_range_is_zero_at_the_reserve(self) -> None:
        assert self.manager().estimate_range_m(20.0) == 0.0

    def test_announces_once_then_stays_quiet(self) -> None:
        manager = self.manager()
        assert self.assess(manager, 12.0) is not None
        assert self.assess(manager, 12.0, now_s=1.0) is None

    def test_announces_again_after_the_memory_window(self) -> None:
        manager = self.manager()
        self.assess(manager, 12.0)
        assert self.assess(manager, 12.0, now_s=120.0) is not None

    def test_reports_the_range_used_for_the_decision(self) -> None:
        manager = self.manager()
        decision = self.assess(manager, 21.0, task=make_task(x=19.0, y=19.0))
        assert decision.estimated_range_m == pytest.approx(
            manager.estimate_range_m(21.0), rel=1e-3
        )


class TestChargerSelection:
    def test_picks_the_nearest_charger(self) -> None:
        chosen = BatteryManager().pick_charger(Position2D(x=4.0, y=4.0), index())
        assert chosen == Cell(2, 2)

    def test_skips_a_reserved_charger(self) -> None:
        chosen = BatteryManager().pick_charger(
            Position2D(x=4.0, y=4.0), index(), reserved=frozenset({Cell(2, 2)})
        )
        assert chosen == Cell(7, 2)

    def test_falls_back_when_every_charger_is_reserved(self) -> None:
        chosen = BatteryManager().pick_charger(
            Position2D(x=4.0, y=4.0),
            index(),
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
        assert BatteryManager().pick_charger(ORIGIN, empty) is None

    def test_satisfied_only_above_the_charge_target(self) -> None:
        manager = BatteryManager(charge_target_percent=85.0)
        assert manager.is_satisfied(90.0) is True
        assert manager.is_satisfied(60.0) is False

    def test_builds_a_return_to_charger_action(self) -> None:
        action = BatteryManager().build_return_action(
            "robot-001", 9.0, (5.0, 5.0), now_s=1.0
        )
        assert action.action_type is RecoveryActionType.RETURN_TO_CHARGER
        assert action.target_robot_ids == ("robot-001",)
        assert "9.0%" in action.reason
