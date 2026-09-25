"""Agent 2 safety and movement subsystem.

Canonical imports for this subsystem::

    from backend.safety.robot_profile import RobotProfile, RobotProfileRegistry
    from backend.safety.pathfinding import AStarPathPlanner, find_path
    from backend.safety.trajectory import create_trajectory
    from backend.safety.collision import detect_collision
    from backend.safety.right_of_way import find_earliest_conflict
    from backend.safety.deadlock import find_deadlock_cycles
    from backend.safety.battery import BatteryManager
    from backend.safety.failure import fail_robot

This package depends only on ``backend.contracts`` and on
``backend.simulation.grid`` (the coordinate/footprint source of truth). It never
imports ``backend.negotiation`` or ``backend.allocation``: task assignment and
reassignment stay Agent 1's decision, and Agent 2 only produces the safety and
failure observations that feed them.

The fleet-wide ``SafetyEngine`` adapter lives in
:mod:`backend.simulation.runtime`, because it needs the runtime's live
trajectory registry. A single ``PathPlanner``/``SafetyEngine`` protocol
implementation exists; there is no competing interface.
"""

from __future__ import annotations

from .battery import (
    BatteryManager,
    BatteryObservation,
    BatteryPolicy,
)
from .collision import (
    Collision,
    TimeSegment,
    build_segments,
    detect_collision,
)
from .deadlock import (
    DeadlockCycle,
    DeadlockRecovery,
    build_deadlock_report,
    build_recovery_action,
    find_deadlock_cycles,
    has_deadlock,
    recover_deadlock,
)
from .failure import (
    can_coordinate,
    evolve_robot,
    fail_robot,
    is_failed,
    mark_communication_lost,
    restore_communication,
    restore_robot,
)
from .pathfinding import AStarPathPlanner, PathStep, find_path
from .right_of_way import (
    FleetConflict,
    RightOfWayDecision,
    RobotPriority,
    YieldResolution,
    find_earliest_conflict,
    try_timing_resolution,
)
from .robot_profile import RobotProfile, RobotProfileRegistry
from .trajectory import TrajectoryPoint, create_trajectory

__all__ = [
    "AStarPathPlanner",
    "BatteryManager",
    "BatteryObservation",
    "BatteryPolicy",
    "Collision",
    "DeadlockCycle",
    "DeadlockRecovery",
    "FleetConflict",
    "PathStep",
    "RightOfWayDecision",
    "RobotPriority",
    "RobotProfile",
    "RobotProfileRegistry",
    "TimeSegment",
    "TrajectoryPoint",
    "YieldResolution",
    "build_deadlock_report",
    "build_recovery_action",
    "build_segments",
    "can_coordinate",
    "create_trajectory",
    "detect_collision",
    "evolve_robot",
    "fail_robot",
    "find_deadlock_cycles",
    "find_earliest_conflict",
    "find_path",
    "has_deadlock",
    "is_failed",
    "mark_communication_lost",
    "recover_deadlock",
    "restore_communication",
    "restore_robot",
    "try_timing_resolution",
]
