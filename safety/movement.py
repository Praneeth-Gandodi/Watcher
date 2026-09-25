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
    """Advance ``position`` along ``route`` by up to one step of travel.

    Movement starts from the point on the polyline closest to the robot rather
    than from its second waypoint. A robot that overshoots a waypoint by even a
    few centimetres would otherwise be pulled back to that waypoint on the next
    tick and oscillate there forever, never reaching its target.

    The full remaining polyline is preserved (not consumed) because replanning
    and conflict recovery need the original intent, and the caller tracks
    progress by comparing positions rather than mutating the route.
    """

    waypoints = route.waypoints
    if len(waypoints) < 2:
        return MovementResult(
            position=position,
            arrived=True,
            distance_travelled_m=0.0,
            remaining_waypoints=waypoints,
        )
    if speed_mps <= 0:
        return MovementResult(
            position=position,
            arrived=False,
            distance_travelled_m=0.0,
            remaining_waypoints=waypoints,
        )

    budget = max_step_m if max_step_m is not None else speed_mps
    start_segment, start_x, start_y = _closest_point_on_polyline(waypoints, position)
    current_x, current_y = start_x, start_y
    travelled = 0.0

    for index in range(start_segment, len(waypoints) - 1):
        target = waypoints[index + 1]
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


def _closest_point_on_polyline(
    waypoints: tuple[Position2D, ...], position: Position2D
) -> tuple[int, float, float]:
    """Return the segment index and point on it nearest to ``position``."""

    best_index = 0
    best_x, best_y = waypoints[0].x, waypoints[0].y
    best_distance = float("inf")

    for index in range(len(waypoints) - 1):
        start = waypoints[index]
        end = waypoints[index + 1]
        delta_x = end.x - start.x
        delta_y = end.y - start.y
        length_squared = delta_x * delta_x + delta_y * delta_y
        if length_squared <= 1e-18:
            ratio = 0.0
        else:
            ratio = (
                (position.x - start.x) * delta_x + (position.y - start.y) * delta_y
            ) / length_squared
            ratio = min(1.0, max(0.0, ratio))
        point_x = start.x + delta_x * ratio
        point_y = start.y + delta_y * ratio
        distance = (position.x - point_x) ** 2 + (position.y - point_y) ** 2
        if distance < best_distance:
            best_index = index
            best_x, best_y = point_x, point_y
            best_distance = distance
    return best_index, best_x, best_y


def remaining_distance_m(route: RoutePlan, position: Position2D) -> float:
    """Distance from ``position`` to the end of the route polyline."""

    waypoints = route.waypoints
    if not waypoints:
        return 0.0
    total = hypot(waypoints[-1].x - position.x, waypoints[-1].y - position.y)
    return max(0.0, total)
