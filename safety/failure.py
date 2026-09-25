"""Robot failure, communication loss, and restoration handling for Agent 2.

This module holds the authoritative view of robot health. It decides *when* a
robot is considered failed or unreachable, and it is the only place that turns
a raw command into a health transition, so the runtime and the dashboard can
never disagree about whether a robot is alive.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    FailureKind,
    Robot,
    RobotStatus,
)


@dataclass(frozen=True, slots=True)
class HealthVerdict:
    """Whether a robot is still usable, and why not when it is not."""

    usable: bool
    status: RobotStatus
    communication_state: CommunicationState
    failure: FailureInfo | None
    reason: str


class FailureRegistry:
    """Tracks injected failures and communication loss with their timeouts."""

    def __init__(self, *, communication_timeout_s: float = 3.0) -> None:
        if communication_timeout_s <= 0:
            raise ValueError("communication_timeout_s must be positive")
        self.communication_timeout_s = communication_timeout_s
        self.failures: dict[str, FailureInfo] = {}
        self.silenced_until_s: dict[str, float] = {}
        self.last_contact_at_s: dict[str, float] = {}

    def inject_failure(
        self, robot_id: str, failure: FailureInfo, *, now_s: float
    ) -> FailureInfo:
        self.failures[robot_id] = failure
        self.last_contact_at_s.setdefault(robot_id, now_s)
        return failure

    def inject_communication_loss(self, robot_id: str, *, now_s: float, timeout_s: float) -> float:
        self.silenced_until_s[robot_id] = now_s + max(timeout_s, 0.0)
        self.last_contact_at_s[robot_id] = now_s
        return self.silenced_until_s[robot_id]

    def restore(self, robot_id: str) -> bool:
        """Clear failure and communication loss. Returns whether anything changed."""

        changed = robot_id in self.failures or robot_id in self.silenced_until_s
        self.failures.pop(robot_id, None)
        self.silenced_until_s.pop(robot_id, None)
        self.last_contact_at_s.setdefault(robot_id, 0.0)
        return changed

    def is_silenced(self, robot_id: str, *, now_s: float) -> bool:
        until = self.silenced_until_s.get(robot_id)
        return until is not None and now_s < until

    def silence_remaining_s(self, robot_id: str, *, now_s: float) -> float:
        until = self.silenced_until_s.get(robot_id)
        if until is None:
            return 0.0
        return max(0.0, until - now_s)

    def contact_lost_for_s(self, robot_id: str, *, now_s: float) -> float:
        last = self.last_contact_at_s.get(robot_id)
        if last is None:
            return 0.0
        return max(0.0, now_s - last)

    def is_settled(self, robot_id: str, battery_percent: float) -> bool:
        """Whether a robot cannot change state this tick without intervention.

        A robot with no injected failure, no active communication-loss window,
        and energy remaining is nominal by definition, so the runtime can skip
        building its contract model purely to re-derive that. At 500 robots this
        fast path removes almost all of the per-tick health cost.
        """

        if robot_id in self.failures:
            return False
        until = self.silenced_until_s.get(robot_id)
        if until is not None:
            return False
        return battery_percent > 0.0

    def evaluate(
        self,
        robot_id: str,
        battery_percent: float,
        status: RobotStatus,
        communication_state: CommunicationState,
        *,
        now_s: float,
    ) -> HealthVerdict:
        """Decide a robot's authoritative health for this tick.

        The observed values are passed in rather than a ``Robot`` so the verdict
        is based on the simulation's own numbers instead of a wire projection
        that rounds energy to two decimal places.

        Precedence is deliberate: an explicit failure beats communication loss,
        and communication loss beats normal operation. A robot that cannot be
        reached is treated as degraded rather than failed, because the failure
        may be the radio rather than the machine — and the two need different
        recovery.
        """

        failure = self.failures.get(robot_id)
        if failure is not None:
            return HealthVerdict(
                usable=False,
                status=RobotStatus.FAILED,
                communication_state=communication_state,
                failure=failure,
                reason=f"failure {failure.kind}/{failure.code} is active",
            )

        if self.is_silenced(robot_id, now_s=now_s):
            return HealthVerdict(
                usable=False,
                status=RobotStatus.DEGRADED,
                communication_state=CommunicationState.LOST,
                failure=None,
                reason="peer heartbeat timed out",
            )

        if battery_percent <= 0.0:
            return HealthVerdict(
                usable=False,
                status=RobotStatus.OFFLINE,
                communication_state=CommunicationState.ONLINE,
                failure=FailureInfo(
                    kind=FailureKind.BATTERY,
                    code="battery-depleted",
                    detected_at_s=round(now_s, 3),
                    detail="battery reached zero percent",
                ),
                reason="battery depleted",
            )

        return HealthVerdict(
            usable=True,
            status=status,
            communication_state=CommunicationState.ONLINE,
            failure=None,
            reason="nominal",
        )


def degraded_failure(
    kind: FailureKind,
    code: str,
    *,
    now_s: float,
    detail: str | None = None,
) -> FailureInfo:
    """Build a non-fatal `FailureInfo` for a degraded but running robot."""

    return FailureInfo(
        kind=kind,
        code=code,
        detected_at_s=round(now_s, 3),
        detail=detail,
    )
