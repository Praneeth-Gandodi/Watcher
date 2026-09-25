"""Unit tests for wait-for deadlock detection and recovery."""

from __future__ import annotations

import pytest

from backend.contracts.models import (
    CommunicationState,
    GridCell,
    GridCellType,
    Position2D,
    RecoveryActionType,
    Robot,
    RobotStatus,
    RoutePlan,
    RouteStatus,
    WorldState,
)
from backend.simulation.grid import Cell, WorldIndex
from safety.deadlock import DeadlockDetector, WaitEdge, build_recovery_actions

COLUMNS = 20
ROWS = 20
CELL_SIZE = 2.0


def build_world() -> WorldState:
    return WorldState(
        width_m=40.0,
        height_m=40.0,
        cell_size_m=CELL_SIZE,
        columns=COLUMNS,
        rows=ROWS,
        cells=(GridCell(cell_x=0, cell_y=0, cell_type=GridCellType.OBSTACLE),),
        revision=1,
    )


def make_robot(robot_id: str, x: float, y: float, task_id: str | None) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=Position2D(x=x, y=y),
        battery_percent=70.0,
        capabilities=("transport",),
        workload=1,
        status=RobotStatus.BLOCKED,
        current_task_id=task_id,
        communication_state=CommunicationState.ONLINE,
        last_updated_at_s=0.0,
    )


def make_route(robot_id: str, task_id: str, waypoints) -> RoutePlan:
    return RoutePlan(
        route_id=f"route-{robot_id}",
        robot_id=robot_id,
        task_id=task_id,
        waypoints=tuple(Position2D(x=x, y=y) for x, y in waypoints),
        strategy="astar-grid-direct",
        status=RouteStatus.BLOCKED,
        version=1,
        planned_at_s=0.0,
    )


def edge(waiting: str, blocking: str, cell: Cell = Cell(5, 5)) -> WaitEdge:
    return WaitEdge(
        waiting_robot_id=waiting, blocking_robot_id=blocking, cell=cell, since_s=0.0
    )


class TestWaitEdges:
    def test_derives_an_edge_from_a_reserved_cell_ahead(self) -> None:
        world = build_world()
        index = WorldIndex.from_world(world)
        robot = make_robot("robot-001", 8.0, 10.0, "task-001")
        route = make_route("robot-001", "task-001", ((8.0, 10.0), (20.0, 10.0)))
        owner = {index.cell_of((10.0, 10.0)): "robot-002"}
        edges = DeadlockDetector().build_edges(
            robots={"robot-001": robot},
            routes={"robot-001": route},
            reservation_owner=owner,
            index=index,
            cell_size_m=CELL_SIZE,
        )
        assert len(edges) == 1
        assert edges[0].waiting_robot_id == "robot-001"
        assert edges[0].blocking_robot_id == "robot-002"

    def test_no_edge_when_the_cell_ahead_is_free(self) -> None:
        world = build_world()
        index = WorldIndex.from_world(world)
        robot = make_robot("robot-001", 8.0, 10.0, "task-001")
        route = make_route("robot-001", "task-001", ((8.0, 10.0), (20.0, 10.0)))
        edges = DeadlockDetector().build_edges(
            robots={"robot-001": robot},
            routes={"robot-001": route},
            reservation_owner={},
            index=index,
            cell_size_m=CELL_SIZE,
        )
        assert edges == []

    def test_no_edge_for_a_robots_own_reservation(self) -> None:
        world = build_world()
        index = WorldIndex.from_world(world)
        robot = make_robot("robot-001", 8.0, 10.0, "task-001")
        route = make_route("robot-001", "task-001", ((8.0, 10.0), (20.0, 10.0)))
        owner = {index.cell_of((10.0, 10.0)): "robot-001"}
        edges = DeadlockDetector().build_edges(
            robots={"robot-001": robot},
            routes={"robot-001": route},
            reservation_owner=owner,
            index=index,
            cell_size_m=CELL_SIZE,
        )
        assert edges == []

    def test_ignores_a_route_whose_robot_is_unknown(self) -> None:
        world = build_world()
        index = WorldIndex.from_world(world)
        route = make_route("robot-404", "task-404", ((8.0, 10.0), (20.0, 10.0)))
        edges = DeadlockDetector().build_edges(
            robots={},
            routes={"robot-404": route},
            reservation_owner={index.cell_of((10.0, 10.0)): "robot-002"},
            index=index,
            cell_size_m=CELL_SIZE,
        )
        assert edges == []


class TestDeadlockDetection:
    def detector(self) -> DeadlockDetector:
        return DeadlockDetector(memory_s=4.0)

    def robots(self) -> dict[str, Robot]:
        return {
            "robot-001": make_robot("robot-001", 4.0, 4.0, "task-001"),
            "robot-002": make_robot("robot-002", 8.0, 4.0, "task-002"),
            "robot-003": make_robot("robot-003", 12.0, 4.0, None),
        }

    def test_detects_a_two_robot_cycle(self) -> None:
        reports = self.detector().detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=self.robots(),
            blocked_task_by_robot={
                "robot-001": "task-001",
                "robot-002": "task-002",
                "robot-003": None,
            },
            now_s=0.0,
        )
        assert len(reports) == 1
        report, cycle_edges = reports[0]
        assert report.cycle_robot_ids == ("robot-001", "robot-002")
        assert report.blocked_task_ids == ("task-001", "task-002")
        assert len(cycle_edges) == 2

    def test_detects_a_three_robot_cycle(self) -> None:
        reports = self.detector().detect(
            [
                edge("robot-001", "robot-002"),
                edge("robot-002", "robot-003"),
                edge("robot-003", "robot-001"),
            ],
            robots=self.robots(),
            blocked_task_by_robot={
                "robot-001": "task-001",
                "robot-002": "task-002",
                "robot-003": "task-003",
            },
            now_s=0.0,
        )
        assert len(reports) == 1
        assert reports[0][0].cycle_robot_ids == ("robot-001", "robot-002", "robot-003")

    def test_one_way_wait_is_not_a_deadlock(self) -> None:
        reports = self.detector().detect(
            [edge("robot-001", "robot-002")],
            robots=self.robots(),
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )
        assert reports == []

    def test_a_one_robot_self_loop_is_ignored(self) -> None:
        reports = self.detector().detect(
            [edge("robot-001", "robot-001")],
            robots=self.robots(),
            blocked_task_by_robot={"robot-001": "task-001"},
            now_s=0.0,
        )
        assert reports == []

    def test_suppresses_a_repeat_of_the_same_cycle(self) -> None:
        detector = self.detector()
        edges = [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")]
        blocked = {"robot-001": "task-001", "robot-002": "task-002"}
        assert len(detector.detect(edges, robots=self.robots(), blocked_task_by_robot=blocked, now_s=0.0)) == 1
        assert detector.detect(edges, robots=self.robots(), blocked_task_by_robot=blocked, now_s=1.0) == []

    def test_reports_again_after_the_memory_window(self) -> None:
        detector = self.detector()
        edges = [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")]
        blocked = {"robot-001": "task-001", "robot-002": "task-002"}
        detector.detect(edges, robots=self.robots(), blocked_task_by_robot=blocked, now_s=0.0)
        later = detector.detect(edges, robots=self.robots(), blocked_task_by_robot=blocked, now_s=9.0)
        assert len(later) == 1

    def test_confidence_grows_with_cycle_size(self) -> None:
        two = self.detector().detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=self.robots(),
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )[0][0]
        three = DeadlockDetector(memory_s=4.0).detect(
            [
                edge("robot-001", "robot-002"),
                edge("robot-002", "robot-003"),
                edge("robot-003", "robot-001"),
            ],
            robots=self.robots(),
            blocked_task_by_robot={
                "robot-001": "task-001",
                "robot-002": "task-002",
                "robot-003": "task-003",
            },
            now_s=0.0,
        )[0][0]
        assert three.confidence > two.confidence

    def test_a_chain_into_a_cycle_reports_only_the_cycle(self) -> None:
        reports = self.detector().detect(
            [
                edge("robot-003", "robot-001"),
                edge("robot-001", "robot-002"),
                edge("robot-002", "robot-001"),
            ],
            robots=self.robots(),
            blocked_task_by_robot={
                "robot-001": "task-001",
                "robot-002": "task-002",
                "robot-003": "task-003",
            },
            now_s=0.0,
        )
        assert len(reports) == 1
        assert reports[0][0].cycle_robot_ids == ("robot-001", "robot-002")


class TestRecoveryActions:
    def test_breaks_a_cycle_by_migrating_and_replanning(self) -> None:
        detector = DeadlockDetector()
        robots = {
            "robot-001": make_robot("robot-001", 4.0, 4.0, "task-001"),
            "robot-002": make_robot("robot-002", 8.0, 4.0, "task-002"),
        }
        reports = detector.detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=robots,
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )
        actions = build_recovery_actions(
            reports, robots=robots, index=WorldIndex.from_world(build_world()), now_s=0.0
        )
        types = {action.action_type for action in actions}
        assert RecoveryActionType.TASK_MIGRATION in types
        assert RecoveryActionType.REPLAN in types

    def test_targets_exactly_one_participant(self) -> None:
        robots = {
            "robot-001": make_robot("robot-001", 4.0, 4.0, "task-001"),
            "robot-002": make_robot("robot-002", 8.0, 4.0, "task-002"),
        }
        reports = DeadlockDetector().detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=robots,
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )
        actions = build_recovery_actions(
            reports, robots=robots, index=WorldIndex.from_world(build_world()), now_s=0.0
        )
        targeted = {robot_id for action in actions for robot_id in action.target_robot_ids}
        assert targeted == {"robot-001"}

    def test_the_migrating_robot_is_chosen_reproducibly(self) -> None:
        robots = {
            "robot-002": make_robot("robot-002", 8.0, 4.0, "task-002"),
            "robot-001": make_robot("robot-001", 4.0, 4.0, "task-001"),
        }
        reports = DeadlockDetector().detect(
            [edge("robot-002", "robot-001"), edge("robot-001", "robot-002")],
            robots=robots,
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )
        actions = build_recovery_actions(
            reports, robots=robots, index=WorldIndex.from_world(build_world()), now_s=0.0
        )
        assert {robot_id for action in actions for robot_id in action.target_robot_ids} == {
            "robot-001"
        }

    def test_always_emits_a_replan_even_without_a_task(self) -> None:
        robots = {
            "robot-001": make_robot("robot-001", 4.0, 4.0, None),
            "robot-002": make_robot("robot-002", 8.0, 4.0, None),
        }
        reports = DeadlockDetector().detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=robots,
            blocked_task_by_robot={"robot-001": None, "robot-002": None},
            now_s=0.0,
        )
        actions = build_recovery_actions(
            reports, robots=robots, index=WorldIndex.from_world(build_world()), now_s=0.0
        )
        assert [action.action_type for action in actions] == [RecoveryActionType.REPLAN]

    def test_action_reasons_name_the_cycle(self) -> None:
        robots = {
            "robot-001": make_robot("robot-001", 4.0, 4.0, "task-001"),
            "robot-002": make_robot("robot-002", 8.0, 4.0, "task-002"),
        }
        reports = DeadlockDetector().detect(
            [edge("robot-001", "robot-002"), edge("robot-002", "robot-001")],
            robots=robots,
            blocked_task_by_robot={"robot-001": "task-001", "robot-002": "task-002"},
            now_s=0.0,
        )
        actions = build_recovery_actions(
            reports, robots=robots, index=WorldIndex.from_world(build_world()), now_s=0.0
        )
        assert all(reports[0][0].deadlock_id in action.reason for action in actions)

    def test_no_reports_means_no_actions(self) -> None:
        assert build_recovery_actions([], robots={}, index=WorldIndex.from_world(build_world()), now_s=0.0) == []
