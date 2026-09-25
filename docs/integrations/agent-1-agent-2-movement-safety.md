# Agent 1 + Agent 2 integration: movement, safety, and recovery

Branch: `saftey` (integration of `main` with the Agent 2 safety/movement work)
Scope: Agent 2's movement and safety subsystem, integrated behind the existing
Agent 1 negotiation/allocation backend. No contract schema, event name, or
command was added, removed, or changed.

## What was integrated

Agent 2's standalone `safety/` prototype was flattened into `backend/safety/`
and wired to Agent 1 through canonical events only.

```text
backend/
  contracts/          unchanged, protected
  allocation/         unchanged, Agent 1
  negotiation/        unchanged, Agent 1 (two sync cores added, see below)
  safety/             NEW, Agent 2 algorithms
    robot_profile.py  RobotProfile + RobotProfileRegistry
    pathfinding.py    footprint-aware A* + PathPlanner adapter
    trajectory.py     time-aware trajectory generation
    collision.py      space-time collision detection
    right_of_way.py   earliest conflict + deterministic resolution
    deadlock.py       wait-graph cycles + RecoveryAction/DeadlockReport
    battery.py        thresholds, reserve, observations
    failure.py        failure / communication / restore transitions
  simulation/         Agent 2 world and runtime
    grid.py           cell<->metre conversion, occupancy, footprints
    world.py          deterministic seeded world and fleet
    runtime.py        clock, movement, safety, faults, events, projections
  app/
    composition.py    the composition root: the only module that knows both
    api.py            thin HTTP adapters (no decisions)
```

The old root-level `safety/` package was removed so there is exactly one package
naming convention.

## How Agent 1 and Agent 2 communicate

Neither side imports the other. `backend/app/composition.py` is the only module
that knows both exist, and it moves frozen contract objects between them.

```text
CREATE_TASK
    -> runtime.submit_task           -> TASK_CREATED
    -> Agent 1 negotiation           -> NEGOTIATION_STARTED, BID_SUBMITTED xN
                                     -> TASK_ASSIGNED
    -> runtime.handle_event          -> ROUTE_REQUESTED, ROUTE_PLANNED
                                     -> fleet safety pass
                                        -> CONFLICT_DETECTED, RECOVERY_STARTED
                                        -> DEADLOCK_DETECTED
                                     -> BATTERY_LOW / TASK_COMPLETED
    -> Agent 1 ReassignmentService   (on BATTERY_LOW, ROBOT_FAILED,
                                       COMMUNICATION_LOST)
                                     -> TASK_REASSIGNED
    -> runtime.handle_event          -> new route for the replacement robot
```

`SimulationRuntime.handle_event` is the only entry point by which Agent 1
reaches Agent 2, and it understands exactly two payloads. Conversely the runtime
never produces `TASK_ASSIGNED` or `TASK_REASSIGNED`; it reports safety
observations and lets Agent 1 decide who takes the work.

`InMemoryEventStream` is the single sequence authority for the whole system, so
Agent 1 and Agent 2 events share one monotonic numbering and the dashboard can
poll `/events?after_sequence=N` without merging two cursors.

## Contract and interface changes

**No contract model, event, command, or fixture changed.** `backend/contracts/**`
is byte-identical to `main`.

Two additive, backward-compatible changes:

| Change | Why |
|---|---|
| `DefaultNegotiationEngine.decide_sync` added; `decide` now delegates to it | The composition root runs in-process, and the negotiation is pure CPU. One implementation, so the async behaviour and the existing tests are unchanged. |
| `ReassignmentService.reassign_sync` added; `reassign` now delegates to it | Same reason. |

Both are private implementation details: the `NegotiationEngine` and
`ReassignmentService` protocols are untouched.

Two protocol requirements were solved **without** widening any interface:

* The frozen `Robot` contract carries no physical geometry. Footprint, speed,
  and battery rate live in `RobotProfileRegistry`, keyed by `robot_id`, and are
  resolved by `RobotProfileRegistry.get` with a documented 1x1 default.
* `SafetyEngine.evaluate_motion(route, observed_at_s)` only sees one route,
  which cannot answer a fleet question. `FleetSafetyEngine` implements that
  exact protocol and adds an internal `evaluate_fleet` used by the runtime. The
  composition root only ever sees the protocol.

## Documented conventions

* **Grid**: `x` is the column, `y` is the row, Python indexing is
  `grid[cell_y][cell_x]`, and `cell_y` increases upward per
  `docs/architecture/visualization.md`.
* **Metres**: cell centres, `x_m = (cell_x + 0.5) * cell_size_m`. Implemented
  once in `backend/simulation/grid.py`; `position -> cell -> position`
  round-trips exactly.
* **Footprint**: a robot cell position is the top-left anchor. A
  `width_cells x height_cells` body grows along `+x` then `+y`.
* **Timing**: `time_per_cell = cell_size_m / speed_mps`, constant per robot. All
  speed logic lives in `trajectory.py`, never in the planner.
* **Battery**: one percentage per traversed cell, configurable per profile via
  `battery_percent_per_cell`. A simulation assumption, not a physical claim.
* **Right-of-way score**: `task_priority * 100 + (100 - battery) * 0.5 +
  waiting_time * 2`, ties broken by `robot_id`. A weighted sum, not a
  lexicographic order: one priority point equals 200 s of waiting. A nearly empty
  robot outranks a full one at equal priority, because delaying it would burn
  the range it has left.
* **Conflict model**: swept footprint. A segment covers the union of the cells
  it departs from and arrives at, and the final segment extends to infinity
  because a parked robot still occupies space.

## Problems found by the tests, and what changed

1. **Delay released too early.** Releasing the yielder at the end of the
   overlapping window is not enough: a cell stays occupied across consecutive
   swept segments, so the identical conflict reappeared one segment later. The
   resolver now releases only after the other robot has finished sweeping every
   shared cell (`right_of_way.clearance_time_s`).
2. **A vetoed hold was abandoned.** A hold short enough for one cell can still
   be too short for a cell further along the same corridor. A vetoed hold now
   escalates to a longer release time, bounded by `MAX_DELAY_ATTEMPTS`.
3. **Permanent blockers raised instead of reporting.** A robot parked on a cell
   another must cross makes the conflict unresolvable; the clearance time is
   infinite. This is now reported as unresolved with an explicit reason.
4. **Battery drained quadratically.** Consumption re-applied the cumulative
   distance every commit. It now charges only newly traversed cells.
5. **A robot could win two tasks.** The task was bound to the robot on its first
   movement, so a stationary robot looked idle. It is now bound at assignment.
6. **Deadlock recovery lied.** Clearing a wait dependency marked the conflict
   resolved while the space-time conflict remained. Detection now reports and
   proposes a yield, and the conflict honestly stays open.
7. **One-waypoint routes.** A task targeting the robot's own cell produced a
   route with one waypoint, which `RoutePlan` forbids. It now repeats the point.
8. **Unroutable tasks raised.** They created a single-robot `Conflict`, which the
   contract forbids (`min_length=2`). They are now reported as blocked tasks.
9. **Conflict records multiplied.** Keying a conflict on its start time created a
   new record on every safety pass (30 open conflicts after one 10-robot run).
   A robot pair now holds at most one open record.

Prototype lessons that are enforced by tests: A* rejects negative anchors before
indexing; a wait is an explicit hold point, never a stretched segment; a hold is
validated against the whole fleet; a modified route is compared only against
future trajectories; trajectory indexes are never compared across speeds; a
shared route is not a collision; footprints are considered; frozen contracts are
rebuilt through the model so validation always runs.

## Tests

| Suite | Count | What it proves |
|---|---|---|
| `tests/contract` | 27 | contracts unchanged; HTTP surface; boundary rules |
| `tests/unit` | 250 | A* and footprints, sizes, speeds, trajectories, space-time collision, earliest conflict, right-of-way, deadlock, battery, failure, grid, world, runtime |
| `tests/integration/agent1` | 2 | Coder 1's original scenarios still pass, unmodified |
| `tests/integration/agent2` | 28 | the required 5-robot scenario and the 10-robot stress run |
| `tests/scalability` | 9 | deterministic 500-robot fixture and measured costs |
| **total** | **316** | `python -m pytest` |

The 5-robot scenario proves allocation, route planning through a single-cell
gap, a genuine conflict between two robots at different speeds, resolution by a
timing delay with no residual collision, continued simulation, battery drain with
`BATTERY_LOW`, a `ROBOT_FAILED` that Agent 1 turns into a `TASK_REASSIGNED`, and
a valid final snapshot -- and that two identical runs produce identical state
and identical events.

The 10-robot run asserts no exception, no negative or out-of-grid position, no
robot on an obstacle, no unresolved collision, valid metrics, and world
invariants on every tick.

## Measured scale cost

Measured on the development machine, printed by
`python -m pytest tests/scalability -s`:

| Measurement | Value |
|---|---|
| 500-robot fixture build | ~30 ms |
| 500-robot runtime init | ~31 ms |
| A* route planning | ~0.04 ms per route |
| trajectory generation | ~0.07 ms per robot |
| conflict check, N=50 (1 225 pairs) | ~15 ms |
| conflict check, N=500 (123 753 pairs) | ~1.7 s |
| 500-robot fixture memory | ~1.2 MiB |

These are the numbers behind the decision to keep exhaustive pairwise checking:
at N=10 it is 45 pairs and free, at N=500 it is 1.7 s per pass. The brief is
explicit that the ten-robot demo's correctness must not be traded for
500-robot optimisation, so no spatial index was added.

## Known limitations and FUTURE/EXPERIMENTAL work

* **Experimental and not on the MVP path** -- clearly marked in
  `backend/safety/right_of_way.py` under
  `# FUTURE / EXPERIMENTAL SAFETY RECOVERY`: `trim_trajectory_from_time`
  (an alias of the stable `trajectory_future_view`), `find_safe_holding_position`,
  `replan_around_conflict`, `evaluate_yield_options`, and
  `choose_safe_yielding_robot`. Nothing in `backend/simulation` or the
  composition root imports them, and the tests only assert they do not break the
  stable path.
* **Allocation is blind to reachability.** Agent 1 scores on distance, battery,
  and workload, so the cheapest bidder can be a 3x2 robot that cannot fit through
  a one-cell gap. The runtime then reports the task blocked. Feeding route
  feasibility back into bidding needs a new contract, so it was not invented
  here.
* **A head-on corridor is not resolved.** Neither robot can hold safely, so the
  pair is reported, both are marked `BLOCKED`, a `DEADLOCK_DETECTED` report and a
  `YIELD` recovery action are published, and the conflict stays open. Clearing it
  needs the experimental holding-position or replanning strategies.
* **A parked robot is a permanent obstacle.** A finished robot keeps a
  one-point trajectory extending to infinity, so a later route to the same cell
  conflicts. That is correct but means a destination cell is consumed until the
  world is reset.
* **A failed robot is removed from the coordination world.** Its trajectory is
  cleared, so safety does not treat it as a static obstacle. Physically it would
  still be in the way.
* **Movement is discrete.** `Robot.position` is the cell-centre of the anchor
  cell and advances one cell per `time_per_cell`. A dashboard wanting smooth
  motion must interpolate client-side from the route and timestamps.
* **`CONFLICT_DETECTED` has no resolution counterpart.** A resolved conflict is
  visible as `RecoveryAction(status=resolved)` plus the conflict record flipping
  to `resolved` in the snapshot. The event vocabulary has no
  `CONFLICT_RESOLVED`, and none was invented.
* **No WebSocket.** `/events?after_sequence=N` polling is implemented and tested;
  a WebSocket transport over the same stream is still to do.
* **Metrics.** `average_allocation_latency_ms` is the only wall-clock number in
  the system, and it is informational. Everything else uses simulation time.
