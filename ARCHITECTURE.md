# Architecture

## Purpose and boundaries

Watcher is a deterministic, software-only multi-agent simulation. It does not require physical robot hardware. The one-day MVP uses a Python simulation process, an in-process event adapter, and a React dashboard. The interfaces are designed so the coordination service and robot agents can be separated or fault-injected without making the API the decision authority.

```text
Task command -> task broadcast -> candidate discovery -> robot bids
    -> peer negotiation -> task assignment
    -> Agent 2 world/grid and route request
    -> planning, movement, and safety
    -> conflict/deadlock detection -> recovery
    -> task completion and canonical events
    -> SimulationSnapshot + WebSocket event stream -> React dashboard
```

## Subsystems

| Subsystem | Owns | Does not own |
|---|---|---|
| Agent 1 | Eligibility, bidding, negotiation, allocation, reassignment, decision events | Planning, movement, collision, deadlock, UI |
| Agent 2 | Backend 2D world/grid, robot movement, route planning, conflict/right-of-way, deadlock, battery, failure and communication recovery; publishes safety events | Negotiation internals, dashboard rendering |
| Agent 3 | React dashboard, state/event visualization, metrics, commands, fault controls | Backend algorithms, fake domain state |
| Agent 4 | README, architecture diagrams, demos, benchmarks, deployment and presentation | Core product algorithms |

## Dependency rules

`backend/contracts` depends on no owned subsystem. Agent 1 and Agent 2 depend on contracts, not on each other's internals. The protected simulation composition root wires protocols together. The dashboard consumes only the HTTP snapshot/command API and WebSocket event stream. No component reaches into another component's private state.

Concretely:

- `backend/negotiation` and `backend/allocation` import nothing from `backend/safety` or `backend/simulation`.
- `backend/safety` and `backend/simulation` import nothing from `backend/negotiation` or `backend/allocation`. Agent 2 reaches Agent 1 only through `SimulationRuntime.handle_event`, which understands `TASK_ASSIGNED` and `TASK_REASSIGNED`.
- `backend/app/composition.py` is the only module that imports both, and it exchanges nothing but frozen contract objects. Tests assert all three rules.

`SimulationRuntime`'s `InMemoryEventStream` is the single sequence authority for the whole system, so Agent 1 and Agent 2 events share one monotonic numbering and a consumer needs only one cursor.

## Failure model

Robot failure, communication loss, battery exhaustion, conflicts, and deadlocks are explicit states/events. A coordination outage is injected as a service availability fault. Robot-local safety, local work, and eventual peer communication must continue without the coordinator. Recovery is event-driven: reassignment, replanning, yielding, priority changes, or return-to-charge.

## State and events

`SimulationSnapshot` is the dashboard's point-in-time projection and contains `WorldState`, `revision`, and `last_event_sequence`. Agent 2 owns the authoritative world/grid and publishes its state through this projection. Events are immutable, versioned, typed, correlated, and monotonically sequenced. Consumers must tolerate delayed, missing, or conflicting observations and must never treat dashboard input as authoritative.

## API boundary

The health endpoint is `GET /api/v1/health`. The implemented simulation surface is
`GET /snapshot`, `GET /metrics`, `GET /robots`, `GET /tasks`, `GET /routes`,
`GET /world`, `GET /events?after_sequence=N`, `POST /tasks`,
`POST /commands`, `POST /faults/failure`, `POST /faults/communication-loss`,
`POST /faults/restore`, and `POST /simulation/advance|pause|resume|speed|reset`
under `/api/v1`. `POST /commands` validates with the contracts' own
`parse_command` union, so the HTTP surface and the contract cannot disagree
about what a valid command is. The WebSocket (`WS /stream`) is still to do; it
must carry the same canonical envelopes the polling endpoint serves and must not
introduce a second domain contract. Agent 3 must use these real endpoints, not
local fake domain algorithms.

Simulation time is advanced explicitly through `POST /simulation/advance`. The
backend never derives simulation state from wall-clock time, so a recorded
command sequence always reproduces the same run. The single wall-clock number in
the system is `SystemMetrics.average_allocation_latency_ms`, which is
informational.

## Simulation and scale

A seeded world, fixed simulation tick, 2D positions, obstacles/stations/chargers,
and documented event retention make tests deterministic. The grid convention is
authoritative in `backend/simulation/grid.py`: `x` is the column, `y` is the
row, Python indexing is `grid[cell_y][cell_x]`, `cell_y` increases upward, a
robot cell position is the top-left footprint anchor, and world metres use cell
centres.

Conflict detection is exhaustive pairwise comparison, which is exact and correct
at the demo fleet size (45 pairs at 10 robots) and is the measured cost at
500 robots (1.7 s for 123 753 pairs). No spatial index or broad-phase pruning is
implemented; that is a deliberate deferral, not an oversight. Measured costs are
printed by `python -m pytest tests/scalability -s`. Results must be measured and
reported honestly.
