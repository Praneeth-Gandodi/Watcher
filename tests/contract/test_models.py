import pytest
from pydantic import ValidationError

from backend.contracts.fixtures import valid_robot, valid_task
from backend.contracts.models import Bid, Robot, RobotStatus


def test_robot_and_task_round_trip() -> None:
    robot = valid_robot()
    task = valid_task()
    assert RobotStatus(robot.model_dump(mode="json")["status"]) is RobotStatus.IDLE
    assert task.assigned_robot_id is None


def test_unknown_robot_field_is_rejected() -> None:
    data = valid_robot().model_dump(mode="json")
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        Robot.model_validate(data)


def test_bid_validity_window_is_enforced() -> None:
    data = {
        "bid_id": "bid-001",
        "robot_id": "robot-001",
        "task_id": "task-001",
        "total_cost": 10.0,
        "distance_cost": 3.0,
        "battery_cost": 2.0,
        "workload_cost": 5.0,
        "estimated_completion_time_s": 40.0,
        "created_at_s": 10.0,
        "valid_until_s": 10.0,
    }
    with pytest.raises(ValidationError):
        Bid.model_validate(data)
