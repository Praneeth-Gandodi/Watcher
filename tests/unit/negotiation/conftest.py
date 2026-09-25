from __future__ import annotations

from collections.abc import Callable

import pytest
from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    Position2D,
    Robot,
    RobotCapability,
    RobotStatus,
    Task,
    TaskStatus,
)


def make_robot(
    robot_id: str = "robot-001",
    **updates: object,
) -> Robot:
    robot = Robot(
        robot_id=robot_id,
        position=Position2D(x=0.0, y=0.0),
        battery_percent=90.0,
        capabilities=(RobotCapability.TRANSPORT,),
        workload=1,
        status=RobotStatus.IDLE,
        current_task_id=None,
        communication_state=CommunicationState.ONLINE,
        failure=None,
        last_updated_at_s=1.0,
    )
    return robot.model_copy(update=updates)


def make_task(**updates: object) -> Task:
    task = Task(
        task_id="task-001",
        target=Position2D(x=3.0, y=4.0),
        priority=4,
        required_capabilities=(RobotCapability.TRANSPORT,),
        estimated_duration_s=20.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )
    return task.model_copy(update=updates)


@pytest.fixture
def robot_factory() -> Callable[..., Robot]:
    return make_robot


@pytest.fixture
def task_factory() -> Callable[..., Task]:
    return make_task


@pytest.fixture
def failure() -> FailureInfo:
    return FailureInfo(kind="actuator", code="drive-failure", detected_at_s=2.0)
