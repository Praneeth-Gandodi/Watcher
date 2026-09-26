"""Deterministic bid scoring and identifier generation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import hypot, isfinite
from random import Random
from typing import Protocol

from backend.contracts.models import Bid, Robot, Task


@dataclass(frozen=True, slots=True)
class BidCosts:
    """Internal score components before serialization into ``Bid``."""

    total_cost: float
    distance_cost: float
    battery_cost: float
    workload_cost: float
    estimated_completion_time_s: float


def calculate_bid_costs(
    robot: Robot,
    task: Task,
    jitter: float = 0.0,
    rng: Random | None = None,
) -> BidCosts:
    """Calculate a lower-is-better workload-aware bid score.

    Distance is Euclidean world distance. Battery cost is the robot's battery
    deficit percentage, and workload cost is its current workload. Total cost
    is the equal-weight sum of those contributions. Completion estimates use a
    documented nominal one distance unit per second travel time; route planning
    remains Agent 2's responsibility.

    ``jitter`` is a bounded, non-negative amount of randomisation added to the
    distance component. With ``jitter = 0`` the score is fully deterministic.
    A positive value deliberately blurs the distance term so a round can be
    repeated with a different winner, which is how the console's random
    allocation control is exercised. It never makes a cost negative, so the
    score stays a valid lower-is-better value, and it is a property of the
    scoring input rather than of the winner selection, so allocation still
    always picks the cheapest bid.

    Pass ``rng`` to make a randomised round reproducible; the caller owns the
    generator, so one round and its events can be replayed from a seed.
    """

    if not isfinite(jitter) or jitter < 0:
        raise ValueError("jitter must be a non-negative finite number")
    distance_cost = hypot(
        task.target.x - robot.position.x,
        task.target.y - robot.position.y,
    )
    randomised = distance_cost
    if jitter > 0:
        randomised = distance_cost * (1.0 + jitter * (rng or Random()).random())
    battery_cost = 100.0 - robot.battery_percent
    workload_cost = float(robot.workload)
    return BidCosts(
        total_cost=randomised + battery_cost + workload_cost,
        distance_cost=randomised,
        battery_cost=battery_cost,
        workload_cost=workload_cost,
        estimated_completion_time_s=task.estimated_duration_s + randomised,
    )


class BidIdFactory(Protocol):
    """Create stable bid IDs for a robot/task observation round."""

    def __call__(self, task: Task, robot: Robot, observed_at_s: float, valid_until_s: float) -> str: ...


class DeterministicBidIdFactory:
    """Create reproducible kebab-case IDs without shared mutable state."""

    def __call__(
        self,
        task: Task,
        robot: Robot,
        observed_at_s: float,
        valid_until_s: float,
    ) -> str:
        identity = (
            f"{task.task_id}|{robot.robot_id}|{observed_at_s:.6f}|{valid_until_s:.6f}"
        )
        digest = sha256(identity.encode("utf-8")).hexdigest()[:20]
        return f"bid-{digest}"


def create_bid(
    task: Task,
    robot: Robot,
    observed_at_s: float,
    valid_until_s: float,
    id_factory: BidIdFactory,
    jitter: float = 0.0,
    rng: Random | None = None,
) -> Bid:
    """Create a canonical bid for an eligible candidate."""

    costs = calculate_bid_costs(robot, task, jitter=jitter, rng=rng)
    return Bid(
        bid_id=id_factory(task, robot, observed_at_s, valid_until_s),
        robot_id=robot.robot_id,
        task_id=task.task_id,
        total_cost=costs.total_cost,
        distance_cost=costs.distance_cost,
        battery_cost=costs.battery_cost,
        workload_cost=costs.workload_cost,
        estimated_completion_time_s=costs.estimated_completion_time_s,
        created_at_s=observed_at_s,
        valid_until_s=valid_until_s,
    )
