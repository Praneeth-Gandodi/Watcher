"""Task and robot eligibility for Agent 1 negotiation."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from backend.contracts.models import (
    CommunicationState,
    Robot,
    RobotStatus,
    Task,
    TaskStatus,
)

_NON_NEGOTIABLE_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


def validate_observed_time(observed_at_s: float) -> None:
    """Reject invalid simulation-time inputs before creating a negotiation."""

    if not isfinite(observed_at_s) or observed_at_s < 0:
        raise ValueError("observed_at_s must be a finite, non-negative number")


def validate_negotiable_task(task: Task, observed_at_s: float) -> None:
    """Validate that a task may enter a new decision round."""

    validate_observed_time(observed_at_s)
    if task.status in _NON_NEGOTIABLE_TASK_STATUSES:
        raise ValueError(f"task {task.task_id!r} is terminal and cannot be negotiated")
    if task.created_at_s > observed_at_s:
        raise ValueError("task creation cannot be in the future")


def is_robot_eligible(task: Task, robot: Robot, observed_at_s: float) -> bool:
    """Return whether a robot can currently bid for a task.

    Battery safety thresholds remain owned by Agent 2. Agent 1 excludes only a
    robot with no remaining energy and handles explicit safety observations at
    the reassignment boundary.
    """

    validate_observed_time(observed_at_s)
    if task.status in _NON_NEGOTIABLE_TASK_STATUSES:
        return False
    if robot.last_updated_at_s > observed_at_s:
        return False
    if robot.battery_percent <= 0:
        return False
    if robot.status is not RobotStatus.IDLE or robot.current_task_id is not None:
        return False
    if robot.communication_state is not CommunicationState.ONLINE:
        return False
    if robot.failure is not None:
        return False
    return set(task.required_capabilities).issubset(robot.capabilities)


def discover_eligible_robots(
    task: Task,
    robots: Iterable[object],
    observed_at_s: float,
    *,
    excluded_robot_ids: frozenset[str] = frozenset(),
) -> tuple[Robot, ...]:
    """Discover eligible candidates in deterministic robot-ID order."""

    validate_negotiable_task(task, observed_at_s)
    candidates: dict[str, Robot] = {}
    for robot in robots:
        if not isinstance(robot, Robot):
            raise TypeError("candidate_robots must contain Robot instances")
        if robot.robot_id in excluded_robot_ids:
            continue
        if is_robot_eligible(task, robot, observed_at_s):
            candidates.setdefault(robot.robot_id, robot)
    return tuple(candidates[robot_id] for robot_id in sorted(candidates))
