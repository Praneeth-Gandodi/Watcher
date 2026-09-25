# Integration Changelog

Use one short entry per merged integration change:

```text
Agent:
Date:
Branch:
Change:
Contracts affected:
New events:
Consumed events:
Tests added:
Integration notes:
Known issues:
```

This file is append-only and integration-controlled.

```text
Agent: integration (Agent 1 negotiation + Agent 2 movement/safety)
Date: 2026-09-25
Branch: saftey
Change: Agent 2's standalone safety/ prototype flattened into backend/safety and
  wired to Agent 1 behind the composition root. Added backend/simulation grid,
  world, and runtime, and the minimum simulation HTTP surface. Removed the
  competing root safety/ package.
Contracts affected: none. backend/contracts/** is byte-identical to main. Robot
  physical metadata lives in a separate RobotProfileRegistry, not on Robot. The
  SafetyEngine and PathPlanner protocols are implemented unchanged; the
  fleet-wide safety pass is internal to FleetSafetyEngine.
New events: none. All 15 canonical event types are produced by existing schemas.
  Agent 2 produces ROUTE_REQUESTED, ROUTE_PLANNED, ROUTE_REPLANNED,
  CONFLICT_DETECTED, DEADLOCK_DETECTED, RECOVERY_STARTED, BATTERY_LOW,
  ROBOT_FAILED, COMMUNICATION_LOST, and TASK_COMPLETED.
Consumed events: TASK_ASSIGNED and TASK_REASSIGNED, handled by
  SimulationRuntime.handle_event. BATTERY_LOW, ROBOT_FAILED, and
  COMMUNICATION_LOST are consumed by Agent 1's ReassignmentService.
Tests added: 316 total (was 51). 27 contract, 250 unit, 30 integration
  (Agent 1's original 2 unchanged, 28 new for Agent 2), 9 scalability.
Integration notes: docs/integrations/agent-1-agent-2-movement-safety.md.
  backend/app/composition.py is the only module that imports both agents, and it
  exchanges nothing but frozen contract objects. The runtime's InMemoryEventStream
  is the single sequence authority for Agent 1 and Agent 2 events.
  Two additive sync cores were added to Agent 1 (decide_sync, reassign_sync); the
  existing async methods delegate to them, so behaviour is unchanged.
Known issues: allocation is blind to route reachability, so a task can be
  assigned to a body that cannot fit the only gap and is then reported blocked.
  A head-on corridor is reported and left open rather than resolved, because
  holding-position and replanning are still experimental. A parked robot occupies
  its cell until reset. Failed robots are removed from the coordination world
  rather than treated as static obstacles. Movement is discrete per cell. No
  WebSocket transport yet; /events?after_sequence=N polling is implemented.
  Exhaustive O(N^2) conflict checking is kept by design: 1.7 s at N=500.
```

```text
Agent: Agent 2 (backend world, movement, safety, recovery)
Date: 2026-09-25
Branch: saftey (merged into the integration above)
Change: backend/safety/** and backend/simulation/** implemented: footprint-aware
  A*, time-aware trajectories, space-time collision, earliest-conflict detection,
  deterministic right-of-way, wait-graph deadlock, battery observations, and
  failure/communication recovery.
Contracts affected: none.
New events: ROUTE_REQUESTED, ROUTE_PLANNED, ROUTE_REPLANNED, CONFLICT_DETECTED,
  DEADLOCK_DETECTED, RECOVERY_STARTED, BATTERY_LOW, ROBOT_FAILED,
  COMMUNICATION_LOST, TASK_COMPLETED.
Consumed events: TASK_ASSIGNED, TASK_REASSIGNED.
Tests added: 287 (250 unit, 28 integration, 9 scalability).
Integration notes: depends only on backend.contracts and
  backend.simulation.grid. Never imports backend.negotiation or
  backend.allocation; asserted by a test.
Known issues: safe holding position and alternate-route replanning are
  experimental and marked as such; they are not on the MVP resolution path.
```
