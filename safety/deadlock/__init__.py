"""Deadlock boundary for Agent 2.

`detector.py` owns the wait-for graph, elementary cycle extraction, and the
canonical `DeadlockReport` plus `RecoveryAction` records a cycle produces. The
runtime publishes those events; it does not re-derive cycles.
"""

from .detector import (
    DEADLOCK_MEMORY_S,
    MIN_WAIT_S,
    DeadlockDetector,
    WaitEdge,
    build_recovery_actions,
)

__all__ = [
    "DEADLOCK_MEMORY_S",
    "MIN_WAIT_S",
    "DeadlockDetector",
    "WaitEdge",
    "build_recovery_actions",
]
