# Watcher

Decentralized coordination for heterogeneous mobile robots in a dense
industrial environment.

Watcher is a software-only simulation of a warehouse floor where every robot
bids for its own work, plans its own route, yields its own right of way, and
recovers from its own failures. There is no central traffic controller. The
coordination service allocates work; it is not the execution authority, and the
floor keeps working when it goes away.

**Live demo:** _TODO: deployment URL_

**Repository:** <https://github.com/Praneeth-Gandodi/Watcher>

---

## What it does

A task arrives. Every robot that is free and capable of doing it computes a bid
from its distance, its workload, its remaining charge, and the task priority.
The best bid wins. The winner plans a footprint-aware route around the racking.
While it travels it predicts whether it will actually collide with anyone, in
space and in time, and yields if the documented priority order says it should. If
it cannot hold its position safely it retreats and replans. If it runs out of
battery it hands its task to a robot that can finish it. If it fails, its task
is migrated. If two robots end up waiting on each other, the cycle is detected
and broken.

Every one of those decisions is published as a canonical event. The console is a
view over that stream.

| | |
|---|---|
| **Fleet** | 10 mixed-size robots on a seeded 40 × 25 warehouse; footprint, speed, charge, and capability profiles cycle across the fleet, so a small demo already exercises 1×1, 2×2, and 3×2 bodies |
| **Coordination** | Peer-to-peer auction — eligibility, weighted bid scoring, selection, assignment, reassignment |
| **Safety** | Footprint-aware A*, time-aware trajectories, space-time collision prediction, documented right-of-way priority, safe holding, retreat and replan, wait-for cycle detection |
| **Resilience** | Robot failure, communication loss, battery reserve and forced drain, task migration on reassignment |
| **Console** | Live map with body/path/trail rendering, roster, inspector, event log by category, three themes, staged scenario demos that pause on the problem before the fix runs |

## Architecture

```
                    ┌──────────────────────────────┐
   operator ───────▶│  console  (frontend/)         │
                    │  map · roster · inspector     │
                    │  scenarios · event log        │
                    └───────────┬──────────────────┘
                          REST  │  poll
                                ▼
                    ┌──────────────────────────────┐
                    │  api  (backend/app/api.py)   │
                    │  composition root wires the  │
                    │  coordinator to the runtime  │
                    └───────────┬──────────────────┘
                                ▼
                    ┌──────────────────────────────┐
                    │  runtime (backend/simulation) │
                    │                              │
                    │  health → charge → allocate   │
                    │  → reserve → move → energy    │
                    │  → collide → yield → deadlock │
                    └───────┬──────────────┬───────┘
                            │              │
          ┌─────────────────┴──┐   ┌───────┴──────────────┐
          ▼                    ▼   ▼                      ▼
     negotiation            safety/                 contracts/
     eligibility,           pathfinding,            frozen models,
     scoring, engine,       collision, trajectory,  events, commands
     reassignment           right_of_way, deadlock,
                            battery, failure
```

Simulation time only advances when the console asks it to. `POST
/api/v1/simulation/advance` is the clock, so the same seed and the same command
sequence reproduce the same world, the same fleet, the same decisions, and the
same event identifiers. Nothing runs in the background that you cannot pause,
step, or rewind — which is also why a demo can hold on a deadlock and explain it
before the recovery runs.

`backend/contracts/**` is frozen and authoritative. Nothing reaches around it:
the runtime converts its mutable internal state into contract models only when
it builds a projection, and reassignment decisions are built in the negotiation
layer, not in the composition root or the runtime.

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRACTS.md](CONTRACTS.md), and
[INTEGRATION.md](INTEGRATION.md). The negotiation-to-safety integration is
documented in
[docs/integrations/agent-1-agent-2-movement-safety.md](docs/integrations/agent-1-agent-2-movement-safety.md).

## Running it

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn backend.app.main:app --port 8000
```

Then the console, in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The console is already pointed at the backend —
Vite proxies `/api` to `http://127.0.0.1:8000`, so the browser never needs a
CORS grant and the base URL lives in one place. Point it elsewhere with
`VITE_API_BASE_URL`.

The clock is explicit, so the API is worth knowing on its own:

```powershell
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/snapshot
curl http://localhost:8000/api/v1/metrics
curl http://localhost:8000/api/v1/telemetry

# create a task: negotiation, allocation, route planning, and a safety pass
curl -X POST http://localhost:8000/api/v1/tasks -H "Content-Type: application/json" -d '{
  "task": {"task_id": "task-001", "target": {"x": 8.5, "y": 8.5}, "priority": 4,
           "required_capabilities": [], "estimated_duration_s": 20.0,
           "status": "pending", "assigned_robot_id": null, "created_at_s": 0.0}}'

# advance the clock
curl -X POST "http://localhost:8000/api/v1/simulation/advance?ticks=100"

# canonical events after a cursor
curl "http://localhost:8000/api/v1/events?after_sequence=0"

# faults
curl -X POST http://localhost:8000/api/v1/faults/failure -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-001", "failure": {"kind": "actuator", "code": "drive-failure", "detected_at_s": 0.0}}'
curl -X POST http://localhost:8000/api/v1/faults/communication-loss -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-002", "timeout_s": 3.0}'
curl -X POST http://localhost:8000/api/v1/faults/battery-drain -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-003", "percent": 5.0}'
```

### Tests

```powershell
python -m pytest                        # 392: unit, contract, integration, scalability
python -m pytest tests/contract         # 103 contract and HTTP surface
python -m pytest tests/unit             # 250 component
python -m pytest tests/integration      #  30 end-to-end scenarios
python -m pytest tests/scalability -s   #   9 large-fleet fixture and cost measurements
python -m pytest -m "not scalability"   # skip the large-fleet measurements
```

```powershell
cd frontend
npm test          # camera, geometry, interpolation, scene rendering
npm run typecheck
```

## Results

The integration suite runs the real runtime with no mocks, and the scalability
suite prints its own numbers and asserts its own budgets. Re-run them rather
than trusting this table:

```powershell
python -m pytest tests/scalability -q -s
```

Measured on one developer machine at `5b958ca`:

| Measurement | Cost |
|---|---|
| Build a 500-robot fixture | ~29 ms |
| Initialise a 500-robot runtime | ~32 ms |
| Plan 50 routes | ~2.6 ms (0.05 ms/route) |
| Generate 50 trajectories | ~4.7 ms |
| Check 1,225 trajectory pairs (N=50) | ~22 ms |
| Check 123,753 trajectory pairs (N=500) | ~2,210 ms |
| 500-robot fixture memory | 1.3 MiB retained, 1.3 MiB peak |
| Build a 500-robot coordinator | ~41 ms |
| `CREATE_TASK` → negotiation → assignment → route | ~16 ms (505 events) |

**The last row of that table is the honest one.** Conflict detection is an
exhaustive pairwise pass: for *N* robots the right-of-way stage costs
`N * (N - 1) / 2` trajectory comparisons. The module says so in its own
docstring — *45 pairs at N=10, and remains the right choice for the MVP* — and
there is no broad-phase pruning or spatial index in front of it. Every other
subsystem is built for and measured at 500 robots. That one is quadratic, which
is why the console demonstrates a small fleet rather than 500. Closing it is a
broad-phase index over predicted trajectories, not a rewrite.

Every figure above came out of a bug during development. A few of them, because
they are the interesting ones:

- A robot reported as `BLOCKED` kept changing cell. The move stage ignored the
  safety hold and went on consuming the original trajectory timestamps, so a
  held robot drove through whatever it was blocked by while only its status said
  otherwise. A held robot now does not move at all, and on release its route is
  re-timed from the cell it stopped on, so it resumes from a standstill instead
  of teleporting.
- Deadlock recovery was computed and published but never applied. The recovery
  emitted `RECOVERY_STARTED` and returned, so the victim kept waiting and the
  conflict stayed open: a detected deadlock never actually cleared. The chosen
  robot is now given a real planned route to the nearest cell with room for two
  robots, and its task route is replanned only once it has arrived. Retries
  continue on later safety passes, because the first retreat plan can be
  unplannable.
- One call rebound a task while the robot was still standing in the passage, and
  re-entered a commit from inside a commit. Arrival is now checked properly and
  the inner call can decline to commit.
- A robot's route detached from its body. Three overlapping strokes were drawn
  per robot and the visible line came from the stale `RoutePlan` a replan leaves
  behind. There is now one solid line per robot, taken from the committed timed
  trail, with a guard that stops the line where the trail is stale rather than
  cutting across the floor.
- Clicking a cell to place a task started a cancel instead of a pick, and the
  cell that was picked was then discarded. A finished run also looked frozen
  rather than finished, and `RESET` left an empty fleet instead of restarting
  the loaded scenario.

## Deployment

**Not yet deployed.** There is no container image and no host configuration in
this repository, and no live URL. The backend serves JSON only, so a deployment
needs either a single process that also serves the built console, or a static
host for the console plus the API behind it. That work is outstanding.

When it exists, the URL goes at the top of this file. Do not describe a
deployment as working until a live URL has been opened and exercised against
this source revision.

## Limitations

Stated plainly, because a demo that overstates itself is worse than one that
does not.

- **This is a simulation, not hardware.** Every robot is a state machine. There
  is no physics, no actuator model, and no sensor noise. The coordination logic
  is real; the robots are not.
- **Conflict detection is O(N²).** See Results. This is the single biggest
  constraint on fleet size, and it is the reason the console does not show 500
  robots today.
- **The demo fleet is small and fixed.** The console starts with 10 mixed-size
  robots on a 40 × 25 floor. The count lives in
  `backend/app/composition.py` and is not configurable from the environment or
  the UI.
- **There is no WebSocket transport.** The console polls. Fine for a demo on one
  screen; not fine at scale.
- **Allocation ignores route reachability.** A robot can win a bid for work it
  then cannot route to.
- **A finished run can look stalled.** A run with no work left holds rather than
  announcing completion on every surface at once.

## Team

<!-- TODO: fill in before submitting -->

| Name | Role | Contribution |
|---|---|---|
| _TODO_ | _TODO_ | _TODO_ |

## Repository layout

| Path | Subsystem | What lives there |
|---|---|---|
| `backend/contracts/` | shared | Frozen models, events, commands, fixtures. Authoritative. |
| `backend/negotiation/` | coordination | Eligibility, scoring, auction engine, reassignment. |
| `backend/safety/` | safety | Pathfinding, collision, trajectory, right of way, deadlock, battery, failure, robot profiles. |
| `backend/simulation/` | simulation | World and fleet construction, grid, layouts, scenarios, telemetry, the runtime. |
| `backend/app/` | interface | HTTP surface, composition root, service lifecycle. |
| `frontend/` | console | The operations console. |
| `tests/contract/` | shared | Contract compliance for every published surface. |
| `tests/integration/` | shared | End-to-end scenarios, run against the real runtime. |
| `tests/scalability/` | shared | Large-fleet fixtures and the measured costs above. |

## Licence

_TODO: add the licence this project is released under._
