"""Robot physical profiles: footprint, speed, and the deterministic registry.

The canonical ``Robot`` contract is frozen and carries no physical geometry, so
Agent 2 keeps it in a registry keyed by ``robot_id`` rather than widening the
shared model.
"""

from __future__ import annotations

import pytest
from backend.safety.robot_profile import (
    DEFAULT_PROFILE,
    RobotProfile,
    RobotProfileRegistry,
)


def test_a_profile_carries_footprint_speed_and_consumption_rate() -> None:
    profile = RobotProfile(
        "robot-001", width_cells=2, height_cells=2, speed_mps=2.0
    )

    assert profile.robot_id == "robot-001"
    assert profile.footprint_cell_count == 4
    assert profile.time_per_cell_s(2.0) == 1.0
    assert profile.battery_percent_for_cells(4) == 4.0
    assert profile.is_default_size is False
    assert DEFAULT_PROFILE.is_default_size is True


def test_a_profile_is_immutable() -> None:
    profile = RobotProfile("robot-001")

    with pytest.raises(Exception):
        profile.speed_mps = 5.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width_cells": 0},
        {"height_cells": -1},
        {"speed_mps": 0.0},
        {"speed_mps": -1.0},
        {"speed_mps": float("inf")},
        {"battery_percent_per_cell": -0.5},
    ],
)
def test_invalid_profiles_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RobotProfile("robot-001", **kwargs)  # type: ignore[arg-type]


def test_time_per_cell_is_cell_size_over_speed() -> None:
    profile = RobotProfile("robot-001", 1, 1, speed_mps=0.5)

    assert profile.time_per_cell_s(1.0) == 2.0
    assert profile.time_per_cell_s(2.0) == 4.0
    with pytest.raises(ValueError):
        profile.time_per_cell_s(0.0)


def test_the_registry_is_a_deterministic_mapping() -> None:
    registry = RobotProfileRegistry(
        [
            RobotProfile("robot-003", 3, 2, 1.0),
            RobotProfile("robot-001", 1, 1, 2.0),
            RobotProfile("robot-002", 2, 2, 0.5),
        ]
    )

    assert registry.robot_ids() == ("robot-001", "robot-002", "robot-003")
    assert [profile.robot_id for profile in registry] == list(registry.robot_ids())
    assert len(registry) == 3
    assert "robot-002" in registry
    assert list(registry.as_mapping()) == list(registry.robot_ids())
    assert "RobotProfileRegistry" in repr(registry)


def test_registration_order_does_not_change_the_registry() -> None:
    forward = RobotProfileRegistry(
        [RobotProfile("robot-001", 1, 1, 1.0), RobotProfile("robot-002", 2, 2, 2.0)]
    )
    backward = RobotProfileRegistry(
        [RobotProfile("robot-002", 2, 2, 2.0), RobotProfile("robot-001", 1, 1, 1.0)]
    )

    assert forward.robot_ids() == backward.robot_ids()
    assert forward.require("robot-002") == backward.require("robot-002")


def test_duplicate_registration_is_rejected() -> None:
    registry = RobotProfileRegistry([RobotProfile("robot-001")])

    with pytest.raises(ValueError, match="duplicate"):
        registry.register(RobotProfile("robot-001"))
    with pytest.raises(ValueError, match="duplicate"):
        RobotProfileRegistry([RobotProfile("robot-001"), RobotProfile("robot-001")])


def test_only_profiles_can_be_registered() -> None:
    with pytest.raises(TypeError):
        RobotProfileRegistry([{"robot_id": "robot-001"}])  # type: ignore[list-item]


def test_an_unregistered_robot_falls_back_to_the_default_shape() -> None:
    registry = RobotProfileRegistry()

    profile = registry.get("robot-999")

    assert profile.robot_id == "robot-999"
    assert (profile.width_cells, profile.height_cells) == (1, 1)
    assert profile.speed_mps == 1.0


def test_require_raises_for_an_unregistered_robot() -> None:
    registry = RobotProfileRegistry([RobotProfile("robot-001")])

    with pytest.raises(KeyError, match="robot-999"):
        registry.require("robot-999")


def test_custom_defaults_apply_to_unregistered_robots() -> None:
    registry = RobotProfileRegistry(
        defaults=RobotProfile("default", 2, 1, 3.0)
    )

    profile = registry.get("robot-999")

    assert (profile.width_cells, profile.height_cells, profile.speed_mps) == (2, 1, 3.0)
    assert registry.defaults.speed_mps == 3.0


def test_a_registered_profile_wins_over_the_defaults() -> None:
    registry = RobotProfileRegistry(
        [RobotProfile("robot-001", 3, 2, 0.5)],
        defaults=RobotProfile("default", 1, 1, 1.0),
    )

    assert registry.get("robot-001").footprint_cell_count == 6


def test_uniform_registry_builds_one_shape() -> None:
    registry = RobotProfileRegistry.uniform(
        ["robot-003", "robot-001", "robot-002"], width_cells=2, height_cells=3
    )

    assert registry.robot_ids() == ("robot-001", "robot-002", "robot-003")
    assert {profile.footprint_cell_count for profile in registry} == {6}


def test_a_registry_can_be_built_from_a_mapping() -> None:
    registry = RobotProfileRegistry({"robot-001": RobotProfile("robot-001", 2, 2)})

    assert registry.require("robot-001").width_cells == 2
