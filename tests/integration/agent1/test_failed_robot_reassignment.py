"""Integration scenario B: failure causes deterministic task reassignment."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

from backend.contracts.events import (
    EventEnvelope,
    EventType,
    RobotFailedPayload,
    TaskReassignedPayload,
)
from backend.contracts.fixtures import valid_robot, valid_task
from backend.contracts.models import (
    NegotiationStatus,
    RobotStatus,
    TaskStatus,
)
from backend.negotiation.events import DecisionEventContext
from backend.negotiation.reassignment import ReassignmentService

TRIGGER_EVENT_ID = UUID("20000000-0000-4000-8000-000000000001")


def event_ids() -> Iterator[UUID]:
    for suffix in range(2, 5):
        yield UUID(f"20000000-0000-4000-8000-{suffix:012d}")


def test_failed_assigned_robot_is_replaced_by_eligible_candidate() -> None:
    task = valid_task(TaskStatus.ASSIGNED)
    previous = valid_robot("robot-001").model_copy(
        update={
            "status": RobotStatus.ACTIVE,
            "current_task_id": task.task_id,
        }
    )
    replacement = valid_robot("robot-002")
    trigger = EventEnvelope(
        event_id=TRIGGER_EVENT_ID,
        sequence=1,
        producer="agent-2-safety",
        correlation_id=task.task_id,
        occurred_at_s=2.0,
        event_type=EventType.ROBOT_FAILED,
        payload=RobotFailedPayload(
            robot_id=previous.robot_id,
            failure={
                "kind": "actuator",
                "code": "drive-failure",
                "detected_at_s": 2.0,
            },
        ),
    )
    context = DecisionEventContext(
        correlation_id=task.task_id,
        first_sequence=2,
        event_id_factory=event_ids().__next__,
    )

    decision = asyncio.run(
        ReassignmentService().reassign(
            task=task,
            robots=[previous, replacement],
            observed_at_s=2.0,
            trigger=trigger,
            event_context=context,
        )
    )

    assert decision.outcome.status is NegotiationStatus.ASSIGNED
    assert decision.outcome.assignment is not None
    assert decision.outcome.assignment.robot_id == "robot-002"
    assert decision.outcome.assignment.robot_id != previous.robot_id
    assert [event.event_type for event in decision.events] == [
        EventType.NEGOTIATION_STARTED,
        EventType.BID_SUBMITTED,
        EventType.TASK_REASSIGNED,
    ]
    reassigned = decision.events[-1]
    assert isinstance(reassigned.payload, TaskReassignedPayload)
    assert reassigned.payload.task_id == task.task_id
    assert reassigned.payload.previous_robot_id == "robot-001"
    assert reassigned.payload.new_robot_id == "robot-002"
    assert reassigned.payload.trigger_event_id == TRIGGER_EVENT_ID
