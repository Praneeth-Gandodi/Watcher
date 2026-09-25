# Agent 2 — Safety, Movement, and Recovery

## Mission

Implement movement and route planning, collision prediction, right-of-way resolution, deadlock detection/recovery, battery constraints, robot failure handling, communication-loss handling, and safe replanning. This is the safety subsystem, not the negotiation layer.

## Ownership

May modify:

- `safety/**`
- `backend/simulation/motion.py`, `backend/simulation/faults.py` and other explicitly assigned runtime files
- `tests/unit/safety/**`
- Agent-owned safety integration tests
- safety-specific decision records

Must not modify:

- `backend/contracts/**`
- `backend/negotiation/**` or `backend/allocation/**`
- `dashboard/**`
- deployment, presentation, or another agent's tests
- protected root documents without an integration PR

## Inputs and outputs

Consume canonical `TaskAssigned`, `TaskReassigned`, `Robot`, and relevant failure/battery events. Produce `RoutePlan`, `SafetyDecision`, conflict/deadlock/recovery records, and canonical safety events. Never call or rewrite Agent 1's private negotiation logic.

## Required workflow

Read all shared docs first and inspect the existing runtime. Write unit tests for path validity, collision/right-of-way, deadlock cycles, battery thresholds, timeout recovery, and replanning. Add real integration tests for scenarios D–G when the runtime is available. Run focused tests and `python -m pytest tests/contract`.

## Rules

Keep the planner simple and deterministic. Do not use hidden shared state. Make graceful recovery visible through events and state. Do not implement negotiation, UI, deployment, or unrelated refactors. Do not disable tests.

## PR

Use branch `feat/agent-2-safety`, make small commits, and open a PR. Final report must list changes, tests, contracts/events affected, integration dependencies, and limitations. Ask for human input only for genuine contract ambiguity, ownership conflict, missing requirements, or unavoidable architecture change.
