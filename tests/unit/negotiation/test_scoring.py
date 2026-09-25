from __future__ import annotations

from backend.negotiation.scoring import (
    DeterministicBidIdFactory,
    calculate_bid_costs,
    create_bid,
)

from .conftest import make_robot, make_task


def test_bid_costs_are_workload_aware_and_deterministic() -> None:
    robot = make_robot(battery_percent=75.0, workload=2)
    task = make_task(estimated_duration_s=20.0)

    costs = calculate_bid_costs(robot, task)

    assert costs.distance_cost == 5.0
    assert costs.battery_cost == 25.0
    assert costs.workload_cost == 2.0
    assert costs.total_cost == 32.0
    assert costs.estimated_completion_time_s == 25.0


def test_bid_id_is_stable_for_same_observation() -> None:
    robot = make_robot()
    task = make_task()
    factory = DeterministicBidIdFactory()

    first = create_bid(task, robot, 2.0, 7.0, factory)
    second = create_bid(task, robot, 2.0, 7.0, factory)

    assert first == second
    assert first.bid_id.startswith("bid-")
    assert first.robot_id == "robot-001"
    assert first.task_id == "task-001"
    assert first.valid_until_s == 7.0
