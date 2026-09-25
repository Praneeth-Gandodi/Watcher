"""Battery accounting and charging decisions for Agent 2.

Energy is modelled as a simple, documented budget rather than a physics sim:
an idle robot drains slowly, a moving robot drains proportionally to distance
travelled, and a carrying robot drains faster. What matters for coordination is
not the absolute number but the *margin* — whether a robot can still finish its
task and still reach a charger — so the manager works in terms of that margin
and emits ``BATTERY_LOW`` plus a return-to-charger decision before a robot is
stranded.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from backend.contracts.models import (
    GridCellType,
    Position2D,
    RecoveryAction,
    RecoveryActionType,
    Robot,
    Task,
    ActionStatus,
)
from backend.simulation.grid import WorldIndex

IDLE_DRAIN_PER_S = 0.02
MOVE_DRAIN_PER_M = 0.09
CARRY_DRAIN_MULTIPLIER = 1.6
CHARGING_GAIN_PER_S = 1.4
LOW_BATTERY_THRESHOLD_PERCENT = 20.0
CRITICAL_BATTERY_THRESHOLD_PERCENT = 8.0
CHARGE_TARGET_PERCENT = 85.0
LOW_BATTERY_EVENT_MEMORY_S = 30.0


@dataclass(frozen=True, slots=True)
class BatteryDecision:
    """What the battery manager wants the runtime to do about one robot."""

    robot_id: str
    battery_percent: float
    threshold_percent: float
    estimated_range_m: float
    should_return_to_charger: bool
    should_release_task: bool


@dataclass(slots=True)
class BatteryManager:
    """Owns energy drain, low-battery thresholds, and charger selection."""

    low_threshold_percent: float = LOW_BATTERY_THRESHOLD_PERCENT
    critical_threshold_percent: float = CRITICAL_BATTERY_THRESHOLD_PERCENT
    charge_target_percent: float = CHARGE_TARGET_PERCENT
    drain_per_m: float = MOVE_DRAIN_PER_M
    idle_drain_per_s: float = IDLE_DRAIN_PER_S
    gain_per_s: float = CHARGING_GAIN_PER_S
    announced: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.announced is None:
            self.announced = {}

    def drain(
        self,
        robot: Robot,
        *,
        distance_travelled_m: float,
        elapsed_s: float,
        carrying: bool,
    ) -> float:
        """Return the battery percentage after one tick of energy use."""

        if robot.status.value == "charging":
            return min(100.0, robot.battery_percent + self.gain_per_s * elapsed_s)
        if robot.status.value in {"failed", "offline"}:
            return robot.battery_percent
        consumption = self.idle_drain_per_s * elapsed_s
        consumption += self.drain_per_m * distance_travelled_m
        if carrying:
            consumption *= CARRY_DRAIN_MULTIPLIER
        return max(0.0, robot.battery_percent - consumption)

    def estimate_range_m(self, battery_percent: float) -> float:
        """Range in meters at the movement drain rate, ignoring idle draw."""

        usable = max(0.0, battery_percent - self.low_threshold_percent)
        return usable / self.drain_per_m if self.drain_per_m else 0.0

    def assess(
        self,
        robot: Robot,
        task: Task | None,
        *,
        index: WorldIndex,
        now_s: float,
    ) -> BatteryDecision | None:
        """Decide whether a robot must charge, or announce a low battery.

        Returns ``None`` when the robot is comfortably above the threshold and
        nothing needs to happen, so the caller can skip the work entirely on
        the common path.
        """

        if robot.status.value in {"failed", "offline", "charging"}:
            return None

        range_m = self.estimate_range_m(robot.battery_percent)
        task_distance = 0.0
        if task is not None:
            task_distance = hypot(
                task.target.x - robot.position.x, task.target.y - robot.position.y
            )
        threshold = (
            self.critical_threshold_percent
            if robot.battery_percent <= self.critical_threshold_percent
            else self.low_threshold_percent
        )
        needs_energy = robot.battery_percent <= self.low_threshold_percent
        cannot_finish = task_distance > range_m
        if not needs_energy and not cannot_finish:
            return None

        first_report = robot.robot_id not in self.announced
        if first_report:
            self.announced[robot.robot_id] = now_s
        elif now_s - self.announced[robot.robot_id] < LOW_BATTERY_EVENT_MEMORY_S:
            return None

        return BatteryDecision(
            robot_id=robot.robot_id,
            battery_percent=round(robot.battery_percent, 2),
            threshold_percent=threshold,
            estimated_range_m=round(range_m, 2),
            should_return_to_charger=needs_energy or cannot_finish,
            should_release_task=needs_energy,
        )

    def pick_charger(
        self,
        robot: Robot,
        index: WorldIndex,
        *,
        reserved: frozenset[Cell] = frozenset(),
    ) -> Cell | None:
        """Choose the nearest charging cell that another robot is not using."""

        nearest = index.nearest_cell_of_type(
            robot.position, GridCellType.CHARGING, unavailable=reserved
        )
        if nearest is None:
            nearest = index.nearest_cell_of_type(robot.position, GridCellType.CHARGING)
        return nearest

    def is_satisfied(self, robot: Robot) -> bool:
        return robot.battery_percent >= self.charge_target_percent

    def build_return_action(
        self,
        robot: Robot,
        charger_center: tuple[float, float],
        *,
        now_s: float,
    ) -> RecoveryAction:
        return RecoveryAction(
            action_id=f"recovery-charge-{robot.robot_id}-{int(now_s * 1000)}",
            action_type=RecoveryActionType.RETURN_TO_CHARGER,
            target_robot_ids=(robot.robot_id,),
            affected_task_ids=(),
            reason=(
                f"battery {robot.battery_percent:.1f}% is below the reserve; "
                f"returning to the charger at "
                f"({charger_center[0]:.1f}, {charger_center[1]:.1f}) m"
            )[:500],
            started_at_s=round(now_s, 3),
            status=ActionStatus.ACTIVE,
        )


def distance_to(position: Position2D, target: tuple[float, float]) -> float:
    return hypot(target[0] - position.x, target[1] - position.y)
