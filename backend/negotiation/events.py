"""Request-local construction of canonical Agent 1 event envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import UUID, uuid4

from backend.contracts.events import EventEnvelope, EventPayload


@dataclass(frozen=True, slots=True)
class DecisionEventContext:
    """Correlation and sequencing metadata supplied by the composition root."""

    correlation_id: str
    first_sequence: int
    producer: str = "agent-1-negotiation"
    event_id_factory: Callable[[], UUID] = field(default=uuid4, repr=False)


class DecisionEventFactory:
    """Create monotonically sequenced events for one decision request."""

    def __init__(self, context: DecisionEventContext) -> None:
        if context.first_sequence < 1:
            raise ValueError("first_sequence must be at least 1")
        self._context = context
        self._next_sequence = context.first_sequence

    def create(
        self,
        payload: EventPayload,
        occurred_at_s: float,
    ) -> EventEnvelope[EventPayload]:
        event = EventEnvelope(
            event_id=self._context.event_id_factory(),
            sequence=self._next_sequence,
            producer=self._context.producer,
            correlation_id=self._context.correlation_id,
            occurred_at_s=occurred_at_s,
            event_type=payload.event_type,
            payload=payload,
        )
        self._next_sequence += 1
        return event
