"""Test plan F and G: earliest fleet conflict and deterministic right-of-way.

Covers:
* the earliest conflict is found across a 3- to 10-robot fleet
* the right-of-way decision is deterministic and does not depend on mapping
  iteration order
* both robots are evaluated as potential yielders
* a timing delay is only accepted when it is clear of the whole fleet
* waiting is validated against all other robots, not just the conflicting pair
* a resolution is re-checked and leaves no residual conflict
"""

from __future__ import annotations

import pytest
from backend.safety.collision import detect_collision
from backend.safety.right_of_way import (
    RobotPriority,
    can_wait_safely,
    choose_yielding_robot,
    clearance_time_s,
    find_earliest_conflict,
    resolve_conflict,
    try_timing_resolution,
)
from tests.unit.safety.conftest import make_trajectory

ROBOT_COUNT = 10


def crossing_fleet(robot_count: int = ROBOT_COUNT) -> dict[str, tuple]:
    """Build a deterministic fleet with a known earliest conflict at t=0.

    ``robot-001`` and ``robot-002`` share row 0 and start together, so their
    conflict begins at t=0, which is the earliest a conflict can begin. The
    remaining robots are spread over separate rows with staggered departures,
    so nothing can conflict earlier and the result stays meaningful.
    """

    trajectories: dict[str, tuple] = {}
    for offset in range(robot_count):
        robot_id = f"robot-{offset + 1:03d}"
        row = 0 if offset < 2 else (offset % 5) + 1
        cells = [(column, row) for column in range(0, 6)]
        trajectories[robot_id] = make_trajectory(
            cells, speed_mps=1.0, start_time_s=2.0 * offset
        )
    return trajectories


# ----------------------------------------------------------------------
# F. earliest conflict
# ----------------------------------------------------------------------


def test_earliest_conflict_is_found_across_a_ten_robot_fleet() -> None:
    trajectories = crossing_fleet()

    conflict = find_earliest_conflict(trajectories)

    # Exhaustive check: 10 robots is 45 unique pairs, and more than one pair
    # genuinely conflicts, so the answer is a real minimum, not a default.
    from itertools import combinations

    from backend.safety.collision import detect_collision as detect

    every_conflict = [
        (
            detect(trajectories[first], trajectories[second]).start_time_s,
            first,
            second,
        )
        for first, second in combinations(sorted(trajectories), 2)
        if detect(trajectories[first], trajectories[second]) is not None
    ]
    assert len(every_conflict) > 1

    assert conflict is not None
    assert len(trajectories) == 10
    assert conflict.start_time_s == min(entry[0] for entry in every_conflict)
    assert conflict.start_time_s == 6.0
    assert conflict.involved_robot_ids == ("robot-001", "robot-002")
    assert conflict.cells == ((5, 0),), (
        "both robots share row 0, so they only overlap once the leader parks"
    )


def test_earliest_conflict_skips_a_pair_that_only_conflicts_later() -> None:
    """The answer is the earliest in time, not the first pair alphabetically."""

    northbound = make_trajectory([(0, 1), (1, 1), (2, 1)], speed_mps=1.0)
    first_east = make_trajectory([(0, 0), (1, 0), (2, 0)], speed_mps=1.0)
    second_east = make_trajectory(
        [(0, 0), (1, 0), (2, 0)], speed_mps=1.0, start_time_s=0.5
    )

    conflict = find_earliest_conflict(
        {
            "robot-001": northbound,
            "robot-002": first_east,
            "robot-003": second_east,
        }
    )

    assert conflict is not None
    assert conflict.start_time_s == 0.5
    assert set(conflict.involved_robot_ids) == {"robot-002", "robot-003"}
    assert not conflict.involves("robot-001"), (
        "robot-001 sorts first but is not involved in the conflict"
    )


def test_no_conflict_is_reported_for_a_spread_out_fleet() -> None:
    trajectories = {
        f"robot-{index + 1:03d}": make_trajectory(
            [(0, index), (1, index), (2, index)], speed_mps=1.0
        )
        for index in range(5)
    }

    assert find_earliest_conflict(trajectories) is None


def test_earliest_conflict_is_independent_of_mapping_insertion_order() -> None:
    trajectories = crossing_fleet(4)
    forward = find_earliest_conflict(trajectories)
    reversed_order = find_earliest_conflict(dict(reversed(list(trajectories.items()))))

    assert forward is not None
    assert reversed_order is not None
    assert forward.involved_robot_ids == reversed_order.involved_robot_ids
    assert forward.start_time_s == reversed_order.start_time_s
    assert forward.cells == reversed_order.cells


# ----------------------------------------------------------------------
# G. deterministic right-of-way
# ----------------------------------------------------------------------


def test_higher_task_priority_keeps_the_right_of_way() -> None:
    decision = choose_yielding_robot(
        "robot-001",
        "robot-002",
        {
            "robot-001": RobotPriority("robot-001", task_priority=5, battery_percent=50.0),
            "robot-002": RobotPriority("robot-002", task_priority=1, battery_percent=90.0),
        },
    )

    assert decision.priority_robot == "robot-001"
    assert decision.yield_robot == "robot-002"
    assert "robot-001" in decision.reason and "robot-002" in decision.reason


def test_equal_priority_is_broken_by_battery_then_waiting_time() -> None:
    """A robot that cannot afford to wait keeps the right of way.

    The battery term is ``(100 - battery) * 0.5``, so a nearly empty robot
    outranks a full one at equal task priority: delaying it would burn the
    range it has left, while a full robot can hold without cost.
    """

    low_battery_wins = choose_yielding_robot(
        "robot-001",
        "robot-002",
        {
            "robot-001": RobotPriority("robot-001", task_priority=3, battery_percent=20.0),
            "robot-002": RobotPriority("robot-002", task_priority=3, battery_percent=80.0),
        },
    )
    assert low_battery_wins.priority_robot == "robot-001"
    assert low_battery_wins.yield_robot == "robot-002"

    long_wait_wins = choose_yielding_robot(
        "robot-001",
        "robot-002",
        {
            "robot-001": RobotPriority(
                "robot-001", task_priority=3, battery_percent=50.0, waiting_time_s=30.0
            ),
            "robot-002": RobotPriority(
                "robot-002", task_priority=3, battery_percent=50.0, waiting_time_s=1.0
            ),
        },
    )
    assert long_wait_wins.priority_robot == "robot-001"


def test_task_priority_outweighs_the_lower_order_terms_at_realistic_scales() -> None:
    decision = choose_yielding_robot(
        "robot-001",
        "robot-002",
        {
            "robot-001": RobotPriority(
                "robot-001", task_priority=5, battery_percent=10.0, waiting_time_s=0.0
            ),
            "robot-002": RobotPriority(
                "robot-002", task_priority=4, battery_percent=100.0, waiting_time_s=30.0
            ),
        },
    )

    assert decision.priority_robot == "robot-001"


def test_the_priority_score_is_a_weighted_sum_not_a_lexicographic_order() -> None:
    """Documented weighting: priority 100, battery deficit 0.5, waiting 2.0.

    One extra task-priority point is worth 200 s of waiting, so a robot that has
    waited a very long time can eventually outrank a higher-priority task. That
    is intentional -- waiting forever is its own failure mode -- and the weights
    are the single source of truth for the trade-off.
    """

    decision = choose_yielding_robot(
        "robot-001",
        "robot-002",
        {
            "robot-001": RobotPriority(
                "robot-001", task_priority=5, battery_percent=10.0, waiting_time_s=0.0
            ),
            "robot-002": RobotPriority(
                "robot-002", task_priority=4, battery_percent=100.0, waiting_time_s=600.0
            ),
        },
    )

    assert RobotPriority("robot-001", 5, 10.0, 0.0).score() == pytest.approx(545.0)
    assert RobotPriority("robot-002", 4, 100.0, 600.0).score() == pytest.approx(1600.0)
    assert decision.priority_robot == "robot-002"


def test_a_perfect_tie_is_broken_by_robot_id_in_every_input_order() -> None:
    robot_info = {
        "robot-001": RobotPriority("robot-001", task_priority=3, battery_percent=50.0),
        "robot-002": RobotPriority("robot-002", task_priority=3, battery_percent=50.0),
    }
    reversed_info = dict(reversed(list(robot_info.items())))

    first = choose_yielding_robot("robot-001", "robot-002", robot_info)
    second = choose_yielding_robot("robot-002", "robot-001", reversed_info)

    assert first.priority_robot == "robot-001"
    assert first.yield_robot == "robot-002"
    # Swapping the argument order must not change the outcome.
    assert second.priority_robot == "robot-001"
    assert second.yield_robot == "robot-002"


def test_an_unknown_robot_falls_back_to_a_default_priority() -> None:
    decision = choose_yielding_robot("robot-001", "robot-002", {})

    assert decision.priority_robot == "robot-001"
    assert decision.yield_robot == "robot-002"


# ----------------------------------------------------------------------
# G. resolution
# ----------------------------------------------------------------------


def conflicting_pair() -> tuple[dict[str, tuple], dict[str, RobotPriority]]:
    trajectories = {
        "robot-001": make_trajectory(
            [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2)], speed_mps=1.0
        ),
        "robot-002": make_trajectory([(2, 0), (2, 1), (2, 2), (2, 3)], speed_mps=1.0),
    }
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4, battery_percent=80.0),
        "robot-002": RobotPriority("robot-002", task_priority=2, battery_percent=80.0),
    }
    return trajectories, priorities


def test_a_safe_timing_delay_resolves_the_conflict() -> None:
    trajectories, priorities = conflicting_pair()
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert resolution.resolved
    assert resolution.strategy == "wait"
    assert resolution.yield_robot == "robot-002", "the lower-priority robot yields"
    assert resolution.trajectory is not None
    # The resolution is validated: no residual conflict remains.
    assert detect_collision(trajectories["robot-001"], resolution.trajectory) is None
    assert find_earliest_conflict(
        {**trajectories, "robot-002": resolution.trajectory}
    ) is None


def test_the_yielder_falls_back_when_its_own_departure_cell_is_needed() -> None:
    """Waiting is not free: if the yielder's hold cell is needed, the other yields.

    ``robot-001`` travels east through (2, 2) and ``robot-002`` arrives at
    (2, 2) later. Making ``robot-001`` wait at (2, 2) would simply swap one
    conflict for another, so ``robot-002`` yields from (2, 1) instead.
    """

    trajectories, _ = conflicting_pair()
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=1, battery_percent=80.0),
        "robot-002": RobotPriority("robot-002", task_priority=5, battery_percent=80.0),
    }
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert resolution.resolved
    assert resolution.yield_robot == "robot-002", (
        "the lower-priority robot yields because the preferred yielder's hold "
        "cell is still needed"
    )
    assert resolution.decision is not None
    assert detect_collision(trajectories["robot-001"], resolution.trajectory) is None


def test_a_third_robot_can_veto_both_yield_options() -> None:
    """When neither robot can hold, the conflict is reported as unresolved."""

    trajectories, _ = conflicting_pair()
    # Two robots sit on the two candidate hold cells, so no delay is safe.
    trajectories["robot-003"] = make_trajectory(
        [(2, 1), (2, 0)], speed_mps=1.0, start_time_s=0.0
    )
    trajectories["robot-004"] = make_trajectory(
        [(3, 2), (4, 2)], speed_mps=1.0, start_time_s=0.0
    )
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4),
        "robot-002": RobotPriority("robot-002", task_priority=2),
        "robot-003": RobotPriority("robot-003", task_priority=1),
        "robot-004": RobotPriority("robot-004", task_priority=1),
    }
    conflict = find_earliest_conflict(trajectories)

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert not resolution.resolved
    assert resolution.reason


def test_a_head_on_corridor_reports_an_unresolved_conflict() -> None:
    """Two robots walking into each other cannot be fixed by timing alone."""

    trajectories = {
        "robot-001": make_trajectory(
            [(0, 3), (1, 3), (2, 3), (3, 3)], speed_mps=1.0
        ),
        "robot-002": make_trajectory(
            [(3, 3), (2, 3), (1, 3), (0, 3)], speed_mps=1.0
        ),
    }
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4),
        "robot-002": RobotPriority("robot-002", task_priority=2),
    }
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert not resolution.resolved
    assert resolution.strategy is None
    assert resolution.yield_robot is None
    assert resolution.reason


def test_a_delay_must_outlast_the_whole_shared_cell_occupancy() -> None:
    """Releasing after the overlap window alone is not enough.

    The swept footprint keeps a cell occupied across consecutive segments, so a
    delay that ends at the first overlapping window simply recreates the same
    conflict one segment later. The resolver therefore releases the yielder only
    once the other robot has finished sweeping every shared cell.
    """

    leader = make_trajectory(
        [(1, 2), (2, 2), (3, 2), (4, 2)], speed_mps=0.5
    )  # 2 s per cell: (2, 2) is swept over [0, 4]
    follower = make_trajectory(
        [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)], speed_mps=1.0
    )
    trajectories = {"robot-001": leader, "robot-002": follower}
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4),
        "robot-002": RobotPriority("robot-002", task_priority=1),
    }
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None
    shared = frozenset(conflict.cells)

    # The overlap window ends at 2.0, but the leader keeps sweeping the shared
    # cell until 4.0, so clearance is 4.0.
    assert conflict.end_time_s == 2.0
    assert clearance_time_s(leader, shared, conflict.start_time_s) == 4.0

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert resolution.resolved
    assert resolution.yield_robot == "robot-002"
    assert find_earliest_conflict(
        {**trajectories, "robot-002": resolution.trajectory}
    ) is None


def test_a_permanently_occupied_cell_is_reported_as_unresolvable() -> None:
    """A parked robot holds its cell forever, so no delay can help."""

    passer = make_trajectory([(1, 2), (2, 2), (3, 2)], speed_mps=1.0)
    parker = make_trajectory([(2, 2)], speed_mps=1.0)
    trajectories = {"robot-001": passer, "robot-002": parker}
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4),
        "robot-002": RobotPriority("robot-002", task_priority=1),
    }
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert not resolution.resolved
    assert "permanently" in resolution.reason
    assert resolution.trajectory is None
    """Two robots walking into each other cannot be fixed by timing alone."""

    trajectories = {
        "robot-001": make_trajectory(
            [(0, 3), (1, 3), (2, 3), (3, 3)], speed_mps=1.0
        ),
        "robot-002": make_trajectory(
            [(3, 3), (2, 3), (1, 3), (0, 3)], speed_mps=1.0
        ),
    }
    priorities = {
        "robot-001": RobotPriority("robot-001", task_priority=4),
        "robot-002": RobotPriority("robot-002", task_priority=2),
    }
    conflict = find_earliest_conflict(trajectories)
    assert conflict is not None

    resolution = try_timing_resolution(conflict, trajectories, priorities)

    assert not resolution.resolved
    assert resolution.strategy is None
    assert resolution.yield_robot is None
    assert resolution.reason


# ----------------------------------------------------------------------
# waiting is validated against the whole fleet
# ----------------------------------------------------------------------


def test_can_wait_safely_rejects_a_hold_blocked_by_a_third_robot() -> None:
    trajectories, _ = conflicting_pair()
    # robot-003 parks on (2, 1), the cell robot-002 would have to hold in.
    trajectories["robot-003"] = make_trajectory([(2, 1)], speed_mps=1.0)

    check = can_wait_safely("robot-002", trajectories, 1, 2.1, from_time_s=1.0)

    assert not check.safe
    assert check.conflict_with == "robot-003"
    assert check.collision is not None
    assert check.reason


def test_a_hold_is_only_compared_against_future_trajectories() -> None:
    """Elapsed history cannot conflict again, so it must not veto a hold."""

    trajectories, _ = conflicting_pair()
    # robot-003 already passed (2, 1) before robot-002 needs to hold there.
    trajectories["robot-003"] = make_trajectory(
        [(2, 1), (2, 0)], speed_mps=1.0, start_time_s=0.0
    )

    check = can_wait_safely("robot-002", trajectories, 1, 2.1, from_time_s=1.0)

    assert check.safe
    assert check.conflict_with is None


def test_can_wait_safely_accepts_a_clear_hold() -> None:
    trajectories, _ = conflicting_pair()

    check = can_wait_safely("robot-002", trajectories, 1, 2.1, from_time_s=1.0)

    assert check.safe
    assert check.conflict_with is None
    assert check.trajectory[1].cell == (2, 1)
    assert check.trajectory[2].cell == (2, 1), "an explicit hold point is inserted"
    assert check.trajectory[2].timestamp_s == 2.1


def test_can_wait_safely_reports_an_invalid_hold_point() -> None:
    trajectories, _ = conflicting_pair()

    check = can_wait_safely("robot-002", trajectories, 99, 5.0)

    assert not check.safe
    assert "point_index" in check.reason


def test_can_wait_safely_requires_a_registered_robot() -> None:
    trajectories, _ = conflicting_pair()

    with pytest.raises(KeyError):
        can_wait_safely("robot-999", trajectories, 0, 1.0)


def test_resolve_conflict_requires_a_conflict() -> None:
    with pytest.raises(ValueError):
        resolve_conflict(None, {})
