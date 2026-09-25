"""Time-aware trajectory generation.

A* decides **where** a robot travels; a trajectory decides **when** it reaches
each footprint placement. Every point records the anchor cell, the timestamp at
which the robot arrives, and the cells the footprint occupies at that instant::

    {"position": (2, 3), "timestamp": 1.5, "occupied_cells": [...]}

Robots may move at different speeds, so trajectories are never assumed to share
a timestep. The MVP models constant speed between adjacent grid cells::

    time_per_cell = cell_size_m / speed_mps

The resulting timestamps are monotonic, which is what makes space-time
collision comparison in :mod:`backend.safety.collision` meaningful.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from backend.safety.pathfinding import PathStep
from backend.simulation.grid import Cell, footprint_cells

__all__ = [
    "TrajectoryPoint",
    "Trajectory",
    "create_trajectory",
    "hold_until",
    "point_at_time",
    "seconds_per_cell",
    "shift_trajectory",
    "total_duration_s",
    "trajectory_cells",
    "trajectory_from_cells",
    "trajectory_future_view",
]

#: A trajectory is an ordered, time-stamped sequence of footprint placements.
Trajectory = tuple["TrajectoryPoint", ...]


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    """One footprint placement at one instant of simulated time."""

    cell: Cell
    timestamp_s: float
    occupied_cells: tuple[Cell, ...]

    @property
    def position(self) -> Cell:
        """Alias for the prototype's ``position`` field."""

        return self.cell

    def as_dict(self) -> dict[str, object]:
        """Return the prototype's plain-dict representation for diagnostics."""

        return {
            "position": self.cell,
            "timestamp": self.timestamp_s,
            "occupied_cells": list(self.occupied_cells),
        }


def seconds_per_cell(cell_size_m: float, speed_mps: float) -> float:
    """Return the constant travel time for one grid cell."""

    if not isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be a positive finite number")
    if not isfinite(speed_mps) or speed_mps <= 0:
        raise ValueError("speed_mps must be a positive finite number")
    return cell_size_m / speed_mps


def create_trajectory(
    path: Sequence[PathStep],
    speed_mps: float,
    cell_size_m: float = 1.0,
    start_time_s: float = 0.0,
) -> Trajectory:
    """Convert a size-aware A* path into a time-aware trajectory.

    ``start_time_s`` shifts the whole trajectory onto the simulation clock.
    """

    if not isfinite(start_time_s):
        raise ValueError("start_time_s must be a finite number")
    if not path:
        return ()
    time_per_cell = seconds_per_cell(cell_size_m, speed_mps)

    points: list[TrajectoryPoint] = []
    current_time = start_time_s
    for index, step in enumerate(path):
        if index > 0:
            current_time += time_per_cell
        points.append(
            TrajectoryPoint(
                cell=step.cell,
                timestamp_s=current_time,
                occupied_cells=step.occupied_cells,
            )
        )
    return tuple(points)


def trajectory_from_cells(
    cells: Sequence[Cell],
    width_cells: int = 1,
    height_cells: int = 1,
    speed_mps: float = 1.0,
    cell_size_m: float = 1.0,
    start_time_s: float = 0.0,
) -> Trajectory:
    """Build a trajectory straight from anchor cells, filling the footprint."""

    steps = tuple(
        PathStep(
            cell=cell,
            occupied_cells=footprint_cells(cell, width_cells, height_cells),
        )
        for cell in cells
    )
    return create_trajectory(steps, speed_mps, cell_size_m, start_time_s)


def trajectory_cells(trajectory: Trajectory) -> tuple[Cell, ...]:
    """Return the anchor cell of every trajectory point."""

    return tuple(point.cell for point in trajectory)


def total_duration_s(trajectory: Trajectory) -> float:
    """Return the time span covered by a trajectory."""

    if not trajectory:
        return 0.0
    return trajectory[-1].timestamp_s - trajectory[0].timestamp_s


def shift_trajectory(trajectory: Trajectory, delta_s: float) -> Trajectory:
    """Return the trajectory with every timestamp moved by ``delta_s``."""

    if not isfinite(delta_s):
        raise ValueError("delta_s must be a finite number")
    if delta_s == 0.0:
        return trajectory
    return tuple(
        TrajectoryPoint(
            cell=point.cell,
            timestamp_s=point.timestamp_s + delta_s,
            occupied_cells=point.occupied_cells,
        )
        for point in trajectory
    )


def hold_until(trajectory: Trajectory, point_index: int, hold_until_s: float) -> Trajectory:
    """Insert an explicit hold at ``point_index`` and delay everything after it.

    A hold is represented by an extra point with the *same* cell and footprint
    at the release time, so the robot provably stays put between its arrival
    and ``hold_until_s``. Simply stretching the following movement segment would
    silently relocate the robot, which is a known failure mode of the original
    prototype.
    """

    if not trajectory:
        raise ValueError("cannot hold on an empty trajectory")
    if not 0 <= point_index < len(trajectory):
        raise ValueError(
            f"point_index must be within 0..{len(trajectory) - 1}, got {point_index}"
        )
    if not isfinite(hold_until_s):
        raise ValueError("hold_until_s must be a finite number")

    held_point = trajectory[point_index]
    if hold_until_s <= held_point.timestamp_s:
        return trajectory

    hold_point = TrajectoryPoint(
        cell=held_point.cell,
        timestamp_s=hold_until_s,
        occupied_cells=held_point.occupied_cells,
    )
    return (
        trajectory[: point_index + 1]
        + (hold_point,)
        + shift_trajectory(
            trajectory[point_index + 1 :], hold_until_s - held_point.timestamp_s
        )
    )


def point_at_time(trajectory: Trajectory, timestamp_s: float) -> TrajectoryPoint | None:
    """Return the placement a robot occupies at ``timestamp_s``.

    Returns the last point for times after the trajectory ends, and ``None``
    only for an empty trajectory.
    """

    if not trajectory:
        return None
    if timestamp_s < trajectory[0].timestamp_s:
        return trajectory[0]
    for index in range(1, len(trajectory)):
        if timestamp_s < trajectory[index].timestamp_s:
            return trajectory[index - 1]
    return trajectory[-1]


def trajectory_future_view(trajectory: Trajectory, from_time_s: float) -> Trajectory:
    """Return only the part of a trajectory at or after ``from_time_s``.

    A modified route must never be compared against already elapsed history:
    the robot already occupied those cells, so they cannot conflict again.
    The returned view keeps a conservative anchor point at ``from_time_s`` that
    carries the union of the cells it is leaving, so the comparison never
    forgets where the robot was.

    This is a stable primitive; the prototype's ``trim_trajectory_from_time``
    is kept as an alias in :mod:`backend.safety.right_of_way`.
    """

    if not trajectory:
        return ()
    if not isfinite(from_time_s):
        raise ValueError("from_time_s must be a finite number")
    if trajectory[0].timestamp_s >= from_time_s:
        return trajectory
    if trajectory[-1].timestamp_s < from_time_s:
        last = trajectory[-1]
        return (
            TrajectoryPoint(
                cell=last.cell,
                timestamp_s=from_time_s,
                occupied_cells=last.occupied_cells,
            ),
        )

    for index in range(1, len(trajectory)):
        if trajectory[index].timestamp_s >= from_time_s:
            previous = trajectory[index - 1]
            current = trajectory[index]
            anchor = TrajectoryPoint(
                cell=previous.cell,
                timestamp_s=from_time_s,
                occupied_cells=tuple(
                    sorted(set(previous.occupied_cells) | set(current.occupied_cells))
                ),
            )
            return (anchor,) + trajectory[index:]
    return ()


def has_strictly_increasing_timestamps(trajectory: Trajectory) -> bool:
    """Return whether timestamps strictly increase (no holds, no stalls)."""

    return all(
        earlier.timestamp_s < later.timestamp_s
        for earlier, later in zip(trajectory, trajectory[1:])
    )


def has_non_decreasing_timestamps(trajectory: Trajectory) -> bool:
    """Return whether timestamps never move backwards (holds allowed)."""

    return all(
        earlier.timestamp_s <= later.timestamp_s
        for earlier, later in zip(trajectory, trajectory[1:])
    )


def as_readonly_map(
    trajectories: Mapping[str, Trajectory],
) -> Mapping[str, Trajectory]:
    """Return a read-only view of a robot/trajectory mapping."""

    return MappingProxyType(dict(trajectories))
