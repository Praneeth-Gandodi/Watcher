"""Unit tests for predictive collision detection and right-of-way resolution."""

from __future__ import annotations

import pytest

from backend.contracts.models import (
    CommunicationState,
    ConflictKind,
    GridCell,
    GridCellType,
    Position2D,
    Robot,
    RobotStatus,
    RoutePlan,
    RouteStatus,
    WorldState,
)
from backend.simulation.grid import WorldIndex
from safety.collision import CollisionDetector, decide_right_of_way

COLUMNS = 20
ROWS = 20


def build_world() -> WorldState:
    return WorldState(
        width_m=40.0,
        height_m=40.0,
        cell_size_m=2.0,
        columns=COLUMNS,
        rows=ROWS,
        cells=(GridCell(cell_x=0, cell_y=0, cell_type=GridCellType.OBSTACLE),),
        revision=1,
    )


def make_robot(
    robot_id: str,
    x: float,
    y: float,
    *,
    status: RobotStatus = RobotStatus.ACTIVE,
    task_id: str | None = "task-001",
) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=Position2D(x=x, y=y),
        battery_percent=80.0,
        capabilities=("transport",),
        workload=1,
        status=status,
        current_task_id=task_id,
        communication_state=CommunicationState.ONLINE,
        last_updated_at_s=0.0,
    )


def make_route(robot_id: str, task_id: str, waypoints: tuple[tuple[float, float], ...]) -> RoutePlan:
    return RoutePlan(
        route_id=f"route-{robot_id}",
        robot_id=robot_id,
        task_id=task_id,
        waypoints=tuple(Position2D(x=x, y=y) for x, y in waypoints),
        strategy="astar-grid-direct",
        status=RouteStatus.ACTIVE,
        version=1,
        planned_at_s=0.0,
    )


def intents_for(
    detector: CollisionDetector,
    world: WorldState,
    robots: list[Robot],
    routes: dict[str, RoutePlan],
    *,
    speed_mps: float = 1.0,
):
    index = WorldIndex.from_world(world)
    return [
        detector.build_intent(robot, routes[robot.robot_id], speed_mps=speed_mps, index=index)
        for robot in robots
    ]


class TestMotionIntent:
    def test_samples_the_horizon_into_steps(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=2.0, step_s=0.5)
        robot = make_robot("robot-001", 4.0, 4.0)
        route = make_route("robot-001", "task-001", ((4.0, 4.0), (14.0, 4.0)))
        intent = detector.build_intent(robot, route, speed_mps=1.0, index=WorldIndex.from_world(world))
        # One sample at the present plus one per step across the horizon.
        assert len(intent.positions) == 5
        assert intent.positions[0] == (4.0, 4.0)
        assert intent.positions[-1][0] > 4.0

    def test_advances_along_the_route_polyline(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=2.0, step_s=1.0)
        robot = make_robot("robot-001", 4.0, 4.0)
        route = make_route("robot-001", "task-001", ((4.0, 4.0), (8.0, 4.0), (8.0, 10.0)))
        intent = detector.build_intent(robot, route, speed_mps=3.0, index=WorldIndex.from_world(world))
        last_x, last_y = intent.positions[-1]
        # Two steps of 3 m: the first is spent crossing to x=8, the rest climbs
        # the second leg.
        assert last_x == pytest.approx(8.0)
        assert 4.0 < last_y < 10.0

    def test_stops_projecting_at_the_route_end(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=10.0, step_s=1.0)
        robot = make_robot("robot-001", 4.0, 4.0)
        route = make_route("robot-001", "task-001", ((4.0, 4.0), (6.0, 4.0)))
        intent = detector.build_intent(robot, route, speed_mps=5.0, index=WorldIndex.from_world(world))
        assert intent.positions[-1] == pytest.approx((6.0, 4.0))


class TestConflictDetection:
    def test_reports_a_collision_when_paths_converge(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=4.0, step_s=0.4)
        left = make_robot("robot-001", 4.0, 10.0)
        right = make_robot("robot-002", 8.0, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 10.0), (24.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((8.0, 10.0), (0.0, 10.0))),
        }
        conflicts, decisions = detector.detect(
            intents_for(detector, world, [left, right], routes),
            robots_by_id={left.robot_id: left, right.robot_id: right},
            now_s=0.0,
        )
        assert len(conflicts) == 1
        assert conflicts[0].kind is ConflictKind.COLLISION_RISK
        assert conflicts[0].severity.value == "critical"
        assert conflicts[0].robot_ids == ("robot-001", "robot-002")
        assert len(decisions) == 1

    def test_reports_right_of_way_for_a_near_miss(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=2.0, step_s=0.5, margin_m=1.0)
        # The two robots straddle a cell boundary: close enough to be a hazard,
        # but never in the same cell, so this is contention, not a collision.
        left = make_robot("robot-001", 3.9, 10.0)
        right = make_robot("robot-002", 4.1, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((3.9, 10.0), (20.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((4.1, 10.0), (20.0, 10.0))),
        }
        conflicts, decisions = detector.detect(
            intents_for(detector, world, [left, right], routes),
            robots_by_id={left.robot_id: left, right.robot_id: right},
            now_s=0.0,
        )
        assert conflicts[0].kind is ConflictKind.RIGHT_OF_WAY
        assert conflicts[0].severity.value == "warning"
        assert decisions[0].conflict_kind is ConflictKind.RIGHT_OF_WAY

    def test_parallel_robots_do_not_conflict(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=3.0, step_s=0.5)
        left = make_robot("robot-001", 4.0, 4.0)
        right = make_robot("robot-002", 4.0, 12.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 4.0), (24.0, 4.0))),
            "robot-002": make_route("robot-002", "task-002", ((4.0, 12.0), (24.0, 12.0))),
        }
        conflicts, decisions = detector.detect(
            intents_for(detector, world, [left, right], routes),
            robots_by_id={left.robot_id: left, right.robot_id: right},
            now_s=0.0,
        )
        assert conflicts == []
        assert decisions == []

    def test_suppresses_repeats_for_the_same_pair(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=4.0, step_s=0.4)
        left = make_robot("robot-001", 4.0, 10.0)
        right = make_robot("robot-002", 8.0, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 10.0), (24.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((8.0, 10.0), (0.0, 10.0))),
        }
        robots = {left.robot_id: left, right.robot_id: right}
        intents = intents_for(detector, world, [left, right], routes)
        first, _ = detector.detect(intents, robots_by_id=robots, now_s=0.0)
        second, _ = detector.detect(intents, robots_by_id=robots, now_s=0.5)
        assert len(first) == 1
        assert second == []

    def test_reports_again_after_the_memory_window(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=4.0, step_s=0.4, memory_s=1.5)
        left = make_robot("robot-001", 4.0, 10.0)
        right = make_robot("robot-002", 8.0, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 10.0), (24.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((8.0, 10.0), (0.0, 10.0))),
        }
        robots = {left.robot_id: left, right.robot_id: right}
        intents = intents_for(detector, world, [left, right], routes)
        detector.detect(intents, robots_by_id=robots, now_s=0.0)
        later, _ = detector.detect(intents, robots_by_id=robots, now_s=5.0)
        assert len(later) == 1

    def test_conflict_ids_are_unique_over_time(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=4.0, step_s=0.4, memory_s=0.0)
        left = make_robot("robot-001", 4.0, 10.0)
        right = make_robot("robot-002", 8.0, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 10.0), (24.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((8.0, 10.0), (0.0, 10.0))),
        }
        robots = {left.robot_id: left, right.robot_id: right}
        intents = intents_for(detector, world, [left, right], routes)
        first, _ = detector.detect(intents, robots_by_id=robots, now_s=0.0)
        second, _ = detector.detect(intents, robots_by_id=robots, now_s=1.0)
        assert first[0].conflict_id != second[0].conflict_id

    def test_skips_intents_without_a_known_robot(self) -> None:
        world = build_world()
        detector = CollisionDetector(horizon_s=4.0, step_s=0.4)
        left = make_robot("robot-001", 4.0, 10.0)
        right = make_robot("robot-002", 16.0, 10.0)
        routes = {
            "robot-001": make_route("robot-001", "task-001", ((4.0, 10.0), (24.0, 10.0))),
            "robot-002": make_route("robot-002", "task-002", ((16.0, 10.0), (2.0, 10.0))),
        }
        conflicts, _ = detector.detect(
            intents_for(detector, world, [left, right], routes),
            robots_by_id={"robot-001": left},
            now_s=0.0,
        )
        assert conflicts == []


class TestRightOfWay:
    def route(self, robot_id: str, waypoints: tuple[tuple[float, float], ...]) -> RoutePlan:
        return make_route(robot_id, "task-001", waypoints)

    def test_a_blocked_robot_yields(self) -> None:
        blocked = make_robot("robot-001", 4.0, 4.0, status=RobotStatus.BLOCKED)
        running = make_robot("robot-002", 4.0, 8.0)
        decision = decide_right_of_way(
            blocked,
            self.route("robot-001", ((4.0, 4.0), (20.0, 4.0))),
            1,
            running,
            self.route("robot-002", ((4.0, 8.0), (20.0, 8.0))),
            1,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        assert decision.yielding_robot_id == "robot-001"
        assert "while the other robot is blocked" in decision.reason

    def test_higher_task_priority_keeps_the_path(self) -> None:
        low = make_robot("robot-001", 4.0, 4.0)
        high = make_robot("robot-002", 4.0, 8.0)
        decision = decide_right_of_way(
            low,
            self.route("robot-001", ((4.0, 4.0), (20.0, 4.0))),
            1,
            high,
            self.route("robot-002", ((4.0, 8.0), (20.0, 8.0))),
            5,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        assert decision.right_of_way_robot_id == "robot-002"
        assert decision.yielding_robot_id == "robot-001"

    def test_the_longer_remaining_route_keeps_the_path(self) -> None:
        short = make_robot("robot-001", 4.0, 4.0)
        long = make_robot("robot-002", 4.0, 8.0)
        decision = decide_right_of_way(
            short,
            self.route("robot-001", ((4.0, 4.0), (8.0, 4.0))),
            3,
            long,
            self.route("robot-002", ((4.0, 8.0), (30.0, 8.0))),
            3,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        assert decision.right_of_way_robot_id == "robot-002"

    def test_the_lower_identifier_yields_on_a_full_tie(self) -> None:
        first = make_robot("robot-001", 4.0, 4.0)
        second = make_robot("robot-002", 4.0, 8.0)
        waypoints = ((4.0, 4.0), (20.0, 4.0))
        decision = decide_right_of_way(
            first,
            self.route("robot-001", waypoints),
            3,
            second,
            self.route("robot-002", ((4.0, 8.0), (20.0, 8.0))),
            3,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        assert decision.yielding_robot_id == "robot-001"
        assert "identifier tie-break" in decision.reason

    def test_the_decision_is_independent_of_argument_order(self) -> None:
        first = make_robot("robot-001", 4.0, 4.0)
        second = make_robot("robot-002", 4.0, 8.0)
        forward = decide_right_of_way(
            first,
            self.route("robot-001", ((4.0, 4.0), (20.0, 4.0))),
            1,
            second,
            self.route("robot-002", ((4.0, 8.0), (20.0, 8.0))),
            5,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        reverse = decide_right_of_way(
            second,
            self.route("robot-002", ((4.0, 8.0), (20.0, 8.0))),
            5,
            first,
            self.route("robot-001", ((4.0, 4.0), (20.0, 4.0))),
            1,
            position=Position2D(x=4.0, y=6.0),
            kind=ConflictKind.RIGHT_OF_WAY,
        )
        assert forward.yielding_robot_id == reverse.yielding_robot_id
        assert forward.right_of_way_robot_id == reverse.right_of_way_robot_id
