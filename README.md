# Watcher - Workload-Aware Task Coordination for Heterogeneous Entities & Robots

Watcher is a software-only simulation foundation for coordinating 500+ heterogeneous mobile robots in a dense industrial environment. Task negotiation, allocation, route planning, space-time collision avoidance, right-of-way, deadlock detection, battery monitoring, and fault recovery are implemented and running end to end. The dashboard shell remains a truthful foundation and does not fake live telemetry.

## Stack

- Python 3.11+, FastAPI, Pydantic v2
- React 19, TypeScript, Vite, CSS
- pytest and GitHub Actions
- Agent 2 owns the backend 2D world/grid and safety runtime in `backend/simulation/` and `backend/safety/`

## Architecture

Task discovery → robot bids → peer negotiation → assignment → route request → safety and recovery → canonical state/events → React dashboard. The coordination service is not the execution authority; robot-local safety and work recovery must continue when it is unavailable.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [CONTRACTS.md](CONTRACTS.md). The Agent 1 + Agent 2 integration is documented in [docs/integrations/agent-1-agent-2-movement-safety.md](docs/integrations/agent-1-agent-2-movement-safety.md).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn backend.app.main:app --reload
```

The backend serves the demo fleet at `http://localhost:8000`:

```powershell
# authoritative state, metrics, robots, tasks, routes, world
curl http://localhost:8000/api/v1/snapshot
curl http://localhost:8000/api/v1/metrics
curl http://localhost:8000/api/v1/robots

# create a task: negotiation, allocation, route planning, and a safety pass
curl -X POST http://localhost:8000/api/v1/tasks -H "Content-Type: application/json" -d '{
  "task": {"task_id": "task-001", "target": {"x": 8.5, "y": 8.5}, "priority": 4,
           "required_capabilities": [], "estimated_duration_s": 20.0,
           "status": "pending", "assigned_robot_id": null, "created_at_s": 0.0}}'

# simulation time is advanced explicitly, so every run is reproducible
curl -X POST "http://localhost:8000/api/v1/simulation/advance?ticks=100"

# canonical events after a cursor
curl "http://localhost:8000/api/v1/events?after_sequence=0"

# fault injection
curl -X POST http://localhost:8000/api/v1/faults/failure -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-001", "failure": {"kind": "actuator", "code": "drive-failure", "detected_at_s": 0.0}}'
curl -X POST http://localhost:8000/api/v1/faults/communication-loss -H "Content-Type: application/json" \
  -d '{"robot_id": "robot-002", "timeout_s": 3.0}'

# controls
curl -X POST http://localhost:8000/api/v1/simulation/pause
curl -X POST http://localhost:8000/api/v1/simulation/resume
curl -X POST http://localhost:8000/api/v1/simulation/speed -H "Content-Type: application/json" -d '{"multiplier": 4.0}'
curl -X POST http://localhost:8000/api/v1/simulation/reset -H "Content-Type: application/json" -d '{"seed": 2026}'
```

In another terminal:

```powershell
cd dashboard
npm install
npm run dev
```

Open `http://localhost:5173`. The current React screen is a truthful foundation shell and does not fake live telemetry.

## Tests

```powershell
python -m pytest                       # everything: 316 tests
python -m pytest tests/contract        # 27  contracts and HTTP surface
python -m pytest tests/unit            # 250 component tests
python -m pytest tests/integration     # 30  end-to-end scenarios
python -m pytest tests/scalability -s  # 9   500-robot fixture + measured costs
python -m pytest -m "not scalability"  # skip the large-fleet measurements
cd dashboard
npm run typecheck
npm run build
```

The scalability suite prints what the backend actually costs, which is the evidence behind the design decision to keep exhaustive conflict checking:

```text
build a 500-robot fixture: 30.2 ms
initialise a 500-robot runtime: 30.8 ms
plan 50 routes: 1.9 ms  (0.04 ms/route)
check 1225 trajectory pairs at N=50: 14.6 ms
check 123753 trajectory pairs at N=500: 1708.6 ms
500-robot fixture memory: 1.2 MiB retained, 1.3 MiB peak
```

## Parallel agent workflow

Read [AGENTS.md](AGENTS.md), [INTEGRATION.md](INTEGRATION.md), and your prompt in `agents/`. Work in a separate clone or Git worktree on the assigned branch; never share one working directory.

- Agent 1: `feat/agent-1-negotiation`
- Agent 2: `feat/agent-2-safety`
- Agent 3: `feat/agent-3-dashboard`
- Agent 4: `feat/agent-4-demo`

Protected shared interfaces are integration-controlled. Never silently change a schema, event, API, or serialization format.

## Current status

Implemented: repository structure, typed model/event/command contracts, protocol interfaces, fixtures, negotiation with bidding/selection/assignment, dynamic reassignment, deterministic world and fleet construction, footprint-aware A* route planning, time-aware trajectory generation, space-time collision detection, right-of-way resolution, deadlock detection, battery and fault observation, the simulation runtime, the Agent 1 ↔ Agent 2 composition root, the simulation HTTP surface, the health endpoint, the React dashboard shell, and 316 passing tests.

Not yet implemented: safe holding-position recovery and alternate-route replanning (marked experimental), a WebSocket event transport, allocation that accounts for route reachability, and a live dashboard wired to the HTTP surface.

## Team and deployment

Public deployment URL: **not yet created**.  
GitHub repository: `https://github.com/Praneeth-Gandodi/Watcher`  
Team members: **to be supplied by the team**.

Do not claim the deployed system is complete until Agent 4 verifies a live URL that matches this source revision.
