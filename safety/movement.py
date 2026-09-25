"""Robot movement along canonical routes for Agent 2.

Movement is intentionally simple and deterministic: a robot advances along the
polyline of its current ``RoutePlan`` at its own speed, consuming the polyline
as it goes. It never chooses a new route and never resolves a conflict — that
is the planner's and the recovery coordinator's job. Keeping movement dumb is
what makes the safety layer's decisions testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from backend.contracts.models import Position2D, RoutePlan


@dataclass(frozen=True, slots=True)
class MovementResult:
    """Where a robot ended up and what it consumed moving there."""

    position: Position2D
    arrived: bool
    distance_travelled_m: float
    remaining_waypoints: tuple[Position2D, ...]


def advance_along_route(
    route: RoutePlan,
    position: Position2D,
    *,
    speed_mps: float,
    max_step_m: float | None = None,
) -> MovementResult:
    """Advance ``position`` along ``route`` by ``speed_mps * 1s`` of travel.

    The full remaining polyline is preserved (not consumed) because replanning
    and conflict recovery need the original intent, and the caller tracks
    progress by comparing positions rather than mutating the route.
    """

    if speed_mps <= 0:
        return MovementResult(position=position, arrived=False, distance_travelled_m=0.0, remaining_waypoints=route.waypoints)

    waypoints = route.waypoints
    if len(waypoints) < 2:
        return MovementResult(position=position, arrived=True, distance_travelled_m=0.0, remaining_waypoints=waypoints)

    budget = max_step_m if max_step_m is not None else speed_mps
    current_x, current_y = position.x, position.y
    travelled = 0.0

    for index in range(1, len(waypoints)):
        target = waypoints[index]
        segment = hypot(target.x - current_x, target.y - current_y)
        if segment <= 1e-9:
            current_x, current_y = target.x, target.y
            continue
        remaining_budget = budget - travelled
        if remaining_budget <= 0:
            break
        if segment <= remaining_budget:
            current_x, current_y = target.x, target.y
            travelled += segment
            continue
        ratio = remaining_budget / segment
        current_x += (target.x - current_x) * ratio
        current_y += (target.y - current_y) * ratio
        travelled += remaining_budget
        break

    final = Position2D(x=round(current_x, 4), y=round(current_y, 4))
    arrived = hypot(final.x - waypoints[-1].x, final.y - waypoints[-1].y) <= 1e-6
    return MovementResult(
        position=final,
        arrived=arrived,
        distance_travelled_m=travelled,
        remaining_waypoints=waypoints,
    )


def remaining_distance_m(route: RoutePlan, position: Position2D) -> float:
    """Distance from ``position`` to the end of the route polyline."""

    waypoints = route.waypoints
    if not waypoints:
        return 0.0
    total = hypot(waypoints[-1].x - position.x, waypoints[-1].y - position.y)
    return max(0.0, total)
