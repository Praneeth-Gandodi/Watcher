"""Dependency-inversion interfaces implemented by the four agents.

Only contracts and method signatures live here. Implementations must not import
another agent's internal modules; they communicate through these protocols.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .events import EventEnvelope
from .models import (
    NegotiationOutcome,
    Position2D,
    RoutePlan,
    SafetyDecision,
    SimulationSnapshot,
    SystemMetrics,
    Task,
    TaskAssignment,
    WorldState,
)


class EventStream(Protocol):
    async def publish(self, event: EventEnvelope[object]) -> None: ...

    def subscribe(self, after_sequence: int = 0): ...


class NegotiationEngine(Protocol):
    async def negotiate(
        self,
        task: Task,
        candidate_robots: Sequence[object],
        observed_at_s: float,
    ) -> NegotiationOutcome: ...


class TaskAllocator(Protocol):
    async def assign(self, outcome: NegotiationOutcome) -> TaskAssignment | None: ...


class PathPlanner(Protocol):
    async def plan(
        self,
        robot_id: str,
        task_id: str,
        origin: Position2D,
        target: Position2D,
        world: WorldState,
        planned_at_s: float,
    ) -> RoutePlan: ...


class SafetyEngine(Protocol):
    async def evaluate_motion(
        self,
        route: RoutePlan,
        observed_at_s: float,
    ) -> SafetyDecision: ...


class RecoveryCoordinator(Protocol):
    async def recover(
        self,
        affected_robot_ids: Sequence[str],
        observed_at_s: float,
    ) -> Sequence[EventEnvelope[object]]: ...


class DashboardDataProvider(Protocol):
    async def get_snapshot(self) -> SimulationSnapshot: ...

    async def get_metrics(self) -> SystemMetrics: ...
