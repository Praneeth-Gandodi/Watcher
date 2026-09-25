from __future__ import annotations

import pytest
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    RobotCapability,
    RobotStatus,
    TaskStatus,
)
from backend.negotiation.eligibility import (
    discover_eligible_robots,
    is_robot_eligible,
    validate_negotiable_task,
)

from .conftest import make_robot, make_task


def test_robot_with_required_capability_is_eligible() -> None:
    assert is_robot_eligible(make_task(), make_robot(), observed_at_s=2.0)


def test_missing_capability_is_not_eligible() -> None:
    task = make_task(required_capabilities=(RobotCapability.TUG,))

    assert not is_robot_eligible(task, make_robot(), observed_at_s=2.0)


@pytest.mark.parametrize(
    "updates",
    [
        {"battery_percent": 0.0},
        {"status": RobotStatus.ACTIVE, "current_task_id": "task-other"},
        {"status": RobotStatus.CHARGING},
        {"status": RobotStatus.BLOCKED},
        {"communication_state": CommunicationState.LOST},
        {"communication_state": CommunicationState.DEGRADED},
        {"last_updated_at_s": 3.0},
    ],
)
def test_unavailable_robot_is_not_eligible(updates: dict[str, object]) -> None:
    assert not is_robot_eligible(make_task(), make_robot(**updates), 2.0)


def test_failed_robot_is_not_eligible(failure: FailureInfo) -> None:
    robot = make_robot(status=RobotStatus.FAILED, failure=failure)

    assert not is_robot_eligible(make_task(), robot, 2.0)


def test_candidate_discovery_is_sorted_deduplicated_and_filtered() -> None:
    task = make_task()
    robots = (
        make_robot("robot-003"),
        make_robot("robot-001"),
        make_robot("robot-002"),
        make_robot("robot-001"),
    )

    candidates = discover_eligible_robots(
        task,
        robots,
        2.0,
        excluded_robot_ids=frozenset({"robot-002"}),
    )

    assert [robot.robot_id for robot in candidates] == ["robot-001", "robot-003"]


def test_terminal_task_cannot_be_negotiated() -> None:
    with pytest.raises(ValueError, match="terminal"):
        validate_negotiable_task(make_task(status=TaskStatus.COMPLETED), 2.0)


def test_future_task_cannot_be_negotiated() -> None:
    with pytest.raises(ValueError, match="future"):
        validate_negotiable_task(make_task(created_at_s=3.0), 2.0)


def test_candidate_collection_rejects_non_robots() -> None:
    with pytest.raises(TypeError, match="Robot"):
        discover_eligible_robots(make_task(), [object()], 2.0)  # type: ignore[list-item]
