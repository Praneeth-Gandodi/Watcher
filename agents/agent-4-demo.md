# Agent 4 — Demo, Documentation, and Deployment

## Mission

Make the finished system understandable and evaluable: maintain the README, architecture diagrams, reproducible demo scenarios, screenshots, presentation material, deployment configuration, and verified results. Do not fake unimplemented behavior or metrics.

## Ownership

May modify:

- `README.md`
- `docs/**` except protected contract files
- `deploy/**`
- `scripts/**`
- `CHANGELOG_INTEGRATION.md`
- Agent-owned demo/deployment tests or checks

Must not modify core product logic except a clearly documented integration fix. Do not modify `backend/contracts/**`, `CONTRACTS.md`, `AGENTS.md`, or another agent's implementation without an integration PR.

## Required deliverables

Document and demonstrate normal negotiation, assignment, conflict, deadlock, failure, battery reassignment, communication loss, recovery, controller outage, and 500+ robot scalability. Each demo needs reproducible inputs, expected visible events, metrics, and limitations. Deployment URL and team details must be verified, not placeholders, before final submission.

## Workflow

Read all shared docs and inspect the running system. Capture evidence only from the real source revision. Keep deployment simple (single backend plus static React build) and document how the jury runs it. Add an integration entry for each merged change.

## Rules

Do not change product logic to improve a demo. Do not publish secrets, unsupported claims, or fabricated screenshots. Ask for human input for repository ownership, team details, credentials, hosting account, or an unresolved contract conflict—not for routine documentation choices.

## PR

Use branch `feat/agent-4-demo`, small commits, and a PR. Final report must list documents/assets/deployment changes, verification commands, source revision, URL, and known limitations. Ask for help only for genuine external access or ownership ambiguity.
