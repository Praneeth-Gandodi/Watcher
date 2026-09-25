# Watcher

Watcher is a software-only simulation foundation for coordinating 500+ heterogeneous mobile robots in a dense industrial environment. The repository is intentionally a **scaffold**: the dashboard shell and protected contracts are runnable, while negotiation, safety, live simulation, and deployment remain owned by parallel implementation agents.

## Stack

- Python 3.11, FastAPI, Pydantic v2
- React 19, TypeScript, Vite, CSS
- pytest and GitHub Actions

## Architecture

Task discovery → robot bids → peer negotiation → assignment → route request → safety and recovery → canonical state/events → React dashboard. The coordination service is not the execution authority; robot-local safety and work recovery must continue when it is unavailable.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [CONTRACTS.md](CONTRACTS.md).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn backend.app.main:app --reload
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
python -m pytest tests/contract
python -m pytest
cd dashboard
npm run typecheck
npm run build
```

## Parallel agent workflow

Read [AGENTS.md](AGENTS.md), [INTEGRATION.md](INTEGRATION.md), and your prompt in `agents/`. Work in a separate clone or Git worktree on the assigned branch; never share one working directory.

- Agent 1: `feat/agent-1-negotiation`
- Agent 2: `feat/agent-2-safety`
- Agent 3: `feat/agent-3-dashboard`
- Agent 4: `feat/agent-4-demo`

Protected shared interfaces are integration-controlled. Never silently change a schema, event, API, or serialization format.

## Current status

Implemented now: repository structure, typed model/event/command contracts, protocol interfaces, fixtures, contract tests, minimal health API, and React dashboard shell. Not yet implemented: the distributed negotiation algorithm, planner, collision/deadlock engines, simulation runtime, live dashboard, deployment, and benchmark results.

## Team and deployment

Public deployment URL: **not yet created**.  
GitHub repository: `https://github.com/Praneeth-Gandodi/Watcher`  
Team members: **to be supplied by the team**.

Do not claim the deployed system is complete until Agent 4 verifies a live URL that matches this source revision.
