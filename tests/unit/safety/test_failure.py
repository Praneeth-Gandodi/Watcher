"""Test plan J: robot failure, communication loss, and restore.

Covers:
* a failure changes the canonical robot state correctly
* a failure event can be produced from the canonical fields
* communication loss is distinct from robot failure
* restore brings a failed robot back, and separately restores a lost link
* state transitions revalidate the frozen contract instead of bypassing it
"""

from __future__ import annotations

import pytest
from backend.contracts.events import CommunicationLostPayload, RobotFailedPayload
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    RobotStatus,
)
from backend.safety.failure import (
    can_coordinate,
    communication_lost_payload,
    evolve_robot,
    fail_robot,
    failure_payload,
    index_by_robot_id,
    is_communicating,
    is_failed,
    is_ignored_by_safety,
    is_mobile,
    mark_communication_lost,
    restore_communication,
    restore_robot,
)
from backend.simulation.world import make_robot


def online_robot() -> object:
    return make_robot("robot-001", (0, 0), battery_percent=64.0)


def failure() -> FailureInfo:
    return FailureInfo(
        kind=FailureKind.ACTUATOR,
        code="drive-failure",
        detected_at_s=12.0,
        detail="Left drive motor stopped responding.",
    )


# ----------------------------------------------------------------------
# evolve
# ----------------------------------------------------------------------


def test_evolve_returns_a_new_revalidated_robot() -> None:
    robot = online_robot()

    updated = evolve_robot(robot, battery_percent=10.0, last_updated_at_s=5.0)

    assert updated is not robot
    assert updated.battery_percent == 10.0
    assert updated.last_updated_at_s == 5.0
    assert robot.battery_percent == 64.0, "the original must not be mutated"
    assert evolve_robot(robot) is robot


def test_evolve_rejects_unknown_fields_and_wrong_types() -> None:
    robot = online_robot()

    with pytest.raises(ValueError, match="unknown Robot fields"):
        evolve_robot(robot, width_cells=2)
    with pytest.raises(TypeError):
        evolve_robot("robot-001", battery_percent=1.0)


def test_evolve_revalidates_cross_field_rules() -> None:
    """A FAILED robot without failure information must be rejected."""

    with pytest.raises(ValueError, match="failed robot must include failure"):
        evolve_robot(online_robot(), status=RobotStatus.FAILED, failure=None)
    with pytest.raises(ValueError, match="failure information requires"):
        evolve_robot(online_robot(), status=RobotStatus.ACTIVE, failure=failure())


# ----------------------------------------------------------------------
# robot failure
# ----------------------------------------------------------------------


def test_failing_a_robot_sets_the_canonical_failure_state() -> None:
    failed = fail_robot(online_robot(), failure(), detected_at_s=12.0)

    assert failed.status is RobotStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.kind is FailureKind.ACTUATOR
    assert failed.failure.code == "drive-failure"
    assert failed.failure.detected_at_s == 12.0
    # A failed robot is out of coordination as well.
    assert failed.communication_state is CommunicationState.LOST
    assert failed.last_updated_at_s == 12.0
    assert failed.position.x == 0.5 and failed.position.y == 0.5
    assert is_failed(failed) is True
    assert is_communicating(failed) is False
    assert can_coordinate(failed) is False
    assert is_ignored_by_safety(failed) is True
    assert is_mobile(failed) is False


def test_a_failure_event_can_be_built_from_the_canonical_fields() -> None:
    failed = fail_robot(online_robot(), failure(), detected_at_s=12.0)

    payload = failure_payload(failed)

    assert isinstance(payload, RobotFailedPayload)
    assert payload.event_type.value == "ROBOT_FAILED"
    assert payload.robot_id == "robot-001"
    assert payload.failure == failed.failure


def test_a_failure_payload_needs_failure_information() -> None:
    with pytest.raises(ValueError, match="no failure information"):
        failure_payload(online_robot())


def test_fail_robot_rejects_a_non_failure_info() -> None:
    with pytest.raises(TypeError):
        fail_robot(online_robot(), {"kind": "actuator"}, detected_at_s=1.0)


# ----------------------------------------------------------------------
# communication loss
# ----------------------------------------------------------------------


def test_communication_loss_is_distinct_from_robot_failure() -> None:
    lost = mark_communication_lost(online_robot(), observed_at_s=7.0)

    # The robot still exists physically and keeps its work, but it drops out of
    # coordination. It is NOT failed.
    assert lost.status is RobotStatus.IDLE
    assert lost.failure is None
    assert lost.communication_state is CommunicationState.LOST
    assert lost.last_updated_at_s == 7.0
    assert is_failed(lost) is False
    assert is_communicating(lost) is False
    assert can_coordinate(lost) is False
    # It is still a physical presence, so safety still accounts for it.
    assert is_ignored_by_safety(lost) is False
    assert is_mobile(lost) is True


def test_a_communication_lost_event_can_be_built_from_the_canonical_fields() -> None:
    lost = mark_communication_lost(online_robot(), observed_at_s=7.0)

    payload = communication_lost_payload(
        lost, last_contact_at_s=4.0, timeout_s=3.0
    )

    assert isinstance(payload, CommunicationLostPayload)
    assert payload.event_type.value == "COMMUNICATION_LOST"
    assert payload.robot_id == "robot-001"
    assert payload.last_contact_at_s == 4.0
    assert payload.timeout_s == 3.0
    with pytest.raises(ValueError):
        communication_lost_payload(lost, last_contact_at_s=4.0, timeout_s=0.0)


def test_a_failed_robot_is_not_a_communication_loss_case() -> None:
    failed = fail_robot(online_robot(), failure(), detected_at_s=12.0)

    with pytest.raises(ValueError, match="not a communication-loss case"):
        mark_communication_lost(failed, observed_at_s=13.0)


def test_degraded_communication_still_counts_as_coordination() -> None:
    degraded = evolve_robot(online_robot(), communication_state=CommunicationState.DEGRADED)

    assert is_communicating(degraded) is True
    assert can_coordinate(degraded) is True


# ----------------------------------------------------------------------
# restore
# ----------------------------------------------------------------------


def test_restoring_a_failed_robot_keeps_its_identity_and_position() -> None:
    failed = fail_robot(online_robot(), failure(), detected_at_s=12.0)

    restored = restore_robot(failed, restored_at_s=20.0)

    assert restored.status is RobotStatus.IDLE
    assert restored.failure is None
    assert restored.communication_state is CommunicationState.ONLINE
    assert restored.last_updated_at_s == 20.0
    assert restored.robot_id == "robot-001"
    assert restored.position == failed.position
    assert restored.battery_percent == failed.battery_percent
    assert is_failed(restored) is False
    assert can_coordinate(restored) is True


def test_restoring_communication_is_separate_from_restoring_a_robot() -> None:
    lost = mark_communication_lost(online_robot(), observed_at_s=7.0)

    restored = restore_communication(lost, observed_at_s=9.0)

    assert restored.communication_state is CommunicationState.ONLINE
    assert restored.status is RobotStatus.IDLE
    assert restored.failure is None
    assert restored.last_updated_at_s == 9.0


def test_restore_never_moves_the_clock_backwards() -> None:
    robot = evolve_robot(online_robot(), last_updated_at_s=30.0)

    assert restore_robot(robot, restored_at_s=5.0).last_updated_at_s == 30.0
    assert mark_communication_lost(robot, observed_at_s=5.0).last_updated_at_s == 30.0


def test_offline_robots_are_ignored_by_safety() -> None:
    offline = evolve_robot(
        online_robot(),
        status=RobotStatus.OFFLINE,
        failure=failure(),
        communication_state=CommunicationState.LOST,
    )

    assert is_ignored_by_safety(offline) is True
    assert can_coordinate(offline) is False


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def test_index_by_robot_id_is_sorted_and_deduplicated() -> None:
    robots = (
        online_robot().model_copy(update={"robot_id": "robot-002"}),
        online_robot().model_copy(update={"robot_id": "robot-001"}),
        online_robot().model_copy(update={"robot_id": "robot-001"}),
    )

    indexed = index_by_robot_id(robots)

    assert list(indexed) == ["robot-001", "robot-002"]
    assert index_by_robot_id(indexed).keys() == indexed.keys()
    with pytest.raises(TypeError):
        index_by_robot_id(["robot-001"])
