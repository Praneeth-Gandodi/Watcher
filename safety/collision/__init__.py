"""Collision and right-of-way boundary for Agent 2.

`detector.py` owns predictive occupancy sampling, conflict construction, and
the documented right-of-way priority order. It produces canonical `Conflict`
values and `YieldDecision` intents; applying a decision is the recovery
coordinator's job.
"""

from .detector import (
    CONFLICT_MEMORY_S,
    PREDICTION_HORIZON_S,
    PREDICTION_STEP_S,
    SAFETY_MARGIN_M,
    CollisionDetector,
    MotionIntent,
    YieldDecision,
    decide_right_of_way,
)

__all__ = [
    "CONFLICT_MEMORY_S",
    "PREDICTION_HORIZON_S",
    "PREDICTION_STEP_S",
    "SAFETY_MARGIN_M",
    "CollisionDetector",
    "MotionIntent",
    "YieldDecision",
    "decide_right_of_way",
]
