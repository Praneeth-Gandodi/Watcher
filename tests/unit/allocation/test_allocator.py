from __future__ import annotations

import asyncio

from backend.allocation.allocator import (
    BestBidAllocator,
    is_bid_valid_at,
    select_winning_bid,
)
from backend.contracts.models import (
    Bid,
    NegotiationOutcome,
    NegotiationStatus,
    TaskAssignment,
)


def make_bid(
    bid_id: str,
    *,
    robot_id: str,
    total_cost: float,
    completion: float = 20.0,
    workload: float = 0.0,
    task_id: str = "task-001",
    created_at_s: float = 1.0,
    valid_until_s: float = 5.0,
) -> Bid:
    return Bid(
        bid_id=bid_id,
        robot_id=robot_id,
        task_id=task_id,
        total_cost=total_cost,
        distance_cost=total_cost / 2.0,
        battery_cost=total_cost / 4.0,
        workload_cost=workload,
        estimated_completion_time_s=completion,
        created_at_s=created_at_s,
        valid_until_s=valid_until_s,
    )


def make_outcome(
    bids: tuple[Bid, ...],
    *,
    assignment: TaskAssignment | None = None,
) -> NegotiationOutcome:
    return NegotiationOutcome(
        task_id="task-001",
        status=NegotiationStatus.ASSIGNED if assignment else NegotiationStatus.UNASSIGNED,
        bids=bids,
        assignment=assignment,
        started_at_s=1.0,
        completed_at_s=2.0,
    )


def test_lowest_valid_cost_wins() -> None:
    bids = (
        make_bid("bid-high", robot_id="robot-a", total_cost=20.0),
        make_bid("bid-low", robot_id="robot-b", total_cost=10.0),
    )

    winner = select_winning_bid(bids, make_outcome(bids))

    assert winner is not None
    assert winner.bid_id == "bid-low"


def test_ties_are_broken_deterministically() -> None:
    first = make_bid(
        "bid-z",
        robot_id="robot-z",
        total_cost=10.0,
        completion=20.0,
        workload=2.0,
    )
    second = make_bid(
        "bid-a",
        robot_id="robot-a",
        total_cost=10.0,
        completion=20.0,
        workload=2.0,
    )

    assert select_winning_bid((first, second), make_outcome((first, second))) == second


def test_expired_and_mismatched_bids_are_invalid() -> None:
    expired = make_bid(
        "bid-expired",
        robot_id="robot-a",
        total_cost=1.0,
        valid_until_s=2.0,
    )
    mismatched = make_bid(
        "bid-other-task",
        robot_id="robot-b",
        total_cost=1.0,
        task_id="task-002",
    )
    valid = make_bid("bid-valid", robot_id="robot-c", total_cost=30.0)
    outcome = make_outcome((expired, mismatched, valid))

    assert not is_bid_valid_at(expired, outcome, outcome.completed_at_s)
    assert not is_bid_valid_at(mismatched, outcome, outcome.completed_at_s)
    winner = select_winning_bid((expired, mismatched, valid), outcome)
    assert winner == valid


def test_future_created_bid_is_invalid() -> None:
    bid = make_bid("bid-future", robot_id="robot-a", total_cost=1.0, created_at_s=3.0)
    outcome = make_outcome((bid,))

    assert select_winning_bid((bid,), outcome) is None


def test_allocator_creates_canonical_assignment() -> None:
    bid = make_bid("bid-low", robot_id="robot-b", total_cost=10.0)
    outcome = make_outcome((bid,))

    assignment = asyncio.run(BestBidAllocator().assign(outcome))

    assert assignment == TaskAssignment(
        task_id="task-001",
        robot_id="robot-b",
        bid_id="bid-low",
        assigned_at_s=2.0,
        reason="lowest valid eligible bid",
    )


def test_allocator_returns_none_when_all_bids_are_expired() -> None:
    bid = make_bid("bid-old", robot_id="robot-a", total_cost=1.0, valid_until_s=2.0)
    outcome = make_outcome((bid,))

    assert asyncio.run(BestBidAllocator().assign(outcome)) is None


def test_allocator_rejects_assigned_outcome_for_nonwinning_bid() -> None:
    bids = (
        make_bid("bid-low", robot_id="robot-b", total_cost=10.0),
        make_bid("bid-high", robot_id="robot-a", total_cost=20.0),
    )
    invalid_assignment = TaskAssignment(
        task_id="task-001",
        robot_id="robot-a",
        bid_id="bid-high",
        assigned_at_s=2.0,
        reason="manual override",
    )
    outcome = make_outcome(bids, assignment=invalid_assignment)

    assert asyncio.run(BestBidAllocator().assign(outcome)) is None
