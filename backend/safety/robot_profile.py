"""Physical simulation profiles for robots.

The canonical ``Robot`` contract is frozen and deliberately carries no
physical geometry: it is the shared decision-layer model, and widening it for
Agent 2's movement needs would couple every consumer to a simulation concern.

Agent 2 therefore keeps footprint, speed, and energy-drain metadata in a
separate, immutable, deterministic registry keyed by ``robot_id``:

``robot-001`` -> ``width_cells=2, height_cells=2, speed_mps=2.0``

Nothing in this module imports another agent's internals, and nothing mutates
the ``Robot`` contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from math import isfinite

__all__ = [
    "DEFAULT_BATTERY_PERCENT_PER_CELL",
    "DEFAULT_HEIGHT_CELLS",
    "DEFAULT_SPEED_MPS",
    "DEFAULT_WIDTH_CELLS",
    "RobotProfile",
    "RobotProfileRegistry",
]

DEFAULT_WIDTH_CELLS = 1
DEFAULT_HEIGHT_CELLS = 1
DEFAULT_SPEED_MPS = 1.0
#: Simulation assumption (not a physical claim): one traversed grid cell costs
#: one battery percentage unit unless a profile overrides it.
DEFAULT_BATTERY_PERCENT_PER_CELL = 1.0


@dataclass(frozen=True, slots=True)
class RobotProfile:
    """Immutable Agent 2 physical profile for one simulated robot."""

    robot_id: str
    width_cells: int = DEFAULT_WIDTH_CELLS
    height_cells: int = DEFAULT_HEIGHT_CELLS
    speed_mps: float = DEFAULT_SPEED_MPS
    battery_percent_per_cell: float = DEFAULT_BATTERY_PERCENT_PER_CELL

    def __post_init__(self) -> None:
        if not self.robot_id:
            raise ValueError("robot_id must not be empty")
        if self.width_cells < 1 or self.height_cells < 1:
            raise ValueError("robot footprint must cover at least one cell")
        if not isfinite(self.speed_mps) or self.speed_mps <= 0:
            raise ValueError("speed_mps must be a positive finite number")
        if (
            not isfinite(self.battery_percent_per_cell)
            or self.battery_percent_per_cell < 0
        ):
            raise ValueError(
                "battery_percent_per_cell must be a non-negative finite number"
            )

    @property
    def footprint_cell_count(self) -> int:
        return self.width_cells * self.height_cells

    def time_per_cell_s(self, cell_size_m: float) -> float:
        """Return the constant time this robot needs for one grid cell.

        The MVP assumes constant speed between adjacent cells:
        ``time_per_cell = cell_size / speed``. Speed logic deliberately lives
        here and in :mod:`backend.safety.trajectory`, never in the planner.
        """

        if not isfinite(cell_size_m) or cell_size_m <= 0:
            raise ValueError("cell_size_m must be a positive finite number")
        return cell_size_m / self.speed_mps

    def battery_percent_for_cells(self, cell_count: float) -> float:
        """Return the battery percentage this robot spends per ``cell_count``."""

        if not isfinite(cell_count) or cell_count < 0:
            raise ValueError("cell_count must be a non-negative finite number")
        return cell_count * self.battery_percent_per_cell

    @property
    def is_default_size(self) -> bool:
        return (
            self.width_cells == DEFAULT_WIDTH_CELLS
            and self.height_cells == DEFAULT_HEIGHT_CELLS
        )


class RobotProfileRegistry:
    """Deterministic ``dict[str, RobotProfile]`` with a documented default.

    Registration order never affects results: iteration, lookup fallbacks, and
    fleet fixtures are all ordered by ``robot_id``.
    """

    __slots__ = ("_defaults", "_profiles")

    def __init__(
        self,
        profiles: Iterable[RobotProfile] | Mapping[str, RobotProfile] = (),
        *,
        defaults: RobotProfile | None = None,
    ) -> None:
        self._profiles: dict[str, RobotProfile] = {}
        self._defaults = defaults
        if isinstance(profiles, Mapping):
            candidates: Iterable[RobotProfile] = profiles.values()
        else:
            candidates = profiles
        for profile in candidates:
            if not isinstance(profile, RobotProfile):
                raise TypeError("registry entries must be RobotProfile instances")
            if profile.robot_id in self._profiles:
                raise ValueError(f"duplicate robot profile for {profile.robot_id!r}")
            self._profiles[profile.robot_id] = profile

    def __contains__(self, robot_id: object) -> bool:
        return robot_id in self._profiles

    def __len__(self) -> int:
        return len(self._profiles)

    def __iter__(self) -> Iterator[RobotProfile]:
        """Iterate profiles in ``robot_id`` order."""

        for robot_id in self.robot_ids():
            yield self._profiles[robot_id]

    def __repr__(self) -> str:
        return f"RobotProfileRegistry(size={len(self._profiles)})"

    @property
    def defaults(self) -> RobotProfile:
        """Profile shape handed to robots that were never registered."""

        if self._defaults is not None:
            return self._defaults
        return DEFAULT_PROFILE

    def register(self, profile: RobotProfile) -> None:
        if profile.robot_id in self._profiles:
            raise ValueError(f"duplicate robot profile for {profile.robot_id!r}")
        self._profiles[profile.robot_id] = profile

    def robot_ids(self) -> tuple[str, ...]:
        """Return every registered ``robot_id`` in sorted order."""

        return tuple(sorted(self._profiles))

    def get(self, robot_id: str) -> RobotProfile:
        """Return a profile, falling back to the default shape.

        Falling back keeps the planner usable for a ``Robot`` that entered the
        fleet without an explicit profile, which is the safe default for a
        simulation prototype: a 1x1 body at a constant speed.
        """

        profile = self._profiles.get(robot_id)
        if profile is not None:
            return profile
        return RobotProfile(
            robot_id=robot_id,
            width_cells=self.defaults.width_cells,
            height_cells=self.defaults.height_cells,
            speed_mps=self.defaults.speed_mps,
            battery_percent_per_cell=self.defaults.battery_percent_per_cell,
        )

    def require(self, robot_id: str) -> RobotProfile:
        """Return a profile or raise when the robot was never registered."""

        try:
            return self._profiles[robot_id]
        except KeyError:
            raise KeyError(f"no robot profile registered for {robot_id!r}") from None

    def as_mapping(self) -> dict[str, RobotProfile]:
        """Return a plain sorted copy of the underlying registry."""

        return {robot_id: self._profiles[robot_id] for robot_id in self.robot_ids()}

    @classmethod
    def uniform(
        cls,
        robot_ids: Iterable[str],
        *,
        width_cells: int = DEFAULT_WIDTH_CELLS,
        height_cells: int = DEFAULT_HEIGHT_CELLS,
        speed_mps: float = DEFAULT_SPEED_MPS,
        battery_percent_per_cell: float = DEFAULT_BATTERY_PERCENT_PER_CELL,
    ) -> RobotProfileRegistry:
        """Build a registry where every robot shares one body shape."""

        return cls(
            (
                RobotProfile(
                    robot_id=robot_id,
                    width_cells=width_cells,
                    height_cells=height_cells,
                    speed_mps=speed_mps,
                    battery_percent_per_cell=battery_percent_per_cell,
                )
                for robot_id in sorted(set(robot_ids))
            )
        )


#: Shape used when a fleet provides no explicit profile for a robot.
DEFAULT_PROFILE = RobotProfile(
    robot_id="default",
    width_cells=DEFAULT_WIDTH_CELLS,
    height_cells=DEFAULT_HEIGHT_CELLS,
    speed_mps=DEFAULT_SPEED_MPS,
    battery_percent_per_cell=DEFAULT_BATTERY_PERCENT_PER_CELL,
)
