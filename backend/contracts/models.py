"""Canonical domain and transport models.

These models are protected shared contracts. Their behavior is intentionally
limited to validation and serialization; algorithms live in owned subsystems.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


class ContractModel(BaseModel):
    """Base configuration for strict, immutable wire models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RobotCapability(StrEnum):
    TRANSPORT = "transport"
    PICK = "pick"
    TUG = "tug"
    INSPECT = "inspect"
    DELIVER = "deliver"


class RobotStatus(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    BLOCKED = "blocked"
    CHARGING = "charging"
    DEGRADED = "degraded"
    FAILED = "failed"
    OFFLINE = "offline"


class CommunicationState(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    LOST = "lost"


class FailureKind(StrEnum):
    ACTUATOR = "actuator"
    SENSOR = "sensor"
    COMPUTE = "compute"
    COMMUNICATION = "communication"
    BATTERY = "battery"
    OTHER = "other"


class TaskStatus(StrEnum):
    PENDING = "pending"
    NEGOTIATING = "negotiating"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    RECOVERY = "recovery"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RouteStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    REPLANNED = "replanned"
    COMPLETED = "completed"
    INVALID = "invalid"


class ConflictKind(StrEnum):
    COLLISION_RISK = "collision_risk"
    RIGHT_OF_WAY = "right_of_way"
    RESOURCE = "resource"


class ConflictSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ResolutionStatus(StrEnum):
    OPEN = "open"
    RESOLVING = "resolving"
    RESOLVED = "resolved"


class RecoveryActionType(StrEnum):
    REPLAN = "replan"
    YIELD = "yield"
    TASK_MIGRATION = "task_migration"
    PRIORITY_CHANGE = "priority_change"
    RETURN_TO_CHARGER = "return_to_charger"
    ISOLATE = "isolate"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NegotiationStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"


class GridCellType(StrEnum):
    FREE = "free"
    OBSTACLE = "obstacle"
    RESOURCE = "resource"
    CHARGING = "charging"
    WORKSTATION = "workstation"
    DEADZONE = "deadzone"


class GridCell(ContractModel):
    cell_x: int = Field(ge=0)
    cell_y: int = Field(ge=0)
    cell_type: GridCellType


class WorldState(ContractModel):
    """Static/dynamic 2D world geometry consumed by safety and dashboard."""

    width_m: float = Field(gt=0, allow_inf_nan=False)
    height_m: float = Field(gt=0, allow_inf_nan=False)
    cell_size_m: float = Field(gt=0, allow_inf_nan=False)
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    cells: tuple[GridCell, ...] = ()
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_cell_coordinates(self) -> WorldState:
        for cell in self.cells:
            if cell.cell_x >= self.columns or cell.cell_y >= self.rows:
                raise ValueError("grid cell coordinates must be inside world bounds")
        return self


class Position2D(ContractModel):
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)


class FailureInfo(ContractModel):
    kind: FailureKind
    code: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER_PATTERN)
    detected_at_s: float = Field(ge=0, allow_inf_nan=False)
    detail: str | None = Field(default=None, max_length=500)


class Robot(ContractModel):
    robot_id: str = Field(pattern=IDENTIFIER_PATTERN)
    position: Position2D
    battery_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    capabilities: tuple[RobotCapability, ...] = ()
    workload: int = Field(default=0, ge=0)
    status: RobotStatus
    current_task_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    communication_state: CommunicationState
    failure: FailureInfo | None = None
    last_updated_at_s: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_failure_state(self) -> Robot:
        if self.status is RobotStatus.FAILED and self.failure is None:
            raise ValueError("a failed robot must include failure information")
        if self.failure is not None and self.status not in {
            RobotStatus.DEGRADED,
            RobotStatus.FAILED,
            RobotStatus.OFFLINE,
        }:
            raise ValueError("failure information requires a degraded, failed, or offline robot")
        return self


class Task(ContractModel):
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    target: Position2D
    priority: int = Field(ge=1, le=5)
    required_capabilities: tuple[RobotCapability, ...] = ()
    estimated_duration_s: float = Field(gt=0, allow_inf_nan=False)
    status: TaskStatus
    assigned_robot_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    created_at_s: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("required_capabilities")
    @classmethod
    def unique_capabilities(cls, value: tuple[RobotCapability, ...]) -> tuple[RobotCapability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required_capabilities must not contain duplicates")
        return value


class Bid(ContractModel):
    bid_id: str = Field(pattern=IDENTIFIER_PATTERN)
    robot_id: str = Field(pattern=IDENTIFIER_PATTERN)
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    total_cost: float = Field(ge=0, allow_inf_nan=False)
    distance_cost: float = Field(ge=0, allow_inf_nan=False)
    battery_cost: float = Field(ge=0, allow_inf_nan=False)
    workload_cost: float = Field(ge=0, allow_inf_nan=False)
    estimated_completion_time_s: float = Field(gt=0, allow_inf_nan=False)
    created_at_s: float = Field(ge=0, allow_inf_nan=False)
    valid_until_s: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_validity_window(self) -> Bid:
        if self.valid_until_s <= self.created_at_s:
            raise ValueError("valid_until_s must be later than created_at_s")
        return self


class RoutePlan(ContractModel):
    route_id: str = Field(pattern=IDENTIFIER_PATTERN)
    robot_id: str = Field(pattern=IDENTIFIER_PATTERN)
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    waypoints: tuple[Position2D, ...] = Field(min_length=2)
    strategy: str = Field(min_length=1, max_length=64)
    status: RouteStatus
    version: int = Field(ge=1)
    planned_at_s: float = Field(ge=0, allow_inf_nan=False)


class Conflict(ContractModel):
    conflict_id: str = Field(pattern=IDENTIFIER_PATTERN)
    kind: ConflictKind
    severity: ConflictSeverity
    robot_ids: tuple[str, ...] = Field(min_length=2)
    task_ids: tuple[str, ...] = ()
    position: Position2D
    status: ResolutionStatus
    detected_at_s: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("robot_ids")
    @classmethod
    def unique_robot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("conflict robot_ids must be unique")
        return value


class DeadlockReport(ContractModel):
    deadlock_id: str = Field(pattern=IDENTIFIER_PATTERN)
    cycle_robot_ids: tuple[str, ...] = Field(min_length=2)
    blocked_task_ids: tuple[str, ...] = ()
    detected_at_s: float = Field(ge=0, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("cycle_robot_ids")
    @classmethod
    def unique_cycle_robots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("deadlock cycle_robot_ids must be unique")
        return value


class RecoveryAction(ContractModel):
    action_id: str = Field(pattern=IDENTIFIER_PATTERN)
    action_type: RecoveryActionType
    target_robot_ids: tuple[str, ...] = Field(min_length=1)
    affected_task_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=500)
    started_at_s: float = Field(ge=0, allow_inf_nan=False)
    status: ActionStatus


class TaskAssignment(ContractModel):
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    robot_id: str = Field(pattern=IDENTIFIER_PATTERN)
    bid_id: str = Field(pattern=IDENTIFIER_PATTERN)
    assigned_at_s: float = Field(ge=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=500)


class NegotiationOutcome(ContractModel):
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    status: NegotiationStatus
    bids: tuple[Bid, ...] = ()
    assignment: TaskAssignment | None = None
    started_at_s: float = Field(ge=0, allow_inf_nan=False)
    completed_at_s: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_assignment(self) -> NegotiationOutcome:
        if self.status is NegotiationStatus.ASSIGNED and self.assignment is None:
            raise ValueError("assigned negotiations require an assignment")
        if self.status is not NegotiationStatus.ASSIGNED and self.assignment is not None:
            raise ValueError("only assigned negotiations may include an assignment")
        return self


class SafetyDecision(ContractModel):
    allowed: bool
    reason: str = Field(min_length=1, max_length=500)
    route: RoutePlan | None = None
    recovery_action: RecoveryAction | None = None


class SystemMetrics(ContractModel):
    active_robots: int = Field(ge=0)
    failed_robots: int = Field(ge=0)
    communication_lost_robots: int = Field(ge=0)
    pending_tasks: int = Field(ge=0)
    completed_tasks: int = Field(ge=0)
    open_conflicts: int = Field(ge=0)
    detected_deadlocks: int = Field(ge=0)
    task_reassignments: int = Field(ge=0)
    average_battery_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    average_allocation_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    event_throughput_per_s: float = Field(ge=0, allow_inf_nan=False)
    controller_available: bool
    extra_metrics: dict[str, float] = Field(default_factory=dict)


class SimulationSnapshot(ContractModel):
    simulation_time_s: float = Field(ge=0, allow_inf_nan=False)
    revision: int = Field(ge=0)
    last_event_sequence: int = Field(ge=0)
    controller_available: bool
    world: WorldState
    robots: tuple[Robot, ...] = ()
    tasks: tuple[Task, ...] = ()
    routes: tuple[RoutePlan, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    metrics: SystemMetrics

    @model_validator(mode="after")
    def validate_event_cursor(self) -> SimulationSnapshot:
        if self.last_event_sequence < self.revision:
            raise ValueError("last_event_sequence cannot be lower than state revision")
        return self


def dump_model(model: BaseModel) -> dict[str, Any]:
    """Return a JSON-compatible representation for fixtures and diagnostics."""

    return model.model_dump(mode="json")
