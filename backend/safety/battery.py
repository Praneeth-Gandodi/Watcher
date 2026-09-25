"""Battery observation, thresholds, and reserve checks.

Energy is a **simulation assumption**, not a physical claim: the MVP charges one
battery percentage unit per traversed grid cell by default, and the rate is
configurable per :class:`~backend.safety.robot_profile.RobotProfile`.

The canonical ``Robot.battery_percent`` field is the only battery state in the
system; this module never introduces a second property. It observes a battery
level and reports whether it crossed a threshold or can still finish its route.
Thresholds are detected, not enforced: producing the canonical ``BATTERY_LOW``
event is the runtime's job, and the *decision* to reassign work stays with
Agent 1's reassignment service.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from backend.contracts.models import Robot
from backend.safety.failure import evolve_robot
from backend.safety.robot_profile import RobotProfile

__all__ = [
    "BatteryManager",
    "BatteryObservation",
    "BatteryPolicy",
    "DEFAULT_CRITICAL_PERCENT",
    "DEFAULT_LOW_PERCENT",
    "DEFAULT_RESERVE_PERCENT",
]

DEFAULT_LOW_PERCENT = 20.0
DEFAULT_CRITICAL_PERCENT = 10.0
DEFAULT_RESERVE_PERCENT = 10.0


@dataclass(frozen=True, slots=True)
class BatteryPolicy:
    """Configurable battery thresholds and reserve."""

    low_threshold_percent: float = DEFAULT_LOW_PERCENT
    critical_threshold_percent: float = DEFAULT_CRITICAL_PERCENT
    reserve_percent: float = DEFAULT_RESERVE_PERCENT

    def __post_init__(self) -> None:
        for name in (
            "low_threshold_percent",
            "critical_threshold_percent",
            "reserve_percent",
        ):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
        if self.critical_threshold_percent > self.low_threshold_percent:
            raise ValueError(
                "critical_threshold_percent must not exceed low_threshold_percent"
            )
        if self.reserve_percent > 100.0:
            raise ValueError("reserve_percent must not exceed 100")


@dataclass(frozen=True, slots=True)
class BatteryObservation:
    """Immutable reading of one robot's energy margin."""

    robot_id: str
    battery_percent: float
    consumed_percent: float
    is_low: bool
    is_critical: bool
    has_route_reserve: bool
    required_percent: float
    remaining_cells: int
    estimated_range_cells: float
    estimated_range_m: float
    low_threshold_percent: float
    critical_threshold_percent: float

    @property
    def threshold_percent(self) -> float:
        """Threshold that produced this observation."""

        if self.is_critical:
            return self.critical_threshold_percent
        return self.low_threshold_percent


class BatteryManager:
    """Apply the battery policy to canonical robots."""

    __slots__ = ("_policy",)

    def __init__(self, policy: BatteryPolicy | None = None) -> None:
        self._policy = policy or BatteryPolicy()

    @property
    def policy(self) -> BatteryPolicy:
        return self._policy

    @property
    def low_threshold_percent(self) -> float:
        return self._policy.low_threshold_percent

    @property
    def critical_threshold_percent(self) -> float:
        return self._policy.critical_threshold_percent

    @property
    def reserve_percent(self) -> float:
        return self._policy.reserve_percent

    def consumption_for(
        self,
        cells_travelled: float,
        profile: RobotProfile,
    ) -> float:
        """Return the percentage spent travelling ``cells_travelled`` cells."""

        if not isfinite(cells_travelled) or cells_travelled < 0:
            raise ValueError("cells_travelled must be a non-negative finite number")
        return profile.battery_percent_for_cells(cells_travelled)

    def apply_consumption(
        self,
        robot: Robot,
        consumed_percent: float,
        observed_at_s: float,
    ) -> Robot:
        """Return a new ``Robot`` with consumption applied and clamped to 0-100."""

        if not isfinite(consumed_percent) or consumed_percent < 0:
            raise ValueError("consumed_percent must be a non-negative finite number")
        if consumed_percent == 0.0:
            return robot
        battery_percent = min(100.0, max(0.0, robot.battery_percent - consumed_percent))
        if battery_percent == robot.battery_percent:
            return robot
        return evolve_robot(
            robot,
            battery_percent=battery_percent,
            last_updated_at_s=max(observed_at_s, robot.last_updated_at_s),
        )

    def is_low(self, battery_percent: float) -> bool:
        return battery_percent <= self._policy.low_threshold_percent

    def is_critical(self, battery_percent: float) -> bool:
        return battery_percent <= self._policy.critical_threshold_percent

    def required_percent(self, remaining_cells: float, profile: RobotProfile) -> float:
        """Battery needed to finish ``remaining_cells`` and keep the reserve."""

        return self.consumption_for(remaining_cells, profile) + self._policy.reserve_percent

    def estimated_range_cells(self, battery_percent: float, profile: RobotProfile) -> float:
        rate = profile.battery_percent_per_cell
        if rate <= 0:
            return 0.0
        return max(0.0, battery_percent) / rate

    def estimated_range_m(
        self,
        battery_percent: float,
        profile: RobotProfile,
        cell_size_m: float,
    ) -> float:
        return self.estimated_range_cells(battery_percent, profile) * cell_size_m

    def observe(
        self,
        robot: Robot,
        profile: RobotProfile,
        *,
        consumed_percent: float = 0.0,
        remaining_cells: int = 0,
        cell_size_m: float = 1.0,
    ) -> BatteryObservation:
        """Observe a robot's energy margin after ``consumed_percent`` of travel."""

        if remaining_cells < 0:
            raise ValueError("remaining_cells must not be negative")
        battery_percent = min(100.0, max(0.0, robot.battery_percent - consumed_percent))
        required = self.required_percent(remaining_cells, profile)
        return BatteryObservation(
            robot_id=robot.robot_id,
            battery_percent=battery_percent,
            consumed_percent=consumed_percent,
            is_low=self.is_low(battery_percent),
            is_critical=self.is_critical(battery_percent),
            has_route_reserve=battery_percent >= required,
            required_percent=required,
            remaining_cells=remaining_cells,
            estimated_range_cells=self.estimated_range_cells(battery_percent, profile),
            estimated_range_m=self.estimated_range_m(
                battery_percent, profile, cell_size_m
            ),
            low_threshold_percent=self._policy.low_threshold_percent,
            critical_threshold_percent=self._policy.critical_threshold_percent,
        )

    def robots_needing_energy(
        self,
        robots: Sequence[Robot],
        profiles,
    ) -> tuple[str, ...]:
        """Return the IDs of robots at or below the low threshold, sorted."""

        return tuple(
            sorted(
                robot.robot_id
                for robot in robots
                if self.is_low(robot.battery_percent)
            )
        )
