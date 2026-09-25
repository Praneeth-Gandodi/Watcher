# Shared Contracts

`backend/contracts/**` is protected and authoritative. The Pydantic models are the executable contract; this document explains the interoperability rules. All identifiers use lowercase kebab case. Simulation time is a non-negative float in seconds. Extra fields are rejected.

## Models

- **WorldState**: `width_m`, `height_m`, `cell_size_m`, `columns`, `rows`, sparse `GridCell` entries, and `revision`. Cell types are `free`, `obstacle`, `resource`, `charging`, `workstation`, and `deadzone`; unlisted cells are free. Agent 2 owns this backend world.
- **GridCell**: integer `cell_x`, integer `cell_y`, and typed `cell_type` within world bounds.
- **Robot**: `robot_id`, world-space `position`, `battery_percent` (0–100), `capabilities`, `workload`, `status`, `current_task_id`, `communication_state`, optional `failure`, and `last_updated_at_s`.
- **Task**: `task_id`, `target`, `priority` (1–5), `required_capabilities`, `estimated_duration_s`, lifecycle `status`, optional `assigned_robot_id`, and `created_at_s`.
- **Bid**: `bid_id`, `robot_id`, `task_id`, `total_cost`, distance/battery/workload contributions, estimated completion time, creation time, and validity deadline. `valid_until_s` must be later than creation time.
- **RoutePlan**: route/task/robot IDs, at least two waypoints, strategy, status, version, and planning time.
- **Conflict**/**DeadlockReport**/**RecoveryAction**: observable safety state and recovery records; algorithms remain in Agent 2.
- **SimulationSnapshot**: current `WorldState`, robots/tasks/routes/conflicts, metrics, controller availability, revision, and event cursor.
- **SystemMetrics**: fleet/task/conflict/recovery/allocation performance counters.

## Commands

The dashboard sends only `CREATE_TASK`, `INJECT_ROBOT_FAILURE`, `INJECT_COMMUNICATION_LOSS`, `RESTORE_ROBOT`, `PAUSE_SIMULATION`, `RESUME_SIMULATION`, `RESET_SIMULATION`, and `SET_SIMULATION_SPEED`. Commands are versioned, correlated, validated, and never direct state mutations in the UI.

## Event vocabulary

| Event | Producer | Consumers | Required payload | Optional fields | Meaning | Kind |
|---|---|---|---|---|---|---|
| `TASK_CREATED` | runtime/command adapter | Agent 1, Agent 3 | `task` | — | A task entered the queue | state-changing |
| `NEGOTIATION_STARTED` | Agent 1 | Agent 3 | task ID, candidate IDs, expiry | — | A bid round opened | informational |
| `BID_SUBMITTED` | Agent 1 | Agent 3 | `bid` | — | A robot proposed eligibility/cost | informational |
| `TASK_ASSIGNED` | Agent 1 | Agent 2, Agent 3 | `assignment` | — | Ownership changed to a robot | state-changing |
| `TASK_REASSIGNED` | Agent 1 | Agent 2, Agent 3 | task, previous/new robot, reason | trigger event ID | Work migrated | state-changing |
| `ROUTE_REQUESTED` | Agent 2 | Agent 3 | task, robot, origin, target | — | A route is needed | informational |
| `ROUTE_PLANNED` | Agent 2 | Agent 3 | `route` | — | A versioned route exists | state-changing |
| `CONFLICT_DETECTED` | Agent 2 | Agent 2, Agent 3 | `conflict` | — | Spatial/resource conflict observed | state-changing |
| `DEADLOCK_DETECTED` | Agent 2 | Agent 2, Agent 3 | `report` | — | Cyclic waiting identified | state-changing |
| `ROUTE_REPLANNED` | Agent 2 | Agent 3 | route, reason | — | A new route version was selected | state-changing |
| `BATTERY_LOW` | Agent 2 | Agent 1, Agent 3 | robot, battery, threshold, range | — | Energy margin is unsafe | informational |
| `ROBOT_FAILED` | Agent 2 | Agent 1, Agent 3 | robot, failure | — | Robot cannot continue | state-changing |
| `COMMUNICATION_LOST` | Agent 2 | Agent 1, Agent 3 | robot, last contact, timeout | — | Peer heartbeat timed out | state-changing |
| `RECOVERY_STARTED` | Agent 2 | Agent 1, Agent 3 | `action` | — | Recovery action entered execution | informational |
| `TASK_COMPLETED` | Agent 2 | Agent 1, Agent 3 | task, robot, start/end times | — | Mission work completed | state-changing |

Every event has `event_id`, monotonically increasing `sequence`, `schema_version=1`, `producer`, `correlation_id`, `occurred_at_s`, and a typed payload whose `event_type` matches the envelope.

## Change control

Proposed change → update this file → update `backend/contracts` → update fixtures/contract tests → update affected agent docs → integration PR → full tests → review → merge. No implementation agent may silently mutate this contract.
