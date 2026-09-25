# Negotiation scoring and allocation decision

Date: 2026-09-25

## Decision

Agent 1 uses a deterministic, lower-is-better bid score:

- Distance cost is Euclidean world distance to the task target.
- Battery cost is `100 - battery_percent`.
- Workload cost is the robot's current workload.
- Total cost is the equal-weight sum of those contributions.
- Estimated completion is task duration plus Euclidean distance, using a nominal
  one-distance-unit-per-second travel assumption.
- Bids remain valid for five simulation seconds by default.

A robot is eligible when it is idle, unassigned, online, failure-free, has
positive battery, and has all required capabilities. Battery thresholds and
range safety remain Agent 2 responsibilities.

The winner is selected by total cost, estimated completion time, workload,
robot ID, and finally bid ID. Re-negotiating the same robot/task observation
produces a stable bid ID.

## Consequences

- Allocation is deterministic for identical immutable observations.
- Agent 1 does not invent route, energy-consumption, or charger behavior.
- The protected composition root owns state mutation, event sequence allocation,
  and event publication.
- `NEGOTIATION_STARTED` is not emitted when no eligible candidate exists because
  the canonical payload requires at least one candidate.
- A reassignment emits `TASK_REASSIGNED` instead of `TASK_ASSIGNED` and carries
  the triggering canonical event ID.
