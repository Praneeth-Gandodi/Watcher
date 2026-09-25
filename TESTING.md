# Testing Strategy

## Layers

1. **Unit tests** (`tests/unit`): bid scoring, eligibility, allocation, path calculation, collision/deadlock detection, battery thresholds, React rendering, and command validation. `tests/unit/safety` covers the Agent 2 algorithms and `tests/unit/simulation` the grid, world, and runtime.
2. **Contract tests** (`tests/contract`): Robot, Task, Bid, route, snapshot, commands, event names, event payload fields, strict serialization, health API, and the simulation HTTP surface.
3. **Integration tests** (`tests/integration`): complete flows A–H documented in `tests/integration/README.md`.
4. **Scalability tests** (`tests/scalability`): a deterministic 500-robot fixture and measured costs. Marked `scalability` and printable with `-s`.

## Commands

```powershell
python -m pytest                       # everything
python -m pytest tests/contract
python -m pytest tests/unit
python -m pytest tests/integration
python -m pytest tests/scalability -s  # prints the measured costs
python -m pytest -m "not scalability"  # skip the large-fleet measurements
cd dashboard
npm run typecheck
npm run build
```

Tests must be deterministic: fixed seed, fixed simulation time, no wall-clock assumptions, and explicit fault schedules. The simulation clock is advanced explicitly, never derived from wall-clock time, so a recorded command sequence reproduces a run exactly. Every failure/recovery scenario must assert both emitted events and resulting state. Do not skip or delete tests to make CI pass. A not-yet-implemented integration scenario is a specification until the owning agent adds a real test.

## Assertions every simulation scenario must make

- no Python exception
- no negative or out-of-grid robot position
- no robot occupies an obstacle cell, and every robot's reported position maps
  back to the cell the runtime tracks it in
- no unresolved space-time conflict after safety resolution, or an explicit
  report that the conflict is unresolvable
- `SimulationSnapshot` and `SystemMetrics` are valid, with
  `last_event_sequence >= revision`
- robots never hold two tasks at once
- the fleet ends in a consistent state (no lost work, no phantom conflicts)
