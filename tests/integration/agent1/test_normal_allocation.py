"""Integration scenario A: normal candidate bidding and allocation."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

from backend.contracts.events import EventType
from backend.contracts.fixtures import valid_robot, valid_task
from backend.contracts.models import NegotiationStatus, Position2D
from backend.negotiation.engine import DefaultNegotiationEngine
from backend.negotiation.events import DecisionEventContext


def event_ids() -> Iterator[UUID]:
    for suffix in range(1, 5):
        yield UUID(f"10000000-0000-4000-8000-{suffix:012d}")


def test_task_candidates_bid_and_lowest_cost_robot_is_assigned() -> None:
    robots = (
        valid_robot("robot-001"),
        valid_robot("robot-002").model_copy(
            update={"position": Position2D(x=42.0, y=36.0), "workload": 0}
        ),
    )
    engine = DefaultNegotiationEngine(bid_validity_s=5.0)
    context = DecisionEventContext(
        correlation_id="scenario-a",
        first_sequence=1,
        event_id_factory=event_ids().__next__,
    )

    decision = asyncio.run(
        engine.decide(valid_task(), robots, observed_at_s=1.0, event_context=context)
    )

    assert decision.outcome.status is NegotiationStatus.ASSIGNED
    assert decision.outcome.assignment is not None
    assert decision.outcome.assignment.robot_id == "robot-002"
    assert [event.event_type for event in decision.events] == [
        EventType.NEGOTIATION_STARTED,
        EventType.BID_SUBMITTED,
        EventType.BID_SUBMITTED,
        EventType.TASK_ASSIGNED,
    ]
    assert [event.sequence for event in decision.events] == [1, 2, 3, 4]
    assert decision.outcome.assignment.robot_id != "robot-001"
