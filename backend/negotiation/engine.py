"""Agent 1 negotiation rounds, outcomes, and decision events."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from backend.allocation.allocator import create_assignment, select_winning_bid
from backend.contracts.events import (
    BidSubmittedPayload,
    EventEnvelope,
    EventPayload,
    NegotiationStartedPayload,
    TaskAssignedPayload,
)
from backend.contracts.models import (
    Bid,
    NegotiationOutcome,
    NegotiationStatus,
    Robot,
    Task,
)
from backend.negotiation.eligibility import discover_eligible_robots
from backend.negotiation.events import (
    DecisionEventContext,
    DecisionEventFactory,
)
from backend.negotiation.scoring import (
    BidIdFactory,
    DeterministicBidIdFactory,
    create_bid,
)


@dataclass(frozen=True, slots=True)
class NegotiationRound:
    """Immutable internal state shared by negotiation and event production."""

    task: Task
    observed_at_s: float
    candidates: tuple[Robot, ...]
    bids: tuple[Bid, ...]
    expires_at_s: float


@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    """Outcome and canonical events returned by one negotiation request."""

    outcome: NegotiationOutcome
    events: tuple[EventEnvelope[EventPayload], ...]


class DefaultNegotiationEngine:
    """Run deterministic in-process peer negotiations for a task."""

    def __init__(
        self,
        *,
        bid_validity_s: float = 5.0,
        bid_id_factory: BidIdFactory | None = None,
    ) -> None:
        if not isfinite(bid_validity_s) or bid_validity_s <= 0:
            raise ValueError("bid_validity_s must be positive")
        self._bid_validity_s = bid_validity_s
        self._bid_id_factory = bid_id_factory or DeterministicBidIdFactory()

    def prepare(
        self,
        task: Task,
        candidate_robots: Sequence[object],
        observed_at_s: float,
        *,
        excluded_robot_ids: frozenset[str] = frozenset(),
    ) -> NegotiationRound:
        """Discover candidates and create their canonical bids."""

        candidates = discover_eligible_robots(
            task,
            candidate_robots,
            observed_at_s,
            excluded_robot_ids=excluded_robot_ids,
        )
        expires_at_s = observed_at_s + self._bid_validity_s
        bids = tuple(
            create_bid(
                task,
                robot,
                observed_at_s,
                expires_at_s,
                self._bid_id_factory,
            )
            for robot in candidates
        )
        return NegotiationRound(
            task=task,
            observed_at_s=observed_at_s,
            candidates=candidates,
            bids=bids,
            expires_at_s=expires_at_s,
        )

    def finish(self, negotiation_round: NegotiationRound) -> NegotiationOutcome:
        """Select a winner and build the canonical negotiation outcome."""

        empty_outcome = NegotiationOutcome(
            task_id=negotiation_round.task.task_id,
            status=NegotiationStatus.UNASSIGNED,
            bids=negotiation_round.bids,
            started_at_s=negotiation_round.observed_at_s,
            completed_at_s=negotiation_round.observed_at_s,
        )
        winning_bid = select_winning_bid(negotiation_round.bids, empty_outcome)
        if winning_bid is None:
            return empty_outcome
        return NegotiationOutcome(
            task_id=negotiation_round.task.task_id,
            status=NegotiationStatus.ASSIGNED,
            bids=negotiation_round.bids,
            assignment=create_assignment(empty_outcome, winning_bid),
            started_at_s=negotiation_round.observed_at_s,
            completed_at_s=negotiation_round.observed_at_s,
        )

    def round_events(
        self,
        negotiation_round: NegotiationRound,
        factory: DecisionEventFactory,
    ) -> list[EventEnvelope[EventPayload]]:
        """Create started and bid-submitted events for a non-empty round."""

        if not negotiation_round.candidates:
            return []
        events: list[EventEnvelope[EventPayload]] = [
            factory.create(
                NegotiationStartedPayload(
                    task_id=negotiation_round.task.task_id,
                    candidate_robot_ids=tuple(
                        robot.robot_id for robot in negotiation_round.candidates
                    ),
                    expires_at_s=negotiation_round.expires_at_s,
                ),
                negotiation_round.observed_at_s,
            )
        ]
        events.extend(
            factory.create(
                BidSubmittedPayload(bid=bid),
                negotiation_round.observed_at_s,
            )
            for bid in negotiation_round.bids
        )
        return events

    async def negotiate(
        self,
        task: Task,
        candidate_robots: Sequence[object],
        observed_at_s: float,
    ) -> NegotiationOutcome:
        """Implement the canonical ``NegotiationEngine`` protocol."""

        return self.finish(self.prepare(task, candidate_robots, observed_at_s))

    def decide_sync(
        self,
        task: Task,
        candidate_robots: Sequence[object],
        observed_at_s: float,
        event_context: DecisionEventContext,
    ) -> NegotiationDecision:
        """Synchronous decision core.

        The negotiation itself is CPU-only and deterministic, so the in-process
        composition root drives it directly. :meth:`decide` is the async form of
        this method and delegates to it, keeping a single implementation.
        """

        negotiation_round = self.prepare(task, candidate_robots, observed_at_s)
        outcome = self.finish(negotiation_round)
        factory = DecisionEventFactory(event_context)
        events = self.round_events(negotiation_round, factory)
        if outcome.assignment is not None:
            events.append(
                factory.create(
                    TaskAssignedPayload(assignment=outcome.assignment),
                    observed_at_s,
                )
            )
        return NegotiationDecision(outcome=outcome, events=tuple(events))

    async def decide(
        self,
        task: Task,
        candidate_robots: Sequence[object],
        observed_at_s: float,
        event_context: DecisionEventContext,
    ) -> NegotiationDecision:
        """Run a negotiation and return its outcome and canonical events."""

        return self.decide_sync(
            task, candidate_robots, observed_at_s, event_context
        )
