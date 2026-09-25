"""Test plan C and D: different speeds and time-aware trajectories.

Covers:
* the same path with a faster robot gets earlier timestamps
* the same path with a slower robot gets later timestamps
* timestamps increase monotonically
* ``occupied_cells`` matches the robot footprint at every point
* movement duration matches ``cell_size_m / speed_mps``
* speed logic lives in the trajectory, not in the planner
"""

from __future__ import annotations

import pytest
from backend.safety.trajectory import (
    has_non_decreasing_timestamps,
    has_strictly_increasing_timestamps,
    point_at_time,
    seconds_per_cell,
    shift_trajectory,
    total_duration_s,
    trajectory_cells,
    trajectory_from_cells,
    trajectory_future_view,
)
from tests.unit.safety.conftest import make_steps

ROW_CELLS = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


def test_seconds_per_cell_is_cell_size_over_speed() -> None:
    assert seconds_per_cell(2.0, 2.0) == 1.0
    assert seconds_per_cell(1.0, 0.5) == 2.0
    assert seconds_per_cell(2.5, 1.25) == 2.0


@pytest.mark.parametrize("speed", [0.0, -1.0, float("inf")])
def test_non_positive_or_non_finite_speed_is_rejected(speed: float) -> None:
    with pytest.raises(ValueError):
        seconds_per_cell(1.0, speed)


def test_timestamps_increase_monotonically() -> None:
    trajectory = trajectory_from_cells(ROW_CELLS, speed_mps=1.5, cell_size_m=2.0)

    assert [point.timestamp_s for point in trajectory] == [0.0, 4 / 3, 8 / 3, 4.0, 16 / 3]
    assert has_strictly_increasing_timestamps(trajectory)
    assert has_non_decreasing_timestamps(trajectory)
    assert total_duration_s(trajectory) == pytest.approx(16 / 3)


def test_occupied_cells_match_the_footprint_at_every_point() -> None:
    trajectory = trajectory_from_cells(
        [(3, 4), (4, 4), (5, 4)],
        width_cells=2,
        height_cells=2,
        speed_mps=1.0,
    )

    assert [point.occupied_cells for point in trajectory] == [
        ((3, 4), (4, 4), (3, 5), (4, 5)),
        ((4, 4), (5, 4), (4, 5), (5, 5)),
        ((5, 4), (6, 4), (5, 5), (6, 5)),
    ]
    for point in trajectory:
        assert point.occupied_cells[0] == point.cell
        assert len(point.occupied_cells) == 4


def test_three_by_two_footprint_is_reported_on_every_point() -> None:
    trajectory = trajectory_from_cells(
        [(0, 0), (1, 0)], width_cells=3, height_cells=2, speed_mps=1.0
    )

    assert [len(point.occupied_cells) for point in trajectory] == [6, 6]
    assert trajectory[1].occupied_cells == (
        (1, 0), (2, 0), (3, 0), (1, 1), (2, 1), (3, 1),
    )


# ----------------------------------------------------------------------
# C. the same path at different speeds
# ----------------------------------------------------------------------


def test_faster_robot_arrives_earlier_on_the_same_path() -> None:
    fast = trajectory_from_cells(ROW_CELLS, speed_mps=4.0, cell_size_m=2.0)
    slow = trajectory_from_cells(ROW_CELLS, speed_mps=1.0, cell_size_m=2.0)

    assert trajectory_cells(fast) == trajectory_cells(slow), "same spatial path"
    assert fast[-1].timestamp_s == 2.0  # 4 steps * (2.0 m / 4.0 m/s)
    assert slow[-1].timestamp_s == 8.0  # 4 steps * (2.0 m / 1.0 m/s)
    # Both start at t=0; every arrival after the first is earlier when faster.
    assert fast[0].timestamp_s == slow[0].timestamp_s == 0.0
    for fast_point, slow_point in zip(fast[1:], slow[1:]):
        assert fast_point.timestamp_s < slow_point.timestamp_s


def test_speed_changes_only_the_clock_not_the_path() -> None:
    """Timing is the trajectory's job; A* output is speed independent."""

    path = make_steps(ROW_CELLS)
    double_speed = trajectory_from_cells(ROW_CELLS, speed_mps=2.0)
    half_speed = trajectory_from_cells(ROW_CELLS, speed_mps=0.5)

    assert trajectory_cells(double_speed) == trajectory_cells(path)
    assert trajectory_cells(half_speed) == trajectory_cells(path)
    assert double_speed[-1].timestamp_s * 4 == half_speed[-1].timestamp_s


def test_start_time_shifts_the_whole_trajectory() -> None:
    at_zero = trajectory_from_cells(ROW_CELLS, speed_mps=1.0)
    at_ten = trajectory_from_cells(ROW_CELLS, speed_mps=1.0, start_time_s=10.0)

    assert at_ten[0].timestamp_s == 10.0
    assert at_ten[-1].timestamp_s == at_zero[-1].timestamp_s + 10.0
    assert shift_trajectory(at_zero, 10.0) == at_ten
    assert shift_trajectory(at_zero, 0.0) is at_zero


# ----------------------------------------------------------------------
# helpers used by the collision engine
# ----------------------------------------------------------------------


def test_point_at_time_returns_the_last_reached_placement() -> None:
    trajectory = trajectory_from_cells(ROW_CELLS, speed_mps=1.0, cell_size_m=2.0)

    assert point_at_time(trajectory, -5.0).cell == (0, 0)
    assert point_at_time(trajectory, 0.0).cell == (0, 0)
    assert point_at_time(trajectory, 2.0).cell == (1, 0)
    assert point_at_time(trajectory, 999.0).cell == (4, 0)
    assert point_at_time((), 0.0) is None


def test_future_view_drops_elapsed_history_but_keeps_the_leaving_cells() -> None:
    trajectory = trajectory_from_cells(ROW_CELLS, speed_mps=1.0, cell_size_m=2.0)

    view = trajectory_future_view(trajectory, 5.0)

    assert view[0].timestamp_s == 5.0
    # The anchor keeps the union of the cells it is leaving, so the comparison
    # never forgets where the robot actually was.
    assert set(view[0].occupied_cells) == {(2, 0), (3, 0)}
    assert trajectory_cells(view)[-1] == (4, 0)


def test_future_view_of_a_finished_trajectory_is_a_single_anchor() -> None:
    trajectory = trajectory_from_cells(ROW_CELLS, speed_mps=1.0)

    view = trajectory_future_view(trajectory, 100.0)

    assert len(view) == 1
    assert view[0].cell == (4, 0)
    assert view[0].timestamp_s == 100.0


def test_empty_path_produces_an_empty_trajectory() -> None:
    assert trajectory_from_cells([], speed_mps=1.0) == ()
    assert total_duration_s(()) == 0.0
