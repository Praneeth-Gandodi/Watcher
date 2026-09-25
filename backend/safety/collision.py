"""Space-time collision detection.

Two robots that use the same corridor are **not** in conflict: a conflict only
exists when their physical footprints overlap **during the same time interval**.
Because robots may move at different speeds, trajectory indexes carry no
meaning across robots, so a trajectory is first converted into time segments and
the segments are then swept together by time.

A segment covers the footprint union of the placement it departs from and the
placement it arrives at, and the final segment extends to infinity because a
robot that reached its destination keeps occupying space.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, isinf

from backend.safety.trajectory import Trajectory, hold_until
from backend.simulation.grid import Cell

__all__ = [
    "Collision",
    "TimeSegment",
    "add_wait",
    "build_segments",
    "detect_collision",
    "find_conflicting_segment",
    "hold_until",
]

#: The only conflict kind this engine reports today.
COLLISION_TYPE = "footprint_overlap"


@dataclass(frozen=True, slots=True)
class TimeSegment:
    """A footprint held or swept over a half-open time interval."""

    index: int
    start_time_s: float
    end_time_s: float
    swept_cells: frozenset[Cell]
    start_cell: Cell
    end_cell: Cell | None

    @property
    def is_terminal(self) -> bool:
        return isinf(self.end_time_s)

    def overlaps_time(self, other: TimeSegment) -> tuple[float, float] | None:
        """Return the positive-length shared interval, if any."""

        overlap_start = max(self.start_time_s, other.start_time_s)
        overlap_end = min(self.end_time_s, other.end_time_s)
        if overlap_start < overlap_end:
            return (overlap_start, overlap_end)
        return None

    def overlaps_cells(self, other: TimeSegment) -> tuple[Cell, ...]:
        """Return the shared footprint cells, in deterministic order."""

        return tuple(sorted(self.swept_cells & other.swept_cells))


@dataclass(frozen=True, slots=True)
class Collision:
    """A space-time footprint overlap between two trajectories."""

    time_window: tuple[float, float]
    cells: tuple[Cell, ...]
    segment1: int
    segment2: int
    type: str = COLLISION_TYPE

    @property
    def start_time_s(self) -> float:
        return self.time_window[0]

    @property
    def end_time_s(self) -> float:
        return self.time_window[1]

    def as_dict(self) -> dict[str, object]:
        """Return the prototype's internal dictionary representation."""

        return {
            "collision": True,
            "type": self.type,
            "time_window": self.time_window,
            "cells": list(self.cells),
            "segment1": self.segment1,
            "segment2": self.segment2,
        }


def build_segments(trajectory: Trajectory) -> tuple[TimeSegment, ...]:
    """Convert a trajectory into ordered, non-overlapping time segments."""

    if not trajectory:
        return ()

    segments: list[TimeSegment] = []
    for index in range(len(trajectory) - 1):
        current = trajectory[index]
        following = trajectory[index + 1]
        if following.timestamp_s < current.timestamp_s:
            raise ValueError("trajectory timestamps must not decrease")
        segments.append(
            TimeSegment(
                index=index,
                start_time_s=current.timestamp_s,
                end_time_s=following.timestamp_s,
                swept_cells=frozenset(current.occupied_cells)
                | frozenset(following.occupied_cells),
                start_cell=current.cell,
                end_cell=following.cell,
            )
        )

    last = trajectory[-1]
    segments.append(
        TimeSegment(
            index=len(trajectory) - 1,
            start_time_s=last.timestamp_s,
            end_time_s=float("inf"),
            swept_cells=frozenset(last.occupied_cells),
            start_cell=last.cell,
            end_cell=None,
        )
    )
    return tuple(segments)


def detect_collision(
    trajectory1: Trajectory,
    trajectory2: Trajectory,
) -> Collision | None:
    """Return the earliest space-time conflict between two trajectories.

    Handles different speeds, different start times, and different footprint
    sizes. Returns ``None`` when the trajectories can coexist, which includes
    the case where two robots simply share a route at different times.
    """

    if not trajectory1 or not trajectory2:
        return None

    segments1 = build_segments(trajectory1)
    segments2 = build_segments(trajectory2)

    index1 = 0
    index2 = 0
    while index1 < len(segments1) and index2 < len(segments2):
        segment1 = segments1[index1]
        segment2 = segments2[index2]

        window = segment1.overlaps_time(segment2)
        if window is not None:
            shared_cells = segment1.overlaps_cells(segment2)
            if shared_cells:
                return Collision(
                    time_window=window,
                    cells=shared_cells,
                    segment1=segment1.index,
                    segment2=segment2.index,
                )

        if segment1.end_time_s < segment2.end_time_s:
            index1 += 1
        elif segment2.end_time_s < segment1.end_time_s:
            index2 += 1
        else:
            index1 += 1
            index2 += 1
    return None


def find_conflicting_segment(collision: Collision, robot_is_first: bool) -> int:
    """Return which of the two segment indexes belongs to a robot."""

    return collision.segment1 if robot_is_first else collision.segment2


def add_wait(
    trajectory: Trajectory,
    segment_index: int,
    wait_until_s: float,
) -> Trajectory:
    """Delay the movement of ``segment_index`` by holding before it starts.

    The robot holds at the placement that *departs* into the conflicting
    segment, i.e. at ``trajectory[segment_index]``. Holding is represented by an
    explicit duplicate point so the movement segment is not stretched.
    """

    if not trajectory:
        raise ValueError("cannot resolve an empty trajectory")
    if not isfinite(wait_until_s):
        raise ValueError("wait_until_s must be a finite number")
    if not 0 <= segment_index < len(trajectory):
        raise ValueError(
            "cannot resolve a conflict on the terminal segment by waiting; "
            "the robot has already reached its destination"
        )
    return hold_until(trajectory, segment_index, wait_until_s)


def conflicts_with_any(
    candidate: Trajectory,
    others: Sequence[tuple[str, Trajectory]],
    *,
    from_time_s: float | None = None,
) -> tuple[str, Collision] | None:
    """Return the first (robot_id, collision) a candidate trajectory conflicts with.

    Iteration order follows ``others`` exactly, so callers stay in control of
    determinism. When ``from_time_s`` is given, only the future part of every
    trajectory is compared, because elapsed history cannot collide again.
    """

    from backend.safety.trajectory import trajectory_future_view

    if from_time_s is None:
        candidate_view = candidate
    else:
        candidate_view = trajectory_future_view(candidate, from_time_s)
    for other_id, other_trajectory in others:
        other_view = (
            other_trajectory
            if from_time_s is None
            else trajectory_future_view(other_trajectory, from_time_s)
        )
        collision = detect_collision(candidate_view, other_view)
        if collision is not None:
            return (other_id, collision)
    return None
