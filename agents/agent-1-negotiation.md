# Agent 1 — Negotiation and Allocation

## Mission

Implement the decision layer: task eligibility, candidate discovery, robot bidding, peer negotiation, assignment, and dynamic task reassignment. Use the contracts in `CONTRACTS.md`; do not build a second simulator.

## Ownership

May modify:

- `backend/negotiation/**`
- `backend/allocation/**`
- `tests/unit/negotiation/**`
- `tests/unit/allocation/**`
- Agent-owned integration test subdirectories
- `docs/decisions/*negotiation*.md` only when necessary

Must not modify:

- `backend/contracts/**`
- `safety/**`
- `dashboard/**`
- deployment, presentation, or another agent's tests
- protected root documents without an integration PR

## Inputs and outputs

Consume `Robot`, `Task`, observed simulation time, battery/failure/communication events, and existing assignments. Produce `NegotiationOutcome`, `TaskAssignment`, and canonical `TASK_*`/`NEGOTIATION_*`/`BID_SUBMITTED` events. Do not publish ad-hoc event names.

## Required workflow

Read all shared docs first. Inspect code. Implement only this boundary. Write unit tests for scoring, eligibility, invalid/expired bids, winner selection, and reassignment. Add contract/integration coverage where applicable. Run focused tests plus `python -m pytest tests/contract`.

## Rules

Reuse contracts; preserve backward compatibility; keep changes local; document assumptions and blocked dependencies. Do not implement A*, movement, collision, deadlock, UI, deployment, or presentation. Do not fake candidate data in another subsystem. Do not disable tests.

## Commit policy

Do not wait until the entire negotiation feature is complete to make one commit. Commit coherent slices such as eligibility, bid scoring, negotiation, assignment, reassignment, and their tests as they become reviewable. Use focused messages such as `feat(agent-1): add candidate eligibility`, `fix(agent-1): reject expired bids`, and `test(agent-1): cover failure reassignment`. Keep each commit single-purpose and passing its relevant tests. Before opening the PR, inspect `git log --oneline main..HEAD`; preserve the meaningful commit sequence and do not squash by default.

## PR

Use branch `feat/agent-1-negotiation`, make small reviewable commits, and open a PR. Final report must list changes, tests, contracts/events affected, consumers, integration notes, and known limitations. Ask for human input only for genuine contract ambiguity, ownership conflict, missing requirements, or unavoidable architecture change.
