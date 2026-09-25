# Integration Protocol

## Ownership

- Agent 1 produces task assignments, negotiation outcomes, and decision events.
- Agent 2 consumes assignments and produces the authoritative backend `WorldState`, robot movement state, routes, safety, battery, failure, deadlock, recovery events, and WebSocket-ready canonical events.
- Agent 3 consumes `WorldState`, canonical snapshots/events, and WebSocket updates and produces only validated commands.
- Agent 4 consumes finished behavior, metrics, logs, screenshots, and architecture information.

## Communication

Subsystems communicate through contracts and protocol interfaces, not private imports or shared mutable state. The protected composition root wires adapters. HTTP serves snapshots/commands and a WebSocket streams events. The dashboard obtains a snapshot, then requests events after `last_event_sequence`.

In the current implementation this is concrete, and tests enforce it:

- `backend/app/composition.py` is the only module that imports both agents. It exchanges nothing but frozen contract objects.
- Agent 1 reaches Agent 2 through `SimulationRuntime.handle_event`, which understands `TASK_ASSIGNED` and `TASK_REASSIGNED`.
- Agent 2 reaches Agent 1 by publishing `BATTERY_LOW`, `ROBOT_FAILED`, and `COMMUNICATION_LOST`; `ReassignmentService` decides whether the work moves. Agent 2 never creates a `TaskAssignedPayload` or `TaskReassignedPayload`.
- The runtime's `InMemoryEventStream` is the single sequence authority, so both agents' events share one monotonic numbering and a consumer needs only one cursor.

## Integration workflow

1. Each agent works in its own clone/worktree and branch.
2. Agent adds implementation and tests within ownership paths.
3. Agent runs contract and focused tests.
4. Agent opens a PR describing changed behavior, tests, contracts, events consumed/produced, and limitations.
5. Reviewers check boundary compliance and run the complete suite.
6. Merge focused PRs, then run the A–H scenarios and record evidence in `docs/integrations/`.

## Failure and controller outage

The runtime must represent coordination availability separately from robot execution. If coordination fails, local safety and existing work continue; communication loss triggers timeout handling and work recovery. `SystemMetrics.controller_available` reports the outage and `SimulationRuntime.set_controller_available` models it; a test asserts robots keep moving local work while it is false. Do not use the dashboard or API as a hidden decision-maker.
