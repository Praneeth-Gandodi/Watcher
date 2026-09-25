# Safety Subsystem — Agent 2

This package owns movement, route planning, collision/right-of-way handling,
deadlock detection/recovery, battery constraints, failure recovery, and
communication-loss recovery.

## Boundaries

- Consume canonical contracts from `backend/contracts/`
- Consume Agent 1 assignment events through the documented event boundary
- Do not import or rewrite Agent 1 negotiation internals
- Do not implement dashboard behavior
- Add unit/integration tests under `tests/unit/safety/` and the agent-owned
  integration test area

The subpackages are intentionally empty implementation boundaries at bootstrap.
