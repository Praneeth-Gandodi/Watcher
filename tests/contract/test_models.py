import pytest
from pydantic import ValidationError

from backend.contracts.fixtures import valid_robot, valid_task, valid_world
from backend.contracts.models import Bid, GridCellType, Robot, RobotStatus, WorldState


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


def test_world_grid_contract() -> None:
    world = valid_world()
    assert world.columns == 20
    assert world.cells[0].cell_type is GridCellType.OBSTACLE
    assert world.model_dump(mode="json")["cell_size_m"] == 2.0


def test_grid_cell_must_be_inside_world() -> None:
    with pytest.raises(ValidationError):
        WorldState.model_validate(
            {
                "width_m": 10.0,
                "height_m": 10.0,
                "cell_size_m": 1.0,
                "columns": 2,
                "rows": 2,
                "cells": [{"cell_x": 2, "cell_y": 0, "cell_type": "obstacle"}],
                "revision": 1,
            }
        )


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
