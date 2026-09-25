# Agent 2 — Backend World, Safety, Movement, and Recovery

## Mission

You own the backend simulation world and safety runtime. Implement deterministic 2D grid/world state, robot movement, route planning, collision prediction, right-of-way resolution, deadlock detection/recovery, battery constraints, robot failure handling, communication-loss handling, and safe replanning.

Agent 2 is responsible for the **backend source of truth** for robot positions, grid geometry, routes, conflicts, and safety state. Agent 3 renders your state; it does not calculate it.

## Ownership

May modify:

- `safety/**`
- `backend/simulation/world.py`
- `backend/simulation/grid.py`
- `backend/simulation/motion.py`
- `backend/simulation/faults.py`
- `backend/simulation/runtime.py` only through an integration-reviewed runtime change
- `tests/unit/safety/**`
- `tests/unit/simulation/**`
- Agent-owned safety and simulation integration tests
- safety-specific decision records

Must not modify:

- `backend/contracts/**`
- `backend/negotiation/**` or `backend/allocation/**`
- `dashboard/**`
- deployment, presentation, or another agent's tests
- protected root documents without an integration PR

## Backend grid/world contract

Read `CONTRACTS.md` and `docs/architecture/visualization.md`. Agent 2 must:

- Create `WorldState` with dimensions, cell size, columns, rows, sparse typed cells, and revision.
- Map `Position2D` to grid cells consistently.
- Keep obstacle/resource/charging/workstation/dead-zone geometry in backend state.
- Update robot positions during simulation ticks.
- Produce `SimulationSnapshot` state for Agent 3.
- Publish canonical safety/movement events through the event stream.
- Never make the dashboard calculate authoritative robot positions.

The backend grid is not a React concern. Agent 3 receives it through the snapshot.

## Inputs and outputs

Consume canonical `TaskAssigned`, `TaskReassigned`, `Robot`, `WorldState`, and relevant failure/battery events. Produce `RoutePlan`, `SafetyDecision`, `Conflict`, `DeadlockReport`, `RecoveryAction`, updated `SimulationSnapshot`, and canonical safety events. Never call or rewrite Agent 1's private negotiation logic.

## WebSocket responsibility

Agent 2's runtime publishes canonical events to the protected event stream. The protected API/integration layer exposes those events over WebSocket. Agent 3 consumes the stream. Agent 2 must not create a dashboard-specific WebSocket schema or duplicate event names.

## Required workflow

1. Read all shared docs and inspect the existing runtime.
2. Implement the world/grid and movement behind protected contracts.
3. Write unit tests for cell indexing, world bounds, path validity, collision/right-of-way, deadlock cycles, battery thresholds, timeout recovery, and replanning.
4. Add executable integration tests for collision, deadlock, battery, failure, communication loss, and controller outage scenarios.
5. Run focused tests, `python -m pytest tests/contract`, and the full available suite.
6. Document assumptions and integration impact in the PR.

## Rules

Keep the implementation simple and deterministic. Use simulation time, not wall-clock time. Do not use hidden shared state. Make recovery visible through state and canonical events. Do not implement negotiation, UI, deployment, or unrelated refactors. Do not disable tests.

## Commit policy

Do not wait until the entire safety/world implementation is complete to make one commit. Commit coherent slices such as world creation, grid indexing, movement, route planning, collision handling, deadlock recovery, battery handling, failure recovery, and tests as they become reviewable. Use focused messages such as `feat(agent-2): add world grid`, `fix(agent-2): prevent route boundary crossing`, and `test(agent-2): cover deadlock recovery`. Keep each commit single-purpose and passing its relevant tests. Before opening the PR, inspect `git log --oneline main..HEAD`; preserve the meaningful commit sequence and do not squash by default.

## PR

Use branch `feat/agent-2-safety`, make small reviewable commits, and open a PR. Final report must list files, tests, contracts/events affected, grid/world behavior, WebSocket events produced, integration dependencies, and known limitations. Ask for human input only for genuine contract ambiguity, ownership conflict, missing requirements, or unavoidable architecture change.
