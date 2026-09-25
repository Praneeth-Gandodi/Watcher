"""Mutable per-robot simulation state.

The canonical ``Robot`` contract is frozen, which is right for the wire but
wrong for a simulation that rewrites a robot ten times a second. This module
keeps the authoritative mutable copy and converts to ``Robot`` only when a
snapshot is built, so the safety layer never fights Pydantic immutability.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.contracts.models import (
    CommunicationState,
    FailureInfo,
    Position2D,
    Robot,
    RobotCapability,
    RobotStatus,
)


@dataclass(slots=True)
class RobotState:
    """Authoritative mutable state for one simulated robot."""

    robot_id: str
    position: Position2D
    battery_percent: float
    capabilities: tuple[RobotCapability, ...]
    status: RobotStatus = RobotStatus.IDLE
    current_task_id: str | None = None
    communication_state: CommunicationState = CommunicationState.ONLINE
    failure: FailureInfo | None = None
    workload: int = 0
    speed_mps: float = 1.4
    last_updated_at_s: float = 0.0
    task_started_at_s: float | None = None
    blocked_since_s: float | None = None
    yield_count: int = 0
    charge_target_cell: tuple[int, int] | None = None
    # Seconds of work still owed at the destination. A task is travel *plus*
    # service: a robot that has arrived is loading, unloading, or inspecting,
    # and only then is the task done.
    service_remaining_s: float = 0.0
    service_total_s: float = 0.0

    def to_contract(self) -> Robot:
        """Project this state into the canonical immutable contract."""

        return Robot(
            robot_id=self.robot_id,
            position=self.position,
            battery_percent=round(self.battery_percent, 2),
            capabilities=self.capabilities,
            workload=self.workload,
            status=self.status,
            current_task_id=self.current_task_id,
            communication_state=self.communication_state,
            failure=self.failure,
            last_updated_at_s=round(self.last_updated_at_s, 3),
        )


@dataclass(slots=True)
class FleetProfile:
    """Speed, capability, and battery spread used when seeding a fleet.

    Speeds are higher than a real warehouse AMR would manage. That is a
    deliberate time compression: at 1.4 m/s a 120 m aisle crossing takes almost
    two minutes of simulated time, which is long enough that a reviewer watching
    a live demo sees a fleet that appears to stand still. The values are
    documented rather than tuned silently so the trade-off is visible.
    """

    min_speed_mps: float = 1.6
    max_speed_mps: float = 3.2
    min_battery_percent: float = 35.0
    max_battery_percent: float = 100.0
    capability_pool: tuple[RobotCapability, ...] = (
        RobotCapability.TRANSPORT,
        RobotCapability.PICK,
        RobotCapability.TUG,
        RobotCapability.INSPECT,
        RobotCapability.DELIVER,
    )
    min_capabilities: int = 1
    max_capabilities: int = 3
    extra: dict[str, float] = field(default_factory=dict)
