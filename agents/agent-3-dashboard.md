# Agent 3 — React Dashboard and Visualization

## Mission

Turn the React + TypeScript + Vite shell into a live, accessible 2D fleet dashboard. Render the backend `WorldState`, robot positions, routes, conflicts, deadlocks, failures, tasks, metrics, and events. Implement the visual grid and controls; do not implement backend simulation behavior.

## Ownership

May modify:

- `dashboard/**`
- `tests/unit/dashboard/**`
- Agent-owned dashboard integration tests
- dashboard-specific documentation

Must not modify:

- `backend/contracts/**`
- `backend/simulation/**`
- `backend/negotiation/**`, `backend/allocation/**`, `safety/**`
- deployment/presentation or another agent's tests
- protected root documents without an integration PR

## Backend dependency

Read `CONTRACTS.md` and `docs/architecture/visualization.md` before coding. Agent 2 owns the backend grid/world and movement. Agent 3 consumes:

- `WorldState` for dimensions, cell size, and sparse grid cells
- `Robot` for world-space positions and statuses
- `RoutePlan` for world-space waypoints
- `Conflict` and `DeadlockReport` for overlays
- `SimulationSnapshot` for the complete dashboard projection
- Canonical WebSocket events after the snapshot cursor

The UI must never calculate authoritative robot positions, routes, collisions, or deadlock outcomes.

## Rendering requirements

Implement:

1. 2D industrial grid and world boundary
2. Obstacles, resources, workstations, and charging cells
3. Robot markers with status text/shape and accessible detail selection
4. Route polylines and waypoints
5. Conflict and right-of-way markers
6. Deadlock cycle overlays
7. Battery, failed, offline, and communication-lost indicators
8. Pan, zoom, reset camera, pause, resume, reset, and fault-injection controls
9. Loading, disconnected, stale, empty, and error states
10. Efficient rendering for 500+ robots

Use React for panels and controls. Use Canvas for the high-volume grid/robot layer; do not create 500 separate DOM elements for robots. Batch WebSocket events and draw on animation frames.

## Inputs and outputs

Consume canonical REST snapshots and WebSocket events. Produce only validated control commands. Do not duplicate negotiation, pathfinding, collision, deadlock, or battery logic in the UI. Fixtures may be used only in tests or clearly marked development states.

## Required workflow

1. Read shared docs first and preserve the existing design tokens.
2. Build a typed API client around the canonical API; do not invent domain models.
3. Implement grid rendering and state updates behind small components/hooks.
4. Test coordinate conversion, camera controls, marker selection, loading/error states, and 500+ fixture rendering.
5. Run `npm run typecheck`, `npm run build`, and relevant backend contract tests.

## Rules

Use real backend endpoints, accessible keyboard/focus behavior, reduced motion, responsive layouts, and honest empty/disconnected states. Do not fake backend behavior to make the UI work. Do not add a second event vocabulary or modify public schemas.

## PR

Use branch `feat/agent-3-dashboard`, small commits, and a PR. Final report must list components, endpoints/events consumed, commands produced, grid rendering approach, accessibility/performance notes, tests, and limitations. Ask for human input only for genuine contract ambiguity, unavailable backend endpoints, or ownership conflicts.
