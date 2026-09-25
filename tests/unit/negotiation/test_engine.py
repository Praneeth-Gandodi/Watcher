from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from backend.contracts.events import (
    BidSubmittedPayload,
    EventType,
    NegotiationStartedPayload,
    TaskAssignedPayload,
)
from backend.contracts.models import NegotiationStatus, Position2D
from backend.negotiation.engine import DefaultNegotiationEngine
from backend.negotiation.events import DecisionEventContext

from .conftest import make_robot, make_task


def event_ids() -> Iterator[UUID]:
    for suffix in range(1, 10):
        yield UUID(f"00000000-0000-4000-8000-{suffix:012d}")


def event_context() -> DecisionEventContext:
    return DecisionEventContext(
        correlation_id="task-task-001",
        first_sequence=10,
        event_id_factory=event_ids().__next__,
    )


def test_negotiation_selects_lowest_total_cost_and_emits_canonical_events() -> None:
    robots = (
        make_robot("robot-001", position=Position2D(x=3.0, y=4.0), workload=10),
        make_robot("robot-002", position=Position2D(x=1.0, y=1.0), workload=1),
    )
    engine = DefaultNegotiationEngine(bid_validity_s=5.0)

    decision = asyncio.run(engine.decide(make_task(), robots, 2.0, event_context()))

    assert decision.outcome.status is NegotiationStatus.ASSIGNED
    assert decision.outcome.assignment is not None
    assert decision.outcome.assignment.robot_id == "robot-002"
    assert [event.sequence for event in decision.events] == [10, 11, 12, 13]
    assert [event.event_type for event in decision.events] == [
        EventType.NEGOTIATION_STARTED,
        EventType.BID_SUBMITTED,
        EventType.BID_SUBMITTED,
        EventType.TASK_ASSIGNED,
    ]
    started = decision.events[0]
    assert isinstance(started.payload, NegotiationStartedPayload)
    assert started.payload.candidate_robot_ids == ("robot-001", "robot-002")
    assert started.payload.expires_at_s == 7.0
    assert all(isinstance(event.payload, BidSubmittedPayload) for event in decision.events[1:3])
    assert isinstance(decision.events[3].payload, TaskAssignedPayload)


def test_protocol_negotiate_returns_assignment_without_publishing() -> None:
    decision = asyncio.run(
        DefaultNegotiationEngine().negotiate(make_task(), [make_robot()], 2.0)
    )

    assert decision.status is NegotiationStatus.ASSIGNED
    assert decision.assignment is not None
    assert decision.assignment.robot_id == "robot-001"


def test_negotiation_without_candidates_is_unassigned_and_has_no_events() -> None:
    decision = asyncio.run(
        DefaultNegotiationEngine().decide(
            make_task(),
            [],
            2.0,
            event_context(),
        )
    )

    assert decision.outcome.status is NegotiationStatus.UNASSIGNED
    assert decision.outcome.assignment is None
    assert decision.outcome.bids == ()
    assert decision.events == ()


@pytest.mark.parametrize("validity", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_bid_validity_is_rejected(validity: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        DefaultNegotiationEngine(bid_validity_s=validity)


def test_invalid_observed_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        asyncio.run(
            DefaultNegotiationEngine().negotiate(make_task(), [make_robot()], float("nan"))
        )
