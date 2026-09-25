"""Test plan E: space-time aware collision detection.

Covers:
* same space and same time is a conflict
* the same cells at different times are not a conflict
* different speeds are handled by time, not by index
* different robot sizes are handled by footprint
* obstacles stay respected by pathfinding, so they never appear here
"""

from __future__ import annotations

import pytest
from backend.safety.collision import (
    COLLISION_TYPE,
    add_wait,
    build_segments,
    conflicts_with_any,
    detect_collision,
    find_conflicting_segment,
)
from tests.unit.safety.conftest import make_trajectory

# robot-001 travels east along row 2, one cell per second.
EAST = [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2)]
# robot-002 travels north along column 2, crossing the same row.
NORTH = [(2, 0), (2, 1), (2, 2), (2, 3)]


def test_same_space_at_the_same_time_is_a_conflict() -> None:
    east = make_trajectory(EAST, speed_mps=1.0)
    north = make_trajectory(NORTH, speed_mps=1.0)

    collision = detect_collision(east, north)

    assert collision is not None
    assert collision.type == COLLISION_TYPE
    assert collision.cells == ((2, 2),)
    assert collision.time_window == (1.0, 2.0)
    assert collision.start_time_s == 1.0
    assert collision.end_time_s == 2.0
    assert collision.segment1 == 1
    assert collision.segment2 == 1


def test_the_same_cells_at_different_times_are_not_a_conflict() -> None:
    """A shared route is not a collision: only space AND time must overlap."""

    # Both robots use cells (1,2), (2,2) and (3,2), but the second one leaves
    # that corridor upward and does so ten seconds later.
    first = make_trajectory([(1, 2), (2, 2), (3, 2), (4, 2)], speed_mps=1.0)
    second = make_trajectory(
        [(1, 2), (2, 2), (3, 2), (3, 3)], speed_mps=1.0, start_time_s=10.0
    )

    assert {point.cell for point in first} & {point.cell for point in second}
    assert detect_collision(first, second) is None


def test_the_same_corridor_taken_simultaneously_is_a_conflict() -> None:
    """The mirror image of the previous test: same cells, same time."""

    first = make_trajectory([(1, 2), (2, 2), (3, 2), (4, 2)], speed_mps=1.0)
    second = make_trajectory([(1, 2), (2, 2), (3, 2), (4, 2)], speed_mps=1.0)

    assert detect_collision(first, second) is not None


def test_a_slow_follower_that_catches_the_leader_is_a_conflict() -> None:
    """A robot that closes the gap cannot share the corridor safely."""

    leader = make_trajectory(EAST, speed_mps=1.0)
    follower = make_trajectory(EAST, speed_mps=0.5, start_time_s=1.0)

    collision = detect_collision(leader, follower)

    assert collision is not None
    # The follower needs 2 s per cell against the leader's 1 s, so it closes the
    # gap in the middle of the corridor rather than at the parked destination.
    assert collision.cells == ((2, 2),)
    assert collision.time_window == (1.0, 2.0)


def test_different_speeds_are_compared_by_time_not_by_index() -> None:
    """The same index means a different time, so indexes cannot be compared."""

    slow = make_trajectory(EAST, speed_mps=0.5, start_time_s=0.0)
    fast = make_trajectory(EAST, speed_mps=2.0, start_time_s=5.0)

    assert [point.timestamp_s for point in slow] == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert [point.timestamp_s for point in fast] == [5.0, 5.5, 6.0, 6.5, 7.0]
    # Same cells, same indexes, completely different arrival times.
    assert [point.cell for point in slow] == [point.cell for point in fast]
    assert all(
        slow[index].timestamp_s != fast[index].timestamp_s for index in range(5)
    )

    # The conflict is still found, on the swept cell between indexes 2 and 3.
    collision = detect_collision(slow, fast)
    assert collision is not None
    assert collision.time_window == (5.5, 6.0)
    assert collision.cells == ((3, 2),)


def test_equal_travel_time_on_the_same_cell_is_a_conflict_regardless_of_index() -> None:
    fast = make_trajectory(EAST, speed_mps=2.0)
    slow = make_trajectory(EAST, speed_mps=2.0, start_time_s=0.25)

    collision = detect_collision(fast, slow)

    assert collision is not None
    # The fast robot enters the shared cell at 0.5 s, the slow one at 0.75 s;
    # their swept footprints overlap from 0.25 s on the cell before it.
    assert collision.time_window == (0.25, 0.5)
    assert collision.cells == ((1, 2), (2, 2))


def test_different_robot_sizes_are_compared_by_footprint() -> None:
    """A 2x2 body conflicts where a 1x1 body at the same anchor would not."""

    small = make_trajectory([(3, 2), (4, 2)], speed_mps=1.0)
    large = make_trajectory([(2, 1), (3, 1)], speed_mps=1.0, width_cells=2, height_cells=2)

    # Anchors are two cells apart, but the 2x2 footprint at (2, 1) covers
    # (2, 1) (3, 1) (2, 2) (3, 2) and the 1x1 robot passes through (3, 2).
    collision = detect_collision(small, large)

    assert collision is not None
    assert (3, 2) in collision.cells


def test_a_large_footprint_away_from_the_small_one_is_safe() -> None:
    small = make_trajectory([(0, 0), (1, 0)], speed_mps=1.0)
    large = make_trajectory([(0, 5), (1, 5)], speed_mps=1.0, width_cells=3, height_cells=2)

    assert detect_collision(small, large) is None


def test_a_parked_robot_blocks_its_cell_indefinitely() -> None:
    parked = make_trajectory([(5, 2)], speed_mps=1.0)
    passing = make_trajectory(EAST, speed_mps=1.0)
    late_arrival = make_trajectory(EAST, speed_mps=1.0, start_time_s=60.0)

    # The passer's own final segment ends at t=4, so the overlap window does.
    collision = detect_collision(passing, parked)
    assert collision is not None
    assert collision.cells == ((5, 2),)
    assert collision.time_window == (3.0, 4.0)

    # A robot that shows up a minute later still conflicts, because the parked
    # robot's terminal segment extends to infinity.
    assert build_segments(parked)[-1].end_time_s == float("inf")
    assert detect_collision(late_arrival, parked) is not None


def test_empty_trajectories_never_conflict() -> None:
    assert detect_collision((), make_trajectory(EAST, speed_mps=1.0)) is None
    assert detect_collision(make_trajectory(EAST, speed_mps=1.0), ()) is None
    assert conflicts_with_any((), []) is None


def test_collision_keeps_the_prototype_dictionary_representation() -> None:
    east = make_trajectory(EAST, speed_mps=1.0)
    north = make_trajectory(NORTH, speed_mps=1.0)

    payload = detect_collision(east, north).as_dict()

    assert payload == {
        "collision": True,
        "type": "footprint_overlap",
        "time_window": (1.0, 2.0),
        "cells": [(2, 2)],
        "segment1": 1,
        "segment2": 1,
    }


# ----------------------------------------------------------------------
# segments
# ----------------------------------------------------------------------


def test_segments_cover_every_interval_and_end_with_an_open_ended_hold() -> None:
    trajectory = make_trajectory(EAST, speed_mps=1.0)

    segments = build_segments(trajectory)

    assert [segment.index for segment in segments] == [0, 1, 2, 3, 4]
    assert segments[0].start_time_s == 0.0
    assert segments[0].end_time_s == 1.0
    assert segments[0].swept_cells == {(1, 2), (2, 2)}
    assert segments[-1].is_terminal
    assert segments[-1].end_time_s == float("inf")
    assert segments[-1].swept_cells == {(5, 2)}
    assert build_segments(()) == ()


def test_find_conflicting_segment_reports_the_right_side() -> None:
    collision = detect_collision(
        make_trajectory(EAST, speed_mps=1.0), make_trajectory(NORTH, speed_mps=1.0)
    )

    assert find_conflicting_segment(collision, robot_is_first=True) == 1
    assert find_conflicting_segment(collision, robot_is_first=False) == 1


# ----------------------------------------------------------------------
# waiting
# ----------------------------------------------------------------------


def test_add_wait_inserts_an_explicit_hold_and_delays_the_rest() -> None:
    trajectory = make_trajectory(EAST, speed_mps=1.0)

    delayed = add_wait(trajectory, 1, 4.0)

    # Point 1 is (2, 2). The robot holds there from t=1.0 until t=4.0 and only
    # then resumes, rather than the movement segment being silently stretched.
    assert [(point.cell, point.timestamp_s) for point in delayed] == [
        ((1, 2), 0.0),
        ((2, 2), 1.0),
        ((2, 2), 4.0),
        ((3, 2), 5.0),
        ((4, 2), 6.0),
        ((5, 2), 7.0),
    ]


def test_add_wait_before_the_departure_time_is_a_no_op() -> None:
    trajectory = make_trajectory(EAST, speed_mps=1.0)

    assert add_wait(trajectory, 1, 0.5) is trajectory
    assert add_wait(trajectory, 1, 1.0) is trajectory


def test_add_wait_rejects_a_segment_index_outside_the_trajectory() -> None:
    trajectory = make_trajectory(EAST, speed_mps=1.0)

    with pytest.raises(ValueError, match="terminal segment"):
        add_wait(trajectory, len(trajectory), 5.0)
    with pytest.raises(ValueError, match="terminal segment"):
        add_wait(trajectory, 99, 5.0)
    with pytest.raises(ValueError):
        add_wait((), 0, 5.0)


def test_holding_at_the_final_placement_is_allowed() -> None:
    """A robot that already arrived may simply stay longer."""

    trajectory = make_trajectory(EAST, speed_mps=1.0)

    held = add_wait(trajectory, len(trajectory) - 1, 20.0)

    assert held[-1].cell == (5, 2)
    assert held[-1].timestamp_s == 20.0


def test_delaying_departure_clears_a_conflict() -> None:
    east = make_trajectory(EAST, speed_mps=1.0)
    north = make_trajectory(NORTH, speed_mps=1.0)

    # The northern robot holds at (2, 1) until the eastern robot has passed.
    delayed = add_wait(north, 1, 4.0)

    assert detect_collision(east, delayed) is None


def test_conflicts_with_any_reports_the_first_veto_in_the_given_order() -> None:
    mine = make_trajectory(EAST, speed_mps=1.0)
    north = make_trajectory(NORTH, speed_mps=1.0)
    north_east = make_trajectory(EAST, speed_mps=1.0)

    result = conflicts_with_any(
        mine, [("robot-002", north), ("robot-003", north_east)]
    )

    assert result is not None
    assert result[0] == "robot-002"
    assert conflicts_with_any(mine, [("robot-003", north_east)])[0] == "robot-003"
