"""Deterministic valid fixtures shared by contract tests and demos."""

from __future__ import annotations

from uuid import UUID

from .commands import CreateTaskCommand
from .events import (
    BatteryLowPayload,
    BidSubmittedPayload,
    CommunicationLostPayload,
    ConflictDetectedPayload,
    DeadlockDetectedPayload,
    EventType,
    NegotiationStartedPayload,
    RecoveryStartedPayload,
    RouteReplannedPayload,
    RobotFailedPayload,
    RoutePlannedPayload,
    RouteRequestedPayload,
    TaskAssignedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskReassignedPayload,
)
from .models import (
    ActionStatus,
    Bid,
    CommunicationState,
    Conflict,
    ConflictKind,
    ConflictSeverity,
    DeadlockReport,
    FailureInfo,
    FailureKind,
    Position2D,
    RecoveryAction,
    RecoveryActionType,
    ResolutionStatus,
    Robot,
    RobotCapability,
    RobotStatus,
    RoutePlan,
    RouteStatus,
    Task,
    TaskAssignment,
    TaskStatus,
)

ORIGIN = Position2D(x=10.0, y=12.0)
TARGET = Position2D(x=44.0, y=38.0)
EVENT_ID = UUID("00000000-0000-4000-8000-000000000001")
CORRELATION_ID = "task-task-001"


def valid_robot(robot_id: str = "robot-001") -> Robot:
    return Robot(
        robot_id=robot_id,
        position=Position2D(x=10.0, y=12.0),
        battery_percent=78.5,
        capabilities=(RobotCapability.TRANSPORT, RobotCapability.PICK),
        workload=1,
        status=RobotStatus.IDLE,
        current_task_id=None,
        communication_state=CommunicationState.ONLINE,
        failure=None,
        last_updated_at_s=1.0,
    )


def valid_task(status: TaskStatus = TaskStatus.PENDING) -> Task:
    assigned_robot_id = "robot-001" if status is not TaskStatus.PENDING else None
    return Task(
        task_id="task-001",
        target=TARGET,
        priority=4,
        required_capabilities=(RobotCapability.TRANSPORT,),
        estimated_duration_s=90.0,
        status=status,
        assigned_robot_id=assigned_robot_id,
        created_at_s=0.0,
    )


def valid_bid() -> Bid:
    return Bid(
        bid_id="bid-001",
        robot_id="robot-001",
        task_id="task-001",
        total_cost=18.25,
        distance_cost=8.0,
        battery_cost=4.25,
        workload_cost=6.0,
        estimated_completion_time_s=102.5,
        created_at_s=1.0,
        valid_until_s=6.0,
    )


def valid_assignment() -> TaskAssignment:
    return TaskAssignment(
        task_id="task-001",
        robot_id="robot-001",
        bid_id="bid-001",
        assigned_at_s=2.0,
        reason="lowest eligible bid",
    )


def valid_failure() -> FailureInfo:
    return FailureInfo(
        kind=FailureKind.ACTUATOR,
        code="drive-failure",
        detected_at_s=12.0,
        detail="Left drive motor stopped responding.",
    )


def valid_route() -> RoutePlan:
    return RoutePlan(
        route_id="route-001",
        robot_id="robot-001",
        task_id="task-001",
        waypoints=(ORIGIN, Position2D(x=24.0, y=24.0), TARGET),
        strategy="grid-astar",
        status=RouteStatus.ACTIVE,
        version=1,
        planned_at_s=2.5,
    )


def valid_recovery_action() -> RecoveryAction:
    return RecoveryAction(
        action_id="recovery-001",
        action_type=RecoveryActionType.REPLAN,
        target_robot_ids=("robot-001", "robot-002"),
        affected_task_ids=("task-001", "task-002"),
        reason="predicted right-of-way conflict",
        started_at_s=15.0,
        status=ActionStatus.ACTIVE,
    )


def valid_event_payloads() -> tuple[object, ...]:
    bid = valid_bid()
    assignment = valid_assignment()
    route = valid_route()
    failure = valid_failure()
    conflict = Conflict(
        conflict_id="conflict-001",
        kind=ConflictKind.RIGHT_OF_WAY,
        severity=ConflictSeverity.WARNING,
        robot_ids=("robot-001", "robot-002"),
        task_ids=("task-001", "task-002"),
        position=Position2D(x=24.0, y=24.0),
        status=ResolutionStatus.OPEN,
        detected_at_s=14.0,
    )
    deadlock = DeadlockReport(
        deadlock_id="deadlock-001",
        cycle_robot_ids=("robot-001", "robot-002"),
        blocked_task_ids=("task-001", "task-002"),
        detected_at_s=16.0,
        confidence=0.95,
    )
    return (
        TaskCreatedPayload(task=valid_task()),
        NegotiationStartedPayload(
            task_id="task-001",
            candidate_robot_ids=("robot-001", "robot-002"),
            expires_at_s=8.0,
        ),
        BidSubmittedPayload(bid=bid),
        TaskAssignedPayload(assignment=assignment),
        TaskReassignedPayload(
            task_id="task-001",
            previous_robot_id="robot-001",
            new_robot_id="robot-002",
            reason="assigned robot failed",
            trigger_event_id=EVENT_ID,
        ),
        RouteRequestedPayload(
            task_id="task-001",
            robot_id="robot-001",
            origin=ORIGIN,
            target=TARGET,
        ),
        RoutePlannedPayload(route=route),
        ConflictDetectedPayload(conflict=conflict),
        DeadlockDetectedPayload(report=deadlock),
        RouteReplannedPayload(route=route, reason="blocked segment"),
        BatteryLowPayload(
            robot_id="robot-001",
            battery_percent=18.0,
            threshold_percent=20.0,
            estimated_range_m=125.0,
        ),
        RobotFailedPayload(robot_id="robot-001", failure=failure),
        CommunicationLostPayload(
            robot_id="robot-001",
            last_contact_at_s=10.0,
            timeout_s=3.0,
        ),
        RecoveryStartedPayload(action=valid_recovery_action()),
        TaskCompletedPayload(
            task_id="task-001",
            robot_id="robot-001",
            started_at_s=3.0,
            completed_at_s=95.0,
        ),
    )


def valid_create_task_command() -> CreateTaskCommand:
    return CreateTaskCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000010"),
        issued_at_s=0.0,
        task=valid_task(),
    )


EXPECTED_EVENT_TYPES = frozenset(EventType)


def expected_event_types() -> frozenset[EventType]:
    return EXPECTED_EVENT_TYPES
