"""Pathfinding boundary for Agent 2.

`planner.py` owns deterministic 8-connected A* over the canonical
`WorldState` grid, soft occupancy costs, and line-of-sight string pulling. It
returns canonical `RoutePlan` values only; it never mutates world or robot
state and never decides who yields.
"""

from .planner import (
    STRATEGY_DIRECT,
    STRATEGY_FAILED,
    STRATEGY_YIELD_AWARE,
    build_distance_field,
    plan_route,
    route_length_m,
    straight_line_distance_m,
)

__all__ = [
    "STRATEGY_DIRECT",
    "STRATEGY_FAILED",
    "STRATEGY_YIELD_AWARE",
    "build_distance_field",
    "plan_route",
    "route_length_m",
    "straight_line_distance_m",
]
