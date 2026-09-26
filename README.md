# Watcher

Decentralized coordination and mission management for heterogeneous mobile
robots in a dense industrial environment.

Watcher is a software-only simulation of a warehouse floor where robots bid for
their own work, plan their own routes, resolve their own right of way, and
recover from their own failures. The coordination service allocates work. It is
not the execution authority, and the floor keeps working when it goes away.

**Live demo:** _TODO: deployment URL_

**Repository:** <https://github.com/Praneeth-Gandodi/Watcher>

---

## What it does

A task arrives. Every robot that is free and capable of doing it computes a bid
from its distance, its workload, its remaining battery, and the task priority.
The best bid wins. The winner plans a footprint-aware route around the racking,
then predicts whether it will actually collide with anyone in space *and* time.
If it does, the documented priority order decides who yields. If it cannot
safely hold position it retreats and replans. If two robots end up waiting on
each other, the cycle is detected and broken. If a robot runs low, it hands its
task to a robot that can finish it. If it fails outright, its task is migrated.

Every one of those decisions is published as a canonical event, and the console
is a view over that stream.

| Capability | Where it lives |
|---|---|
| Bid eligibility, scoring, auction, allocation, reassignment | `backend/negotiation/` |
| Footprint-aware A* planning, time-aware trajectories | `backend/safety/pathfinding.py`, `trajectory.py` |
| Space-time collision prediction | `backend/safety/collision.py` |
| Right-of-way, safe holding, replanning around a conflict | `backend/safety/right_of_way.py` |
| Deadlock detection and recovery | `backend/safety/deadlock.py` |
| Battery thresholds, charging, return-to-base | `backend/safety/battery.py` |
| Failure and communication-loss observation | `backend/safety/failure.py` |
| Heterogeneous robot profiles (size, speed, capability) | `backend/safety/robot_profile.py` |
| World and fleet construction, layouts, scenarios | `backend/simulation/` |
| The tick loop, reservations, energy, metrics | `backend/simulation/runtime.py` |
| HTTP surface and composition root | `backend/app/` |
| The operations console | `frontend/` |

## Architecture

```
                    ┌───────────────────────────────┐
   operator ───────▶│  console  (frontend/)         │
                    │  map · roster · inspector      │
                    │  scenarios · event log         │
                    └───────────┬───────────────────┘
                          REST  │  poll
                                ▼
                    ┌───────────────────────────────┐
                    │  api  (backend/app/api.py)    │
                    │  composition root wires the   │
                    │  coordinator to the runtime   │
                    └───────────┬───────────────────┘
                                ▼
                    ┌───────────────────────────────┐
                    │  runtime (simulation/)        │
                    │  world · grid · reservations  │
                    │  tasks · routes · events      │
                    └──────┬──────────────┬─────────┘
                           │              │
          ┌────────────────┴───┐   ┌──────┴──────────────┐
          ▼                    ▼   ▼                     ▼
    negotiation             safety/                contracts/
    eligibility,            pathfinding,           frozen models,
    scoring, engine,        collision, trajectory, events, commands
    reassignment            right_of_way, deadlock,
                            battery, failure
```

Two decisions shape everything else:

**The tick order is fixed and explicit.** Simulation time only advances when
`POST /api/v1/simulation/advance` is called. The same seed and the same command
sequence therefore reproduce the same world, the same fleet, the same decisions,
and the same event identifiers. The console drives the clock; nothing runs in the
background that you cannot pause, step, or rewind.

**`backend/contracts/**` is frozen and authoritative.** Nothing reaches around
it. The runtime converts its mutable internal state into contract models only
when it builds a projection, and the console validates every payload it receives
rather than casting it.

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRACTS.md](CONTRACTS.md), and
[INTEGRATION.md](INTEGRATION.md). The negotiation-to-safety integration is
documented in
[docs/integrations/agent-1-agent-2-movement-safety.md](docs/integrations/agent-1-agent-2-movement-safety.md).

## Stack

- Python 3.11+, FastAPI, Pydantic v2, pytest
- React 19, TypeScript, Vite, Vitest, plain CSS
- GitHub Actions

## Running it

Backend and console are two processes in development, one URL in production.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn backend.app.main:app --port 8000
```

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The console is already pointed at the backend: Vite
proxies `/api` to `http://127.0.0.1:8000`, so the browser never needs a CORS
grant and the base URL lives in one place. Override it with
`VITE_API_BASE_URL` if the backend is somewhere else.

## The HTTP surface

```powershell
# authoritative state and telemetry
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/snapshot
curl http://localhost:8000/api/v1/metrics
curl http://localhost:8000/api/v1/robots
curl http://localhost:8000/api/v1/tasks
curl http://localhost:8000/api/v1/routes
curl http://localhost:8000/api/v1/telemetry

# create a task: negotiation, allocation, route planning, and a safety pass
curl -X POST http://localhost:8000/api/v1/tasks -H "Content-Type: application/json" -d '{
  "task": {"task_id": "task-001", "target": {"x": 8.5, "y": 8.5}, "priority": 4,
           "required_capabilities": [], "estimated_duration_s": 20.0,
           "status": "pending", "assigned_robot_id": null, "created_at_s": 0.0}}'

# simulation time is advanced explicitly, so every run is reproducible
curl -X POST "http://localhost:8000/api/v1/simulation/advance?ticks=100"
curl -X POST http://localhost:8000/api/v1/simulation/pause
curl -X POST http://localhost:8000/api/v1/simulation/resume
curl -X POST http://localhost:8000/api/v1/simulation/speed \
  -H "Content-Type: application/json" -d '{"multiplier": 4.0}'
curl -X POST http://localhost:8000/api/v1/simulation/reset \
  -H "Content-Type: application/json" -d '{"seed": 2026}'

# canonical events after a cursor
curl "http://localhost:8000/api/v1/events?after_sequence=0"

# fault injection
curl -X POST http://localhost:8000/api/v1/faults/failure -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-001", "failure": {"kind": "actuator", "code": "drive-failure", "detected_at_s": 0.0}}'
curl -X POST http://localhost:8000/api/v1/faults/communication-loss -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-002", "timeout_s": 3.0}'
curl -X POST http://localhost:8000/api/v1/faults/restore -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-001"}'
```

## Tests

392 backend tests, all passing.

```powershell
python -m pytest                        # 392 in total
python -m pytest tests/unit             # 250 component tests
python -m pytest tests/contract         # 103 contract and HTTP surface tests
python -m pytest tests/integration      #  30 end-to-end scenarios
python -m pytest tests/scalability -s   #   9 large-fleet fixture and cost measurements
python -m pytest -m "not scalability"   # skip the large-fleet measurements
```

```powershell
cd frontend
npm test          # camera, geometry, interpolation, and scene rendering
npm run typecheck
```

The contract and integration suites run against the real runtime with no mocks.

## Performance, stated honestly

`tests/scalability/` prints what the backend actually costs, and asserts its own
budgets. Run it rather than trusting a number in a README:

```powershell
python -m pytest tests/scalability -s
```

**Conflict detection is exhaustive pairwise comparison.** For a fleet of *N*
robots the right-of-way pass costs `N * (N - 1) / 2` trajectory comparisons. The
code says so itself: *45 pairs at N=10, and remains the right choice for the
MVP.* There is no broad-phase pruning and no spatial index in front of it.

That is the honest shape of the scalability story. The subsystems are built and
measured independently at 500 robots — world construction, route planning,
trajectory generation, memory — and the fixture builds in tens of milliseconds.
What does not yet scale is the pairwise conflict pass, which grows quadratically
and is why the console demonstrates a small fleet rather than 500. Closing that
gap is a broad-phase index over predicted trajectories, not a rewrite.

## Deployment

**Status: not yet deployed.** There is no container image and no host
configuration in this repository yet, and no live URL. The backend serves JSON
only, so a deployment needs either a static host for the built console plus the
API, or a single process that serves both. That work is outstanding, and this
README will carry the URL when it exists.

Do not describe a deployment as working until a live URL has been opened and
exercised against this source revision.

## Limitations

Stated plainly, because a demo that overstates itself is worse than one that does
not.

- **This is a simulation, not hardware.** Every robot is a state machine. There
  is no physics, no actuator model, and no sensor noise. The coordination logic
  is real; the robots are not.
- **Conflict detection is O(N²).** See the performance section. This is the
  single biggest constraint on fleet size.
- **The demo fleet is small.** The console starts with 10 mixed-size robots on a
  40x25 floor. The fleet size is fixed in `backend/app/composition.py` and is not
  yet configurable from the environment or the UI.
- **No WebSocket transport.** The console polls. That is fine for a demo and
  would not be fine at scale.
- **Allocation ignores route reachability.** A robot can win a bid for work it
  then cannot route to.
- **Safe holding and replanning are the newest code** and the least exercised by
  anything outside their own tests.

## Team

<!-- TODO: fill in before submitting -->

| Name | Role | Contribution |
|---|---|---|
| _TODO_ | _TODO_ | _TODO_ |

## Licence

_TODO: add the licence this project is released under._
