# Integration Protocol

## Ownership

- Agent 1 produces task assignments, negotiation outcomes, and decision events.
- Agent 2 consumes assignments and produces the authoritative backend `WorldState`, robot movement state, routes, safety, battery, failure, deadlock, recovery events, and WebSocket-ready canonical events.
- Agent 3 consumes `WorldState`, canonical snapshots/events, and WebSocket updates and produces only validated commands.
- Agent 4 consumes finished behavior, metrics, logs, screenshots, and architecture information.

## Communication

Subsystems communicate through contracts and protocol interfaces, not private imports or shared mutable state. The protected composition root wires adapters. HTTP serves snapshots/commands and a WebSocket streams events. The dashboard obtains a snapshot, then requests events after `last_event_sequence`.

## Integration workflow

1. Each agent works in its own clone/worktree and branch.
2. Agent adds implementation and tests within ownership paths.
3. Agent runs contract and focused tests.
4. Agent opens a PR describing changed behavior, tests, contracts, events consumed/produced, and limitations.
5. Reviewers check boundary compliance and run the complete suite.
6. Merge focused PRs, then run the A–H scenarios and record evidence in `docs/integrations/`.

## Failure and controller outage

The runtime must represent coordination availability separately from robot execution. If coordination fails, local safety and existing work continue; communication loss triggers timeout handling and work recovery. Do not use the dashboard or API as a hidden decision-maker.
