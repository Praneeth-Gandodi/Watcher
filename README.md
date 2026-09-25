# Watcher

Decentralized coordination for 500+ heterogeneous mobile robots in a dense
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

A task arrives. Robots that can do it and are free bid. The cheapest bid wins.
The winner plans a route around obstacles, other robots' reservations, and
dead zones. While it travels it predicts whether it will conflict with anyone
and yields if the documented priority order says it should. If it runs out of
battery it hands its task to a robot that can finish it and goes to charge. If
it fails, its task is migrated. If two robots end up waiting on each other, the
cycle is detected and broken.

Every one of those decisions is published as a canonical event. The console is a
view over that stream.

| | |
|---|---|
| **Fleet** | 500 heterogeneous robots: varied speed, battery, and capabilities |
| **Floor** | Seeded 540 × 320 m warehouse, ~2,200 rack blocks, 6 aisles, depots, workstations, charging pads, dead zones |
| **Coordination** | Peer-to-peer auction — bid, score, allocate. 8 bidders per round, nearest-first |
| **Safety** | A* with soft occupancy, predictive conflict detection, documented right-of-way priority, wait-for cycle detection |
| **Resilience** | Robot failure, communication loss, battery reserve, coordinator outage |
| **Console** | Live dual-theme map, virtualized roster, ⌘K finder, fault injection, measured performance |

## Architecture

```
                    ┌──────────────────────────────┐
   operator ───────▶│  console  (dashboard/)       │
                    │  map · roster · inspector    │
                    └───────┬──────────────┬───────┘
                     REST   │              │  WS /stream
                            ▼              ▼
                    ┌──────────────────────────────┐
                    │  api  (backend/app/)         │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  runtime  (backend/simulation)│
                    │                              │
                    │  health → charge → allocate  │
                    │  → reserve → move → energy   │
                    │  → collide → deadlock        │
                    └───────┬──────────────┬───────┘
                            │              │
              ┌─────────────┴───┐   ┌──────┴─────────────┐
              ▼                 ▼   ▼                    ▼
      negotiation            safety/              contracts/
      (agent 1)        pathfinding, collision,   frozen models,
                        deadlock, battery,        events, commands
                        failure
```

The tick order is fixed and documented because it is what makes a seeded run
reproducible. The same seed and the same commands produce the same world, the
same fleet, the same decisions, and the same event identifiers.

`backend/contracts/**` is frozen and authoritative. Nothing in the simulation or
the console reaches around it: the runtime converts its mutable internal state
into contract models only when it builds a projection, and the console validates
every payload it receives rather than casting it.

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRACTS.md](CONTRACTS.md), and
[dashboard/README.md](dashboard/README.md).

## Running it

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# build the console, then serve both from one process
cd dashboard; npm install; npm run build; cd ..

python -m uvicorn backend.app.main:app --port 8000
```

Open <http://localhost:8000>. Set the fleet size with the environment:

```powershell
$env:WATCHER_FLEET_SIZE = "500"
python -m uvicorn backend.app.main:app --port 8000
```

In development, run the two separately so the console hot-reloads:

```powershell
# terminal 1
python -m uvicorn backend.app.main:app --reload --port 8000
# terminal 2
cd dashboard; npm run dev
```

### Tests

```powershell
python -m pytest                  # 336 tests: unit, contract, integration A–H
cd dashboard; npm test            # 100 tests
cd dashboard; npm run typecheck
```

## Results

Measured on one developer machine, steady state, cyclic collector paused across
the measurement window. Re-run them yourself — the scalability test prints its
own numbers and asserts the budgets:

```powershell
python -m pytest tests/integration/agent2/test_scenario_h_scalability.py -q -s
```

| Fleet | Rate | Tick, mean | p95 | Deadlines missed | Snapshot build |
|---|---|---|---|---|---|
| 50 | 10 Hz | ~5 ms | ~13 ms | 0% | ~2 ms |
| 200 | 5 Hz | ~25 ms | ~38 ms | 0% | ~6 ms |
| 500 | 5 Hz | ~70–95 ms | ~115–275 ms | 2–8% | ~16 ms |

Coordination behaviour is asserted, not asserted-to: the integration suite runs
the real runtime with no mocks and requires that at 500 robots work completes,
conflicts are detected, deadlock cycles are detected and resolved, every robot
stays inside the world and out of the racking, and no robot ever holds two tasks.

Every figure above came out of a bug during development. A few of them, because
they are the interesting ones:

- Robots drove diagonally through rack blocks. The planner validated a
  string-pulled segment with a cell walk that skipped the cells a diagonal step
  passes between, so a long straight segment could clip a rack corner. A
  dense-sampling clearance test now covers it and fails against the old walk.
- Two robots could hold the same task. A task migrated twice in one tick and the
  second caller named the original holder, so the real holder kept its claim.
- The battery froze. Per-tick drain is finer than the two decimal places the
  wire contract keeps, and the runtime was feeding its own rounded value back
  into its own arithmetic.
- A task ended the instant a robot touched its target, so `estimated_duration_s`
  — a contract field — meant nothing, and the fleet looked busy for a moment and
  then stood idle.
- 500 robots on the original 200 × 120 m floor is one robot per four open cells,
  denser than the robots are wide. Every pair tripped the safety margin and
  conflict detection saturated. The floor now grows with the fleet.

## Deployment

One container, one URL. The FastAPI process serves the built console from the
same origin, so there is no CORS setup and no proxy to explain mid-demo.

```powershell
docker build -t watcher .
docker run --rm -p 8000:8000 -e WATCHER_FLEET_SIZE=500 watcher
```

Full instructions, configuration, and per-host notes are in
[docs/deployment.md](docs/deployment.md).

<!-- TODO: paste the deployment URL here once deployed -->

## Limitations

Stated plainly, because a demo that overstates itself is worse than one that
does not.

- **This is a simulation, not hardware.** Every robot is a state machine. There
  is no physics, no actuator model, and no sensor noise. The coordination logic
  is real; the robots are not.
- **Robot speeds are compressed.** Robots move at 1.6–3.2 m/s, faster than a
  real warehouse AMR. At 1.4 m/s a 120 m aisle crossing takes almost two minutes
  and a reviewer watching a live demo sees a fleet that appears to stand still.
  The values are documented rather than tuned silently.
- **The coordination service has no outage command.** The canonical command set
  has no coordinator-outage verb, so the outage is a seeded, documented fault
  schedule instead of an invented interface. The console reports the flag and
  the outage counters but cannot trigger one. Adding
  `INJECT_COORDINATOR_OUTAGE` to `CONTRACTS.md` is the recommended change.
- **Deadlock overlays in the console are event-derived.** `SimulationSnapshot`
  has no deadlock collection, so a cycle is shown until a recovery,
  reassignment, or completion touches one of its robots or tasks. The backend
  should publish authoritative active deadlock state.
- **The tick rate drops to 5 Hz above 200 robots.** Five coordination decisions
  a second per robot, which is ample for right-of-way at walking pace, but it is
  a real reduction in resolution and it is a deliberate trade-off.
- **The event log is bounded.** 10,000 events in memory, 400 in the console. A
  client that falls behind recovers through a snapshot, not by replay.
- **Single process.** The runtime is one authoritative world behind a lock.
  Horizontal scaling would need the world and its reservations moved out of
  process; that is an architecture change, not a configuration one.

## Team

<!-- TODO: fill in before submitting -->

| Name | Role | Contribution |
|---|---|---|
| _TODO_ | _TODO_ | _TODO_ |
| _TODO_ | _TODO_ | _TODO_ |
| _TODO_ | _TODO_ | _TODO_ |

## Repository layout

| Path | Owner | What lives there |
|---|---|---|
| `backend/contracts/` | shared | Frozen models, events, commands. Authoritative. |
| `backend/negotiation/` | agent 1 | Bid eligibility, scoring, auction, allocation. |
| `backend/simulation/` | agent 2 | World generation, grid indexing, the runtime. |
| `safety/` | agent 2 | Pathfinding, collision, deadlock, battery, failure. |
| `backend/app/` | agent 2 | HTTP and WebSocket surface, service lifecycle. |
| `dashboard/` | agent 3 | The operations console. |
| `tests/contract/` | shared | Contract compliance for every published surface. |
| `tests/integration/` | shared | Scenarios A–H, run against the real runtime. |

## Licence

_TODO: add the licence this project is released under._
