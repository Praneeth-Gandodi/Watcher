"""Test plan I: battery consumption, thresholds, and route reserve.

Covers:
* movement lowers battery
* the low threshold triggers
* the critical threshold triggers
* insufficient battery for the remaining route is detected
* the consumption rate is configurable per profile
* the canonical ``Robot.battery_percent`` field stays the only battery state
"""

from __future__ import annotations

import pytest
from backend.contracts.models import CommunicationState, RobotStatus
from backend.safety.battery import BatteryManager, BatteryPolicy
from backend.safety.failure import evolve_robot
from backend.safety.robot_profile import RobotProfile
from backend.simulation.world import make_robot


def robot_at(battery_percent: float) -> object:
    return make_robot(
        "robot-001",
        (0, 0),
        battery_percent=battery_percent,
        capabilities=(),
    )


PROFILE = RobotProfile("robot-001", 1, 1, 1.0)


# ----------------------------------------------------------------------
# movement lowers battery
# ----------------------------------------------------------------------


def test_movement_lowers_battery_by_the_configured_rate() -> None:
    manager = BatteryManager()

    consumed = manager.consumption_for(7.0, PROFILE)

    assert consumed == 7.0
    updated = manager.apply_consumption(robot_at(50.0), consumed, observed_at_s=3.0)
    assert updated.battery_percent == 43.0
    assert updated.last_updated_at_s == 3.0


def test_the_consumption_rate_is_configurable_per_profile() -> None:
    fast_drain = RobotProfile("robot-001", 1, 1, 1.0, battery_percent_per_cell=2.5)
    no_drain = RobotProfile("robot-002", 1, 1, 1.0, battery_percent_per_cell=0.0)
    manager = BatteryManager()
    robot = robot_at(50.0)

    assert manager.consumption_for(4.0, fast_drain) == 10.0
    assert manager.consumption_for(4.0, no_drain) == 0.0
    # A zero rate costs nothing, so the same instance is returned unchanged.
    assert manager.apply_consumption(robot, 0.0, 0.0) is robot
    assert manager.apply_consumption(robot, 10.0, 0.0).battery_percent == 40.0


def test_battery_never_goes_below_zero_or_above_one_hundred() -> None:
    manager = BatteryManager()

    assert manager.apply_consumption(robot_at(3.0), 10.0, 0.0).battery_percent == 0.0
    assert manager.apply_consumption(robot_at(100.0), 10.0, 0.0).battery_percent == 90.0
    assert manager.apply_consumption(robot_at(3.0), 0.0, 0.0).battery_percent == 3.0


def test_the_canonical_robot_field_is_the_only_battery_state() -> None:
    manager = BatteryManager()

    updated = manager.apply_consumption(robot_at(50.0), 5.0, 1.0)

    assert updated.model_dump()["battery_percent"] == 45.0
    assert set(updated.model_dump()) == set(robot_at(50.0).model_dump())


def test_negative_consumption_or_distance_is_rejected() -> None:
    manager = BatteryManager()

    with pytest.raises(ValueError):
        manager.consumption_for(-1.0, PROFILE)
    with pytest.raises(ValueError):
        manager.apply_consumption(robot_at(50.0), -1.0, 0.0)


# ----------------------------------------------------------------------
# thresholds
# ----------------------------------------------------------------------


def test_the_low_threshold_triggers() -> None:
    manager = BatteryManager()

    assert manager.is_low(21.0) is False
    assert manager.is_low(20.0) is True, "the threshold is inclusive"
    assert manager.is_low(5.0) is True


def test_the_critical_threshold_triggers() -> None:
    manager = BatteryManager()

    assert manager.is_critical(11.0) is False
    assert manager.is_critical(10.0) is True
    assert manager.is_critical(0.0) is True
    assert manager.is_critical(15.0) is False


def test_thresholds_are_configurable_and_validated() -> None:
    policy = BatteryPolicy(
        low_threshold_percent=40.0, critical_threshold_percent=35.0, reserve_percent=5.0
    )
    manager = BatteryManager(policy)

    assert manager.low_threshold_percent == 40.0
    assert manager.is_low(41.0) is False
    assert manager.is_low(39.0) is True
    assert manager.is_critical(35.0) is True
    assert manager.reserve_percent == 5.0

    with pytest.raises(ValueError, match="critical_threshold_percent"):
        BatteryPolicy(low_threshold_percent=10.0, critical_threshold_percent=20.0)
    with pytest.raises(ValueError):
        BatteryPolicy(low_threshold_percent=-1.0)


def test_observation_reports_thresholds_and_the_triggered_level() -> None:
    manager = BatteryManager()

    healthy = manager.observe(robot_at(80.0), PROFILE, remaining_cells=10)
    low = manager.observe(robot_at(19.0), PROFILE, remaining_cells=10)
    critical = manager.observe(robot_at(4.0), PROFILE, remaining_cells=10)

    assert (healthy.is_low, healthy.is_critical) == (False, False)
    assert (low.is_low, low.is_critical) == (True, False)
    assert low.threshold_percent == 20.0
    assert (critical.is_low, critical.is_critical) == (True, True)
    assert critical.threshold_percent == 10.0


# ----------------------------------------------------------------------
# route reserve
# ----------------------------------------------------------------------


def test_insufficient_battery_for_the_remaining_route_is_detected() -> None:
    manager = BatteryManager()

    enough = manager.observe(robot_at(60.0), PROFILE, remaining_cells=20)
    short = manager.observe(robot_at(18.0), PROFILE, remaining_cells=20)

    # 20 cells at 1%/cell plus the 10% reserve needs 30%.
    assert enough.required_percent == 30.0
    assert enough.has_route_reserve is True
    assert short.has_route_reserve is False
    assert short.is_low is True


def test_the_reserve_is_included_in_the_requirement() -> None:
    manager = BatteryManager(BatteryPolicy(reserve_percent=25.0))

    observation = manager.observe(robot_at(50.0), PROFILE, remaining_cells=20)

    assert observation.required_percent == 45.0
    assert observation.has_route_reserve is True


def test_a_robot_with_no_remaining_route_needs_only_the_reserve() -> None:
    manager = BatteryManager()

    observation = manager.observe(robot_at(15.0), PROFILE, remaining_cells=0)

    assert observation.required_percent == 10.0
    assert observation.has_route_reserve is True


def test_estimated_range_follows_the_consumption_rate_and_cell_size() -> None:
    manager = BatteryManager()
    slow_drain = RobotProfile("robot-001", 1, 1, 1.0, battery_percent_per_cell=0.5)

    assert manager.estimated_range_cells(50.0, PROFILE) == 50.0
    assert manager.estimated_range_m(50.0, PROFILE, 2.0) == 100.0
    assert manager.estimated_range_cells(50.0, slow_drain) == 100.0
    assert manager.estimated_range_m(50.0, slow_drain, 2.0) == 200.0
    assert manager.estimated_range_cells(50.0, RobotProfile("r", 1, 1, 1.0, 0.0)) == 0.0


def test_robots_needing_energy_is_sorted() -> None:
    manager = BatteryManager()
    robots = (
        robot_at(80.0).model_copy(update={"robot_id": "robot-003"}),
        robot_at(5.0).model_copy(update={"robot_id": "robot-001"}),
        robot_at(15.0).model_copy(update={"robot_id": "robot-002"}),
    )

    assert manager.robots_needing_energy(robots, None) == ("robot-001", "robot-002")


def test_a_remaining_cell_count_must_not_be_negative() -> None:
    manager = BatteryManager()

    with pytest.raises(ValueError):
        manager.observe(robot_at(50.0), PROFILE, remaining_cells=-1)


def test_battery_updates_keep_the_other_robot_fields_intact() -> None:
    manager = BatteryManager()
    robot = evolve_robot(
        robot_at(50.0),
        status=RobotStatus.ACTIVE,
        communication_state=CommunicationState.DEGRADED,
    )

    updated = manager.apply_consumption(robot, 5.0, 4.0)

    assert updated.battery_percent == 45.0
    assert updated.status is RobotStatus.ACTIVE
    assert updated.communication_state is CommunicationState.DEGRADED
    assert updated.robot_id == "robot-001"
