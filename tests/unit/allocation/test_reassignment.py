from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from backend.contracts.events import (
    BatteryLowPayload,
    CommunicationLostPayload,
    EventEnvelope,
    EventType,
    RobotFailedPayload,
    TaskAssignedPayload,
    TaskReassignedPayload,
)
from backend.contracts.models import (
    CommunicationState,
    NegotiationStatus,
    Position2D,
    Robot,
    RobotCapability,
    RobotStatus,
    Task,
    TaskAssignment,
    TaskStatus,
)
from backend.negotiation.events import DecisionEventContext
from backend.negotiation.reassignment import ReassignmentService

TRIGGER_EVENT_ID = UUID("00000000-0000-4000-8000-000000000099")
ASSIGNED_TASK_ID = "task-001"


def assigned_task() -> Task:
    return Task(
        task_id=ASSIGNED_TASK_ID,
        target=Position2D(x=3.0, y=4.0),
        priority=4,
        required_capabilities=(RobotCapability.TRANSPORT,),
        estimated_duration_s=20.0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="robot-001",
        created_at_s=0.0,
    )


def previous_robot() -> Robot:
    return Robot(
        robot_id="robot-001",
        position=Position2D(x=0.0, y=0.0),
        battery_percent=90.0,
        capabilities=(RobotCapability.TRANSPORT,),
        workload=1,
        status=RobotStatus.ACTIVE,
        current_task_id=ASSIGNED_TASK_ID,
        communication_state=CommunicationState.ONLINE,
        failure=None,
        last_updated_at_s=1.0,
    )


def replacement_robot() -> Robot:
    return Robot(
        robot_id="robot-002",
        position=Position2D(x=1.0, y=1.0),
        battery_percent=90.0,
        capabilities=(RobotCapability.TRANSPORT,),
        workload=1,
        status=RobotStatus.IDLE,
        current_task_id=None,
        communication_state=CommunicationState.ONLINE,
        failure=None,
        last_updated_at_s=1.0,
    )


def trigger(payload: object) -> EventEnvelope[object]:
    return EventEnvelope(
        event_id=TRIGGER_EVENT_ID,
        sequence=20,
        producer="agent-2-safety",
        correlation_id=ASSIGNED_TASK_ID,
        occurred_at_s=2.0,
        event_type=payload.event_type,
        payload=payload,
    )


def context() -> DecisionEventContext:
    event_id = iter(
        [
            UUID("00000000-0000-4000-8000-000000000101"),
            UUID("00000000-0000-4000-8000-000000000102"),
            UUID("00000000-0000-4000-8000-000000000103"),
        ]
    )
    return DecisionEventContext(
        correlation_id=ASSIGNED_TASK_ID,
        first_sequence=21,
        event_id_factory=lambda: next(event_id),
    )


@pytest.mark.parametrize(
    "payload",
    [
        RobotFailedPayload(
            robot_id="robot-001",
            failure={
                "kind": "actuator",
                "code": "drive-failure",
                "detected_at_s": 2.0,
            },
        ),
        CommunicationLostPayload(
            robot_id="robot-001",
            last_contact_at_s=1.0,
            timeout_s=1.0,
        ),
        BatteryLowPayload(
            robot_id="robot-001",
            battery_percent=10.0,
            threshold_percent=20.0,
            estimated_range_m=5.0,
        ),
    ],
)
def test_reassignment_uses_replacement_and_trigger(payload: object) -> None:
    service = ReassignmentService()

    decision = asyncio.run(
        service.reassign(
            task=assigned_task(),
            robots=[previous_robot(), replacement_robot()],
            observed_at_s=2.0,
            trigger=trigger(payload),
            event_context=context(),
        )
    )

    assert decision.outcome.status is NegotiationStatus.ASSIGNED
    assert decision.outcome.assignment is not None
    assert decision.outcome.assignment.robot_id == "robot-002"
    assert [event.event_type for event in decision.events] == [
        EventType.NEGOTIATION_STARTED,
        EventType.BID_SUBMITTED,
        EventType.TASK_REASSIGNED,
    ]
    reassigned = decision.events[-1]
    assert isinstance(reassigned.payload, TaskReassignedPayload)
    assert reassigned.payload.previous_robot_id == "robot-001"
    assert reassigned.payload.new_robot_id == "robot-002"
    assert reassigned.payload.trigger_event_id == TRIGGER_EVENT_ID
    assert EventType.TASK_ASSIGNED not in {
        event.event_type for event in decision.events
    }


def test_reassignment_without_replacement_is_unassigned() -> None:
    decision = asyncio.run(
        ReassignmentService().reassign(
            task=assigned_task(),
            robots=[previous_robot()],
            observed_at_s=2.0,
            trigger=trigger(
                CommunicationLostPayload(
                    robot_id="robot-001",
                    last_contact_at_s=1.0,
                    timeout_s=1.0,
                )
            ),
            event_context=context(),
        )
    )

    assert decision.outcome.status is NegotiationStatus.UNASSIGNED
    assert decision.outcome.assignment is None
    assert decision.events == ()


def test_reassignment_requires_existing_assignment() -> None:
    with pytest.raises(ValueError, match="existing"):
        asyncio.run(
            ReassignmentService().reassign(
                task=assigned_task().model_copy(
                    update={"assigned_robot_id": None}
                ),
                robots=[replacement_robot()],
                observed_at_s=2.0,
                trigger=trigger(
                    CommunicationLostPayload(
                        robot_id="robot-001",
                        last_contact_at_s=1.0,
                        timeout_s=1.0,
                    )
                ),
                event_context=context(),
            )
        )


def test_reassignment_trigger_must_belong_to_assigned_robot() -> None:
    with pytest.raises(ValueError, match="assigned robot"):
        asyncio.run(
            ReassignmentService().reassign(
                task=assigned_task(),
                robots=[replacement_robot()],
                observed_at_s=2.0,
                trigger=trigger(
                    CommunicationLostPayload(
                        robot_id="robot-002",
                        last_contact_at_s=1.0,
                        timeout_s=1.0,
                    )
                ),
                event_context=context(),
            )
        )


def test_reassignment_cannot_precede_trigger() -> None:
    with pytest.raises(ValueError, match="precede"):
        asyncio.run(
            ReassignmentService().reassign(
                task=assigned_task(),
                robots=[replacement_robot()],
                observed_at_s=1.0,
                trigger=trigger(
                    CommunicationLostPayload(
                        robot_id="robot-001",
                        last_contact_at_s=0.5,
                        timeout_s=1.0,
                    )
                ),
                event_context=context(),
            )
        )


def test_reassignment_rejects_non_reassignment_trigger() -> None:
    payload = TaskAssignedPayload(
        assignment=TaskAssignment(
            task_id=ASSIGNED_TASK_ID,
            robot_id="robot-001",
            bid_id="bid-001",
            assigned_at_s=1.0,
            reason="existing assignment",
        )
    )
    event = trigger(payload)

    with pytest.raises(TypeError, match="reassignment"):
        asyncio.run(
            ReassignmentService().reassign(
                task=assigned_task(),
                robots=[replacement_robot()],
                observed_at_s=2.0,
                trigger=event,
                event_context=context(),
            )
        )
