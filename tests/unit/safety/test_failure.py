"""Unit tests for failure injection, communication loss, and restoration."""

from __future__ import annotations

import pytest

from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    Position2D,
    Robot,
    RobotStatus,
)
from safety.failure import FailureRegistry, degraded_failure


def make_robot(
    robot_id: str = "robot-001",
    *,
    battery: float = 80.0,
    status: RobotStatus = RobotStatus.ACTIVE,
    communication_state: CommunicationState = CommunicationState.ONLINE,
    failure: FailureInfo | None = None,
) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=Position2D(x=4.0, y=4.0),
        battery_percent=battery,
        capabilities=("transport",),
        workload=0,
        status=status,
        current_task_id=None,
        communication_state=communication_state,
        failure=failure,
        last_updated_at_s=0.0,
    )


class TestFailureInjection:
    def test_an_injected_failure_marks_the_robot_failed(self) -> None:
        registry = FailureRegistry()
        failure = registry.inject_failure(
            "robot-001",
            FailureInfo(
                kind=FailureKind.ACTUATOR, code="actuator-fault", detected_at_s=0.0, detail=None
            ),
            now_s=0.0,
        )
        verdict = registry.evaluate(make_robot(), now_s=0.0)
        assert verdict.usable is False
        assert verdict.status is RobotStatus.FAILED
        assert verdict.failure == failure

    def test_a_failure_outranks_communication_loss(self) -> None:
        registry = FailureRegistry()
        registry.inject_failure(
            "robot-001",
            FailureInfo(kind=FailureKind.SENSOR, code="sensor-fault", detected_at_s=0.0, detail=None),
            now_s=0.0,
        )
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=10.0)
        verdict = registry.evaluate(make_robot(), now_s=1.0)
        assert verdict.status is RobotStatus.FAILED

    def test_the_failure_reason_names_the_code(self) -> None:
        registry = FailureRegistry()
        registry.inject_failure(
            "robot-001",
            FailureInfo(kind=FailureKind.COMPUTE, code="compute-fault", detected_at_s=0.0, detail=None),
            now_s=0.0,
        )
        assert "compute/compute-fault" in registry.evaluate(make_robot(), now_s=0.0).reason


class TestCommunicationLoss:
    def test_a_silenced_robot_is_degraded_and_unreachable(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=5.0)
        verdict = registry.evaluate(make_robot(), now_s=1.0)
        assert verdict.usable is False
        assert verdict.status is RobotStatus.DEGRADED
        assert verdict.communication_state is CommunicationState.LOST

    def test_communication_returns_after_the_timeout(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=5.0)
        assert registry.evaluate(make_robot(), now_s=6.0).usable is True

    def test_reports_the_remaining_silence(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=5.0)
        assert registry.silence_remaining_s("robot-001", now_s=2.0) == pytest.approx(3.0)

    def test_reports_no_remaining_silence_when_never_silenced(self) -> None:
        assert FailureRegistry().silence_remaining_s("robot-001", now_s=2.0) == 0.0

    def test_tracks_time_since_last_contact(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=5.0)
        assert registry.contact_lost_for_s("robot-001", now_s=3.0) == pytest.approx(3.0)

    def test_a_short_timeout_still_silences(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=0.0)
        assert registry.evaluate(make_robot(), now_s=0.0).usable is True

    def test_is_silenced_reports_the_window(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=3.0)
        assert registry.is_silenced("robot-001", now_s=2.9) is True
        assert registry.is_silenced("robot-001", now_s=3.1) is False


class TestRestoration:
    def test_restore_clears_a_failure(self) -> None:
        registry = FailureRegistry()
        registry.inject_failure(
            "robot-001",
            FailureInfo(kind=FailureKind.OTHER, code="injected", detected_at_s=0.0, detail=None),
            now_s=0.0,
        )
        assert registry.restore("robot-001") is True
        assert registry.evaluate(make_robot(), now_s=1.0).usable is True

    def test_restore_clears_communication_loss(self) -> None:
        registry = FailureRegistry()
        registry.inject_communication_loss("robot-001", now_s=0.0, timeout_s=60.0)
        registry.restore("robot-001")
        assert registry.evaluate(make_robot(), now_s=1.0).usable is True

    def test_restoring_an_untouched_robot_reports_no_change(self) -> None:
        assert FailureRegistry().restore("robot-001") is False


class TestNominalAndDegradedStates:
    def test_a_healthy_robot_is_usable(self) -> None:
        verdict = FailureRegistry().evaluate(make_robot(), now_s=0.0)
        assert verdict.usable is True
        assert verdict.status is RobotStatus.ACTIVE
        assert verdict.reason == "nominal"

    def test_a_depleted_battery_takes_the_robot_offline(self) -> None:
        verdict = FailureRegistry().evaluate(make_robot(battery=0.0), now_s=5.0)
        assert verdict.usable is False
        assert verdict.status is RobotStatus.OFFLINE
        assert verdict.failure is not None
        assert verdict.failure.kind is FailureKind.BATTERY

    def test_a_dead_battery_keeps_reporting_the_charger(self) -> None:
        # A depleted robot still answers, so the failure is not a comms loss.
        verdict = FailureRegistry().evaluate(make_robot(battery=0.0), now_s=5.0)
        assert verdict.communication_state is CommunicationState.ONLINE

    def test_rejects_a_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError):
            FailureRegistry(communication_timeout_s=0.0)


class TestDegradedFailureFactory:
    def test_builds_a_non_fatal_failure_record(self) -> None:
        failure = degraded_failure(
            FailureKind.SENSOR, "sensor-degraded", now_s=1.234, detail="partial"
        )
        assert failure.detected_at_s == 1.234
        assert failure.detail == "partial"
        assert failure.kind is FailureKind.SENSOR
