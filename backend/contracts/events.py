"""Canonical event envelopes and typed event payloads.

The event name is part of the payload discriminator and is repeated on the
envelope. Every event is immutable, versioned, and correlated to its cause.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import Field, TypeAdapter

from .models import (
    Bid,
    Conflict,
    ContractModel,
    DeadlockReport,
    FailureInfo,
    Position2D,
    RecoveryAction,
    RoutePlan,
    Task,
    TaskAssignment,
)

PayloadT = TypeVar("PayloadT", bound=ContractModel)


class EventType(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    NEGOTIATION_STARTED = "NEGOTIATION_STARTED"
    BID_SUBMITTED = "BID_SUBMITTED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_REASSIGNED = "TASK_REASSIGNED"
    ROUTE_REQUESTED = "ROUTE_REQUESTED"
    ROUTE_PLANNED = "ROUTE_PLANNED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    DEADLOCK_DETECTED = "DEADLOCK_DETECTED"
    ROUTE_REPLANNED = "ROUTE_REPLANNED"
    BATTERY_LOW = "BATTERY_LOW"
    ROBOT_FAILED = "ROBOT_FAILED"
    COMMUNICATION_LOST = "COMMUNICATION_LOST"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"


class EventPayload(ContractModel):
    """Base class for discriminated event payloads."""


class TaskCreatedPayload(EventPayload):
    event_type: Literal[EventType.TASK_CREATED] = EventType.TASK_CREATED
    task: Task


class NegotiationStartedPayload(EventPayload):
    event_type: Literal[EventType.NEGOTIATION_STARTED] = EventType.NEGOTIATION_STARTED
    task_id: str
    candidate_robot_ids: tuple[str, ...] = Field(min_length=1)
    expires_at_s: float = Field(ge=0)


class BidSubmittedPayload(EventPayload):
    event_type: Literal[EventType.BID_SUBMITTED] = EventType.BID_SUBMITTED
    bid: Bid


class TaskAssignedPayload(EventPayload):
    event_type: Literal[EventType.TASK_ASSIGNED] = EventType.TASK_ASSIGNED
    assignment: TaskAssignment


class TaskReassignedPayload(EventPayload):
    event_type: Literal[EventType.TASK_REASSIGNED] = EventType.TASK_REASSIGNED
    task_id: str
    previous_robot_id: str
    new_robot_id: str
    reason: str = Field(min_length=1, max_length=500)
    trigger_event_id: UUID | None = None


class RouteRequestedPayload(EventPayload):
    event_type: Literal[EventType.ROUTE_REQUESTED] = EventType.ROUTE_REQUESTED
    task_id: str
    robot_id: str
    origin: Position2D
    target: Position2D


class RoutePlannedPayload(EventPayload):
    event_type: Literal[EventType.ROUTE_PLANNED] = EventType.ROUTE_PLANNED
    route: RoutePlan


class ConflictDetectedPayload(EventPayload):
    event_type: Literal[EventType.CONFLICT_DETECTED] = EventType.CONFLICT_DETECTED
    conflict: Conflict


class DeadlockDetectedPayload(EventPayload):
    event_type: Literal[EventType.DEADLOCK_DETECTED] = EventType.DEADLOCK_DETECTED
    report: DeadlockReport


class RouteReplannedPayload(EventPayload):
    event_type: Literal[EventType.ROUTE_REPLANNED] = EventType.ROUTE_REPLANNED
    route: RoutePlan
    reason: str = Field(min_length=1, max_length=500)


class BatteryLowPayload(EventPayload):
    event_type: Literal[EventType.BATTERY_LOW] = EventType.BATTERY_LOW
    robot_id: str
    battery_percent: float = Field(ge=0, le=100)
    threshold_percent: float = Field(ge=0, le=100)
    estimated_range_m: float = Field(ge=0)


class RobotFailedPayload(EventPayload):
    event_type: Literal[EventType.ROBOT_FAILED] = EventType.ROBOT_FAILED
    robot_id: str
    failure: FailureInfo


class CommunicationLostPayload(EventPayload):
    event_type: Literal[EventType.COMMUNICATION_LOST] = EventType.COMMUNICATION_LOST
    robot_id: str
    last_contact_at_s: float = Field(ge=0)
    timeout_s: float = Field(gt=0)


class RecoveryStartedPayload(EventPayload):
    event_type: Literal[EventType.RECOVERY_STARTED] = EventType.RECOVERY_STARTED
    action: RecoveryAction


class TaskCompletedPayload(EventPayload):
    event_type: Literal[EventType.TASK_COMPLETED] = EventType.TASK_COMPLETED
    task_id: str
    robot_id: str
    started_at_s: float = Field(ge=0)
    completed_at_s: float = Field(ge=0)


class EventEnvelope(ContractModel, Generic[PayloadT]):
    event_id: UUID
    sequence: int = Field(ge=1)
    schema_version: Literal[1] = 1
    event_type: EventType
    producer: str = Field(min_length=1, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=128)
    occurred_at_s: float = Field(ge=0, allow_inf_nan=False)
    payload: PayloadT

    def model_post_init(self, _context: Any, /) -> None:
        if self.payload.event_type != self.event_type:
            raise ValueError("envelope event_type must match payload event_type")


DomainEvent: TypeAlias = Annotated[
    TaskCreatedPayload
    | NegotiationStartedPayload
    | BidSubmittedPayload
    | TaskAssignedPayload
    | TaskReassignedPayload
    | RouteRequestedPayload
    | RoutePlannedPayload
    | ConflictDetectedPayload
    | DeadlockDetectedPayload
    | RouteReplannedPayload
    | BatteryLowPayload
    | RobotFailedPayload
    | CommunicationLostPayload
    | RecoveryStartedPayload
    | TaskCompletedPayload,
    Field(discriminator="event_type"),
]

_EVENT_ADAPTER = TypeAdapter(DomainEvent)


def parse_event(data: object) -> DomainEvent:
    """Validate an untrusted serialized event against the canonical union."""

    return _EVENT_ADAPTER.validate_python(data)
