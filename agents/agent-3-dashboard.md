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

## Visual direction — professional operations product

The default `/` route must be the live fleet operations dashboard, not a marketing landing page. This is an industrial control surface for evaluators and operators, not a decorative AI-generated showcase.

Design constraints:

- Do not use a purple/violet-dominant palette, purple gradients, neon glow effects, generic glassmorphism, or excessive bloom.
- Use a restrained industrial palette: deep navy/slate surfaces, blue/cyan for active coordination, green for healthy/nominal, amber for warnings, red for critical failures, and neutral gray for inactive/unknown state.
- Define semantic color, spacing, typography, radius, and status tokens in CSS; avoid scattered raw colors and arbitrary gradients.
- Use a clear operational hierarchy: fleet map first, robot/task details second, metrics and event stream alongside or below it.
- Keep the interface information-dense but readable. Use compact panels, aligned data, tabular figures for IDs/metrics, and strong empty/loading/error states.
- Use neutral professional typography such as the system sans stack or a restrained UI font. Use monospace only for IDs, coordinates, and telemetry values.
- Use animation only to communicate state changes, event updates, or spatial continuity. Respect `prefers-reduced-motion`; do not animate purely for decoration.
- Never use fabricated robot counts, live-looking telemetry, or simulated backend values as if they came from the API. Development fixtures must be visibly labeled and must not be used as production behavior.
- If a marketing/demo page is added, keep it separate from `/`, label concept simulations clearly, and do not let it replace the operations dashboard.

The result should look like a credible industrial operations product: calm, precise, legible, and information-first—not a purple AI SaaS landing page.

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

## Commit policy

Do not wait until the entire dashboard is complete to make one giant commit. Commit coherent slices such as API client, snapshot state, WebSocket events, world renderer, robot markers, routes/conflicts, controls, professional visual system, and tests as they become reviewable. Use focused messages such as `feat(agent-3): connect snapshot client`, `fix(agent-3): handle stale event stream`, and `test(agent-3): cover 500 robot rendering`. Keep each commit single-purpose and passing its relevant checks. Before opening the PR, inspect `git log --oneline main..HEAD`; preserve the meaningful commit sequence and do not squash by default.

## PR

Use branch `feat/agent-3-dashboard`, small commits, and a PR. Final report must list components, endpoints/events consumed, commands produced, grid rendering approach, accessibility/performance notes, tests, and limitations. Ask for human input only for genuine contract ambiguity, unavailable backend endpoints, or ownership conflicts.
