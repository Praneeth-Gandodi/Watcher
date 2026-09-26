# WATCHER

### Decentralized Multi-Robot Coordination & Safety Simulation

Watcher is a software-only simulation of a dense industrial warehouse where heterogeneous mobile robots coordinate tasks, plan routes, avoid space-time conflicts, and recover from failures.

**Repository:** https://github.com/Praneeth-Gandodi/Watcher  
**Live demo:** https://watcher-l439.onrender.com

---

## Why Watcher?

Industrial robot fleets are not just a pathfinding problem. Robots may have different capabilities, sizes, speeds, battery levels, workloads, and priorities. A route that is safe for one robot can become unsafe when another robot reaches the same space at the same time.

Watcher combines:

- decentralized-style task negotiation and reassignment
- footprint-aware A* route planning
- time-aware trajectories
- space-time collision prediction
- right-of-way handling
- deadlock detection and recovery
- battery-aware operation
- robot failure and communication-loss handling
- live monitoring through a 2D operations console

The coordination service can allocate work, but it is not the traffic authority controlling every movement. The simulation runtime executes movement and safety decisions, and the system communicates through canonical events.

---

## What the system does

A typical task lifecycle looks like this:

```text
             NEW TASK
                 |
                 v
        +------------------+
        | ROBOT ELIGIBILITY|
        +------------------+
                 |
                 v
        +------------------+
        | NEGOTIATION/BIDS |
        +------------------+
                 |
                 v
          TASK ASSIGNED
                 |
                 v
        +------------------+
        | A* PATHFINDING   |
        +------------------+
                 |
                 v
        +------------------+
        | TIME TRAJECTORY  |
        +------------------+
                 |
                 v
        +------------------+
        | SAFETY CHECK     |
        | collision / ROW  |
        +------------------+
                 |
           +-----+-----+
           |           |
         SAFE       CONFLICT
           |           |
           v           v
        MOVE        YIELD /
                    RECOVER
                       |
                       v
                 CONTINUE
```

During execution, battery, failures, communication state, and deadlocks can change the plan and trigger further coordination.

---

## Core capabilities

| Area | Watcher |
|---|---|
| **Fleet** | 10 mixed-size robots by default on a seeded **40 × 25** warehouse; footprint, speed, charge, and capability profiles cycle across the fleet |
| **Coordination** | Peer-to-peer auction — eligibility, weighted bid scoring, selection, assignment, reassignment |
| **Path planning** | Footprint-aware A* around warehouse obstacles |
| **Trajectory** | Timestamped movement based on robot speed |
| **Safety** | Space-time footprint collision prediction and documented right-of-way rules |
| **Deadlock** | Wait-for cycle detection and recovery |
| **Resilience** | Robot failure, communication loss, battery drain, task migration |
| **Events** | Canonical event stream for coordination, safety, recovery, and telemetry |
| **Console** | Live 2D map, routes, robot footprints, inspector, event log, metrics, and scenario demos |

---

## The key idea: space + time

A shared path is not automatically a collision.

Two robots can use the same physical cells safely if they reach those cells at different times.

```text
Robot A -----> X

Robot B -----> X
```

If:

```text
A reaches X at 3.0 s
B reaches X at 8.0 s
```

there is no time-overlapping conflict.

But if their physical footprints overlap during the same time interval, the safety engine reports a conflict.

This is why Watcher's trajectories carry both **position** and **time**, while collision detection also considers the robot's complete footprint rather than treating every robot as a single point.

---

## Heterogeneous robots

Robots are not assumed to be identical.

A robot profile can define:

```text
width
height
speed
battery
capabilities
```

For example:

```text
robot-001
2 × 2 footprint
2.0 m/s

robot-002
3 × 2 footprint
1.0 m/s
```

The path planner checks the full footprint against obstacles, while the trajectory and collision layers use the same footprint information.

---

## Safety and recovery

Watcher uses a layered safety strategy.

### 1. Predict conflicts

The safety engine compares future robot trajectories in both physical space and simulation time.

### 2. Resolve right-of-way

When two robots have a genuine conflict, the system applies the documented priority rules and determines which robot should yield.

### 3. Detect deadlocks

A wait-for graph can look like:

```text
robot-001 -> robot-004
robot-004 -> robot-007
robot-007 -> robot-001
```

The cycle identifies a deadlock, which can trigger recovery.

### 4. Handle degradation

The simulation tracks:

- low battery
- critical battery
- robot failure
- communication loss
- task reassignment

The goal is predictable recovery when conditions change.

---

## Reproducible simulation

Simulation time is explicit.

`POST /api/v1/simulation/advance` advances the clock instead of relying on a hidden background loop.

That makes scenarios reproducible and easy to demonstrate one event at a time. A seeded run can be paused, stepped, reset, and replayed using the same command sequence.

---

## Operations console

The frontend is designed as a retro-industrial command console rather than a generic admin dashboard.

The main 2D map shows:

- warehouse grid
- obstacles and industrial areas
- robots and their footprints
- active routes and trails
- tasks and destinations
- conflict locations
- charging/workstation areas
- robot state changes

Selecting a robot exposes its current operational state, including its task, position, battery, speed, footprint, route progress, and current action.

The console also provides scenario controls and an event stream for negotiation, safety, battery, failure, and recovery events.

---

## Architecture

```text
                        WATCHER CONSOLE
                 map / roster / inspector /
                 scenarios / event log
                           |
                         REST
                           |
                           v
                +------------------------+
                | backend/app            |
                | API + composition root |
                +-----------+------------+
                            |
                            v
                +------------------------+
                | simulation runtime     |
                | world / grid / clock   |
                | movement / telemetry   |
                +-----------+------------+
                            |
             +--------------+--------------+
             |                             |
             v                             v
    +------------------+          +------------------+
    | negotiation      |          | safety           |
    | eligibility      |          | A*               |
    | scoring          |          | trajectory       |
    | assignment       |          | collision        |
    | reassignment     |          | right-of-way     |
    +------------------+          | deadlock         |
                                  | battery / failure|
                                  +------------------+
             \                             /
              \                           /
               +-----------+-------------+
                           |
                           v
                    CONTRACTS / EVENTS
```

### Architectural boundary

`backend/contracts/` is the authoritative shared boundary.

The backend keeps contracts frozen and passes validated contract objects between subsystems. Agent 1 owns coordination and reassignment; Agent 2 owns movement, safety, and simulation; the composition root wires them together.

See:

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CONTRACTS.md](CONTRACTS.md)
- [INTEGRATION.md](INTEGRATION.md)
- [docs/integrations/agent-1-agent-2-movement-safety.md](docs/integrations/agent-1-agent-2-movement-safety.md)

---

## Repository layout

```text
backend/
├── contracts/              # Frozen models, events, commands
├── allocation/             # Eligibility and allocation logic
├── negotiation/            # Bids, scoring, assignment, reassignment
├── safety/                 # A*, trajectory, collision, ROW, deadlock, battery, failure
├── simulation/             # Grid, world, scenarios, runtime, telemetry
└── app/                    # API and composition root

frontend/                   # 2D operations console

tests/
├── contract/               # Contract and HTTP-surface validation
├── integration/            # End-to-end runtime scenarios
├── scalability/            # Large-fleet fixtures and measurements
└── unit/                   # Component tests
```

---

## Getting started

### 1. Clone the repository

```powershell
git clone https://github.com/Praneeth-Gandodi/Watcher.git
cd Watcher
```

### 2. Create a Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install the backend

```powershell
python -m pip install -e ".[dev]"
```

### 4. Start the backend

```powershell
python -m uvicorn backend.app.main:app --port 8000
```

### 5. Start the frontend

Open another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

The Vite development server proxies `/api` to the backend.

---

## API surface

Useful endpoints include:

```text
GET  /api/v1/health
GET  /api/v1/snapshot
GET  /api/v1/metrics
GET  /api/v1/telemetry
GET  /api/v1/events
POST /api/v1/tasks
POST /api/v1/simulation/advance
POST /api/v1/faults/failure
POST /api/v1/faults/communication-loss
POST /api/v1/faults/battery-drain
```

Example:

```powershell
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/snapshot
curl http://localhost:8000/api/v1/metrics
curl http://localhost:8000/api/v1/telemetry
curl "http://localhost:8000/api/v1/events?after_sequence=0"
```

Create a task:

```powershell
curl -X POST http://localhost:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{
    "task": {
      "task_id": "task-001",
      "target": {"x": 8.5, "y": 8.5},
      "priority": 4,
      "required_capabilities": [],
      "estimated_duration_s": 20.0,
      "status": "pending",
      "assigned_robot_id": null,
      "created_at_s": 0.0
    }
  }'
```

Advance the simulation:

```powershell
curl -X POST "http://localhost:8000/api/v1/simulation/advance?ticks=100"
```

Inject faults:

```powershell
curl -X POST http://localhost:8000/api/v1/faults/failure `
  -H "Content-Type: application/json" `
  -d '{"robot_id":"robot-001","failure":{"kind":"actuator","code":"drive-failure","detected_at_s":0.0}}'

curl -X POST http://localhost:8000/api/v1/faults/communication-loss `
  -H "Content-Type: application/json" `
  -d '{"robot_id":"robot-002","timeout_s":3.0}'

curl -X POST http://localhost:8000/api/v1/faults/battery-drain `
  -H "Content-Type: application/json" `
  -d '{"robot_id":"robot-003","percent":5.0}'
```

---

## Testing

### Backend

```powershell
python -m pytest
```

The current repository reports:

- **392** backend tests
- **103** contract and HTTP-surface tests
- **250** component/unit tests
- **30** integration scenarios
- **9** scalability fixtures/measurements

Run individual groups:

```powershell
python -m pytest tests/contract
python -m pytest tests/unit
python -m pytest tests/integration
python -m pytest tests/scalability -q -s
python -m pytest -m "not scalability"
```

### Frontend

```powershell
cd frontend
npm test
npm run typecheck
```

---

## Scalability

The scalability suite includes a 500-robot fixture and measures several stages on the recorded developer machine.

| Measurement | Recorded cost |
|---|---:|
| Build 500-robot fixture | ~29 ms |
| Initialise 500-robot runtime | ~32 ms |
| Plan 50 routes | ~2.6 ms |
| Generate 50 trajectories | ~4.7 ms |
| Check 1,225 trajectory pairs (N=50) | ~22 ms |
| Check 123,753 trajectory pairs (N=500) | ~2,210 ms |
| 500-robot fixture memory | ~1.3 MiB |
| Build 500-robot coordinator | ~41 ms |
| `CREATE_TASK` → negotiation → assignment → route | ~16 ms |

These are measurements from one developer machine, not universal hardware guarantees.

### Current scaling bottleneck

The collision/right-of-way stage performs exhaustive pairwise trajectory comparisons:

```text
N × (N - 1) / 2
```

Therefore:

```text
N = 10    → 45 pairs
N = 50    → 1,225 pairs
N = 500   → 124,750 pairs
```

This remains deliberately simple for the MVP. A future optimization would add broad-phase spatial/temporal pruning before detailed pairwise checks.

---

## Demo scenarios

The system is designed around short, reproducible scenarios.

### Normal operations
Robots negotiate tasks, plan routes, and move through the warehouse.

### Crossing traffic
Two or more robots approach shared space at overlapping times and trigger safety handling.

### Deadlock
A cyclic wait-for relationship is created and recovery is demonstrated.

### Battery
A robot is pushed into a low/critical battery condition and the system reacts.

### Failure
A robot fails while owning work and its unfinished task can be migrated through reassignment.

### Communication loss
A robot remains a physical participant in the world while its communication state becomes unavailable.

---

## Design decisions

### Why A*?

It provides a deterministic grid-based route planner and works naturally with obstacle-aware warehouse maps.

### Why time-aware trajectories?

Two robots can use the same route safely at different times, so geometry alone is not enough for collision prediction.

### Why model robot footprints?

A 3 × 2 robot cannot use the same corridors as a 1 × 1 robot. Safety decisions need the complete physical footprint.

### Why explicit simulation time?

It makes scenarios reproducible, debuggable, and easy to demonstrate one event at a time.

### Why events?

Negotiation, safety, failure, reassignment, and UI monitoring need a common stream of observable state changes.

---

## Known limitations

Watcher is a prototype, and its current limitations are documented rather than hidden.

- **Simulation, not hardware:** there is no physical actuator model, sensor noise, or real-world dynamics.
- **Collision checking is O(N²):** exhaustive trajectory-pair checking becomes the main bottleneck for large fleets.
- **No WebSocket transport yet:** the console currently uses polling.
- **Allocation is not fully route-aware:** a robot can win work that later turns out to be unreachable under its footprint/path constraints.
- **Movement is discrete per cell:** the current model does not simulate continuous vehicle dynamics.
- **The default demo fleet is small:** the primary console scenario uses 10 robots even though the backend includes a 500-robot scalability fixture.
- **Advanced recovery paths:** safe holding-position and some alternate-route recovery logic remain experimental and should not be presented as the primary guaranteed recovery mechanism.

---

## Deployment

**Live demo:** https://watcher-l439.onrender.com

The application is deployed for demonstration purposes.

## Team

- Praneeth
- Chandu
- Nithin
- Karthik
