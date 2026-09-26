"""Dynamic reassignment decisions triggered by Agent 2 observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.contracts.events import (
    BatteryLowPayload,
    CommunicationLostPayload,
    EventEnvelope,
    EventPayload,
    RobotFailedPayload,
    TaskReassignedPayload,
)
from backend.contracts.models import NegotiationOutcome, Robot, Task, TaskStatus
from backend.negotiation.engine import DefaultNegotiationEngine, NegotiationRound
from backend.negotiation.events import DecisionEventContext, DecisionEventFactory

ReassignmentTriggerPayload = (
    BatteryLowPayload | CommunicationLostPayload | RobotFailedPayload
)


@dataclass(frozen=True, slots=True)
class ReassignmentDecision:
    """Agent 1 result returned to the protected composition root."""

    outcome: NegotiationOutcome
    events: tuple[EventEnvelope[EventPayload], ...]


def reassignment_reason(payload: ReassignmentTriggerPayload) -> str:
    """Return a concise canonical reason for a reassignment event."""

    if isinstance(payload, RobotFailedPayload):
        return "assigned robot failed"
    if isinstance(payload, CommunicationLostPayload):
        return "assigned robot communication was lost"
    return "assigned robot battery margin is unsafe"


def _validate_trigger(trigger: EventEnvelope[object]) -> ReassignmentTriggerPayload:
    payload = trigger.payload
    if not isinstance(
        payload,
        (BatteryLowPayload, CommunicationLostPayload, RobotFailedPayload),
    ):
        raise TypeError("reassignment requires a battery, failure, or communication event")
    return payload


class ReassignmentService:
    """Run a replacement negotiation without treating reassignment as assignment."""

    def __init__(self, engine: DefaultNegotiationEngine | None = None) -> None:
        self._engine = engine or DefaultNegotiationEngine()

    def publish_manual_handover(
        self,
        *,
        task_id: str,
        previous_robot_id: str,
        new_robot_id: str,
        reason: str,
        observed_at_s: float,
        event_context: DecisionEventContext,
    ) -> EventEnvelope[object]:
        """Publish the record of a task moving to an operator-chosen robot.

        A manual assignment is still a coordination decision, so the canonical
        event is built here, in the decision layer, rather than in the HTTP
        adapter or the composition root. The Agent 2 runtime only moves robots
        and resolves conflicts; it never decides who owns a task.
        """

        factory = DecisionEventFactory(event_context)
        return factory.create(
            TaskReassignedPayload(
                task_id=task_id,
                previous_robot_id=previous_robot_id,
                new_robot_id=new_robot_id,
                reason=reason,
            ),
            observed_at_s,
        )

    async def reassign(
        self,
        *,
        task: Task,
        robots: Sequence[Robot],
        observed_at_s: float,
        trigger: EventEnvelope[object],
        event_context: DecisionEventContext,
    ) -> ReassignmentDecision:
        """Reassign an assigned task when a valid replacement bid exists."""

        return self.reassign_sync(
            task=task,
            robots=robots,
            observed_at_s=observed_at_s,
            trigger=trigger,
            event_context=event_context,
        )

    def reassign_sync(
        self,
        *,
        task: Task,
        robots: Sequence[Robot],
        observed_at_s: float,
        trigger: EventEnvelope[object],
        event_context: DecisionEventContext,
    ) -> ReassignmentDecision:
        """Synchronous reassignment core.

        Reassignment is deterministic and CPU-only, so the in-process
        composition root drives it directly. :meth:`reassign` is the async form
        of this method and delegates to it, keeping a single implementation.
        """

        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ValueError("a terminal task cannot be reassigned")
        if task.assigned_robot_id is None:
            raise ValueError("reassignment requires an existing task assignment")

        trigger_payload = _validate_trigger(trigger)
        if trigger_payload.robot_id != task.assigned_robot_id:
            raise ValueError("reassignment trigger must belong to the assigned robot")
        if observed_at_s < trigger.occurred_at_s:
            raise ValueError("reassignment cannot precede its trigger event")
        excluded_robot_ids = frozenset(
            {task.assigned_robot_id, trigger_payload.robot_id}
        )
        negotiation_round = self._engine.prepare(
            task,
            robots,
            observed_at_s,
            excluded_robot_ids=excluded_robot_ids,
        )
        outcome = self._engine.finish(negotiation_round)
        events = self._build_events(
            negotiation_round,
            outcome,
            trigger,
            trigger_payload,
            event_context,
        )
        return ReassignmentDecision(outcome=outcome, events=events)

    def _build_events(
        self,
        negotiation_round: NegotiationRound,
        outcome: NegotiationOutcome,
        trigger: EventEnvelope[object],
        trigger_payload: ReassignmentTriggerPayload,
        event_context: DecisionEventContext,
    ) -> tuple[EventEnvelope[EventPayload], ...]:
        events: list[EventEnvelope[EventPayload]] = []
        factory = DecisionEventFactory(event_context)
        if negotiation_round.candidates:
            events.extend(
                self._engine.round_events(negotiation_round, factory)
            )

        if outcome.assignment is None:
            return tuple(events)

        previous_robot_id = negotiation_round.task.assigned_robot_id
        if previous_robot_id is None:
            raise ValueError("reassignment lost the previous assignment")
        events.append(
            factory.create(
                TaskReassignedPayload(
                    task_id=outcome.task_id,
                    previous_robot_id=previous_robot_id,
                    new_robot_id=outcome.assignment.robot_id,
                    reason=reassignment_reason(trigger_payload),
                    trigger_event_id=trigger.event_id,
                ),
                negotiation_round.observed_at_s,
            )
        )
        return tuple(events)
