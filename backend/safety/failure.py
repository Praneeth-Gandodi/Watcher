"""Robot failure, communication loss, and immutable state transitions.

The two fault modes are deliberately distinct:

* **Robot failure** -- the robot physically cannot continue. ``status`` becomes
  ``FAILED``, ``failure`` carries the canonical ``FailureInfo``, and the robot
  drops out of coordination entirely.
* **Communication loss** -- the robot still exists in the world and keeps
  moving, but ``communication_state`` becomes ``LOST`` so it is excluded from
  negotiation until contact returns.

``Robot`` is a frozen contract model, so every transition here rebuilds and
**revalidates** the instance. ``model_copy(update=...)`` would skip validation
in Pydantic v2, which could produce a ``FAILED`` robot without failure
information, so :func:`evolve_robot` re-runs the model instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.contracts.events import CommunicationLostPayload, RobotFailedPayload
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    Robot,
    RobotStatus,
)

__all__ = [
    "can_coordinate",
    "communication_lost_payload",
    "evolve_robot",
    "fail_robot",
    "failure_payload",
    "index_by_robot_id",
    "is_communicating",
    "is_failed",
    "is_ignored_by_safety",
    "is_mobile",
    "mark_communication_lost",
    "restore_communication",
    "restore_robot",
]

#: Statuses a robot may hold while it is still physically able to move.
_MOBILE_STATUSES = frozenset(
    {RobotStatus.IDLE, RobotStatus.ACTIVE, RobotStatus.BLOCKED, RobotStatus.CHARGING}
)


def evolve_robot(robot: Robot, **updates: Any) -> Robot:
    """Return a new, revalidated ``Robot`` with ``updates`` applied.

    This is the only supported way Agent 2 changes robot state. The canonical
    models are frozen, and rebuilding through the model keeps the contract's
    cross-field validation active instead of bypassing it.
    """

    if not isinstance(robot, Robot):
        raise TypeError("evolve_robot requires a Robot contract instance")
    if not updates:
        return robot
    data: dict[str, Any] = robot.model_dump()
    unknown = set(updates) - set(data)
    if unknown:
        raise ValueError(f"unknown Robot fields: {sorted(unknown)}")
    data.update(updates)
    return Robot.model_validate(data)


def fail_robot(
    robot: Robot,
    failure: FailureInfo,
    *,
    detected_at_s: float | None = None,
) -> Robot:
    """Mark a robot as physically failed."""

    if not isinstance(failure, FailureInfo):
        raise TypeError("failure must be a FailureInfo contract instance")
    at_s = robot.last_updated_at_s if detected_at_s is None else detected_at_s
    return evolve_robot(
        robot,
        status=RobotStatus.FAILED,
        failure=failure.model_copy(update={"detected_at_s": at_s})
        if detected_at_s is not None
        else failure,
        communication_state=CommunicationState.LOST,
        last_updated_at_s=max(at_s, robot.last_updated_at_s),
    )


def restore_robot(robot: Robot, *, restored_at_s: float | None = None) -> Robot:
    """Return a failed/degraded robot to an idle, communicating state.

    The robot keeps its identity, position, and battery: a failure is a robot
    fault, not a reset of the world.
    """

    at_s = robot.last_updated_at_s if restored_at_s is None else restored_at_s
    return evolve_robot(
        robot,
        status=RobotStatus.IDLE,
        failure=None,
        communication_state=CommunicationState.ONLINE,
        last_updated_at_s=max(at_s, robot.last_updated_at_s),
    )


def mark_communication_lost(robot: Robot, *, observed_at_s: float | None = None) -> Robot:
    """Drop a robot's peer communication while it stays physically present."""

    if is_failed(robot):
        raise ValueError("a failed robot is not a communication-loss case")
    at_s = robot.last_updated_at_s if observed_at_s is None else observed_at_s
    return evolve_robot(
        robot,
        communication_state=CommunicationState.LOST,
        last_updated_at_s=max(at_s, robot.last_updated_at_s),
    )


def restore_communication(
    robot: Robot,
    *,
    observed_at_s: float | None = None,
) -> Robot:
    """Return a robot to online coordination."""

    at_s = robot.last_updated_at_s if observed_at_s is None else observed_at_s
    return evolve_robot(
        robot,
        communication_state=CommunicationState.ONLINE,
        last_updated_at_s=max(at_s, robot.last_updated_at_s),
    )


def is_failed(robot: Robot) -> bool:
    return robot.status is RobotStatus.FAILED


def is_communicating(robot: Robot) -> bool:
    """Return whether the robot is in contact with the coordination layer."""

    return robot.communication_state is not CommunicationState.LOST


def can_coordinate(robot: Robot) -> bool:
    """Return whether a robot may take part in negotiation/coordination."""

    if is_failed(robot):
        return False
    return robot.communication_state is not CommunicationState.LOST


def is_ignored_by_safety(robot: Robot) -> bool:
    """Return whether fleet safety analysis should skip this robot.

    A failed robot is not predicted to move, and an offline robot is a
    stationary obstacle only. Neither should generate right-of-way conflicts
    against a live trajectory.
    """

    return robot.status in {RobotStatus.FAILED, RobotStatus.OFFLINE}


def is_mobile(robot: Robot) -> bool:
    return robot.status in _MOBILE_STATUSES


def failure_payload(robot: Robot) -> RobotFailedPayload:
    """Build the canonical ``ROBOT_FAILED`` payload for a failed robot."""

    if robot.failure is None:
        raise ValueError(f"robot {robot.robot_id!r} has no failure information")
    return RobotFailedPayload(
        robot_id=robot.robot_id,
        failure=robot.failure,
    )


def communication_lost_payload(
    robot: Robot,
    *,
    last_contact_at_s: float,
    timeout_s: float,
) -> CommunicationLostPayload:
    """Build the canonical ``COMMUNICATION_LOST`` payload for a robot."""

    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    return CommunicationLostPayload(
        robot_id=robot.robot_id,
        last_contact_at_s=last_contact_at_s,
        timeout_s=timeout_s,
    )


def index_by_robot_id(robots: object) -> dict[str, Robot]:
    """Return a deterministic ``robot_id -> Robot`` mapping."""

    if not isinstance(robots, Mapping) and not hasattr(robots, "__iter__"):
        raise TypeError("robots must be iterable or a mapping")
    indexed: dict[str, Robot] = {}
    for robot in robots.values() if isinstance(robots, Mapping) else robots:
        if not isinstance(robot, Robot):
            raise TypeError("only Robot contract instances are supported")
        indexed.setdefault(robot.robot_id, robot)
    return {robot_id: indexed[robot_id] for robot_id in sorted(indexed)}
