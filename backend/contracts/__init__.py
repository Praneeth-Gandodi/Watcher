"""Protected interoperability contracts for the Watcher fleet."""

from .commands import ControlCommand, parse_command
from .events import DomainEvent, EventEnvelope, parse_event
from .models import (
    Bid,
    Conflict,
    DeadlockReport,
    FailureInfo,
    Position2D,
    RecoveryAction,
    Robot,
    RoutePlan,
    SimulationSnapshot,
    SystemMetrics,
    Task,
)

__all__ = [
    "Bid",
    "Conflict",
    "ControlCommand",
    "DeadlockReport",
    "DomainEvent",
    "EventEnvelope",
    "FailureInfo",
    "Position2D",
    "RecoveryAction",
    "Robot",
    "RoutePlan",
    "SimulationSnapshot",
    "SystemMetrics",
    "Task",
    "parse_command",
    "parse_event",
]
