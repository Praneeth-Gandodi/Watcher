# Architecture

## Purpose and boundaries

Watcher is a deterministic, software-only multi-agent simulation. It does not require physical robot hardware. The one-day MVP uses a Python simulation process, an in-process event adapter, and a React dashboard. The interfaces are designed so the coordination service and robot agents can be separated or fault-injected without making the API the decision authority.

```text
Task command -> task broadcast -> candidate discovery -> robot bids
    -> peer negotiation -> task assignment
    -> route request -> planning and movement safety
    -> conflict/deadlock detection -> recovery
    -> task completion and canonical events
    -> snapshot/event stream -> React dashboard
```

## Subsystems

| Subsystem | Owns | Does not own |
|---|---|---|
| Agent 1 | Eligibility, bidding, negotiation, allocation, reassignment, decision events | Planning, movement, collision, deadlock, UI |
| Agent 2 | Movement, route planning, conflict/right-of-way, deadlock, battery, failure and communication recovery | Negotiation internals, dashboard |
| Agent 3 | React dashboard, state/event visualization, metrics, commands, fault controls | Backend algorithms, fake domain state |
| Agent 4 | README, architecture diagrams, demos, benchmarks, deployment and presentation | Core product algorithms |

## Dependency rules

`backend/contracts` depends on no owned subsystem. Agent 1 and Agent 2 depend on contracts, not on each other's internals. The protected simulation composition root wires protocols together. The dashboard consumes only the HTTP snapshot/command API and WebSocket event stream. No component reaches into another component's private state.

## Failure model

Robot failure, communication loss, battery exhaustion, conflicts, and deadlocks are explicit states/events. A coordination outage is injected as a service availability fault. Robot-local safety, local work, and eventual peer communication must continue without the coordinator. Recovery is event-driven: reassignment, replanning, yielding, priority changes, or return-to-charge.

## State and events

`SimulationSnapshot` is the dashboard's point-in-time projection and contains `revision` plus `last_event_sequence`. Events are immutable, versioned, typed, correlated, and monotonically sequenced. Consumers must tolerate delayed, missing, or conflicting observations and must never treat dashboard input as authoritative.

## API boundary

The bootstrap health endpoint is `GET /api/v1/health`. Planned integration endpoints are `GET /snapshot`, `GET /robots`, `GET /tasks`, `GET /events`, `GET /metrics`, `POST /commands`, and `WS /stream` under `/api/v1`. Agent 3 must use these real endpoints, not local fake domain algorithms.

## Simulation and scale

A seeded world, fixed simulation tick, 2D positions, obstacles/stations/chargers, and documented event retention make tests deterministic. Scale tests use the same contracts with 50 and 500+ robots. Results must be measured and reported honestly.
