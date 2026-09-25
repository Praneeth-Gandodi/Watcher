# Agent 3 — Dashboard and Visualization

## Mission

Turn the React + TypeScript + Vite shell into a live, accessible fleet dashboard. Display canonical robot/task/route/conflict/failure state, event logs, metrics, and simulation/fault controls. Consume real backend interfaces.

## Ownership

May modify:

- `dashboard/**`
- `tests/unit/dashboard/**`
- Agent-owned dashboard integration tests
- dashboard-specific documentation

Must not modify:

- `backend/contracts/**`
- backend algorithms or protected composition root
- `backend/negotiation/**`, `backend/allocation/**`, `safety/**`
- deployment/presentation or another agent's tests
- protected root documents without an integration PR

## Inputs and outputs

Consume `SimulationSnapshot`, canonical events, metrics, and validated control commands over the documented HTTP/WebSocket interfaces. Do not duplicate negotiation, pathfinding, collision, deadlock, or battery logic in the UI. Do not create fake production behavior; fixtures may be used only in tests or clearly marked development states.

## Required workflow

Read shared docs first. Preserve the existing design tokens and accessible shell. Build small components around a typed API client. Handle loading, empty, disconnected, stale, and error states. Test rendering and command payload construction. Run `npm run typecheck`, `npm run build`, and relevant backend contract tests.

## Rules

Use real endpoints, React state discipline, keyboard/focus support, reduced motion, and responsive layouts. Do not add a second domain model without a formal OpenAPI/client generation decision. Do not alter public event names or fake backend behavior to make the UI work.

## PR

Use branch `feat/agent-3-dashboard`, small commits, and a PR. Final report must list components, endpoints/events consumed, commands produced, tests, accessibility/performance notes, and limitations. Ask for human input only for genuine contract ambiguity or an unavailable endpoint, not for ordinary UI decisions.
