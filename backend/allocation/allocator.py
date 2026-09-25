"""Deterministic winner selection and canonical task assignment."""

from __future__ import annotations

from collections.abc import Iterable

from backend.contracts.models import Bid, NegotiationOutcome, TaskAssignment


def is_bid_valid_at(bid: Bid, outcome: NegotiationOutcome, observed_at_s: float) -> bool:
    """Return whether a bid belongs to and remains valid for an outcome."""

    return (
        bid.task_id == outcome.task_id
        and bid.created_at_s <= observed_at_s < bid.valid_until_s
    )


def _winner_sort_key(bid: Bid) -> tuple[float, float, float, str, str]:
    return (
        bid.total_cost,
        bid.estimated_completion_time_s,
        bid.workload_cost,
        bid.robot_id,
        bid.bid_id,
    )


def select_winning_bid(
    bids: Iterable[Bid],
    outcome: NegotiationOutcome,
) -> Bid | None:
    """Select the lowest valid bid with deterministic tie-breaking."""

    valid_bids = (
        bid for bid in bids if is_bid_valid_at(bid, outcome, outcome.completed_at_s)
    )
    return min(valid_bids, key=_winner_sort_key, default=None)


def create_assignment(
    outcome: NegotiationOutcome,
    bid: Bid,
    *,
    reason: str = "lowest valid eligible bid",
) -> TaskAssignment:
    """Create a canonical assignment for a selected bid."""

    if bid.task_id != outcome.task_id:
        raise ValueError("cannot assign a bid for a different task")
    if not is_bid_valid_at(bid, outcome, outcome.completed_at_s):
        raise ValueError("cannot assign an invalid or expired bid")
    return TaskAssignment(
        task_id=outcome.task_id,
        robot_id=bid.robot_id,
        bid_id=bid.bid_id,
        assigned_at_s=outcome.completed_at_s,
        reason=reason,
    )


class BestBidAllocator:
    """Allocate an outcome to its best valid bid."""

    async def assign(self, outcome: NegotiationOutcome) -> TaskAssignment | None:
        winning_bid = select_winning_bid(outcome.bids, outcome)
        if winning_bid is None:
            return None
        if outcome.assignment is not None:
            assignment = outcome.assignment
            if (
                assignment.task_id != winning_bid.task_id
                or assignment.robot_id != winning_bid.robot_id
                or assignment.bid_id != winning_bid.bid_id
            ):
                return None
            return assignment
        return create_assignment(outcome, winning_bid)
