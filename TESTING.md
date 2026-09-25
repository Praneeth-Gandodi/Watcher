# Testing Strategy

## Layers

1. **Unit tests** (`tests/unit`): bid scoring, eligibility, allocation, path calculation, collision/deadlock detection, battery thresholds, React rendering, and command validation.
2. **Contract tests** (`tests/contract`): Robot, Task, Bid, route, snapshot, commands, event names, event payload fields, strict serialization, and health API.
3. **Integration tests** (`tests/integration`): complete flows A–H documented in `tests/integration/README.md`.

## Commands

```powershell
python -m pytest tests/contract
python -m pytest tests/unit
python -m pytest tests/integration
python -m pytest
cd dashboard
npm run typecheck
npm run build
```

Tests must be deterministic: fixed seed, fixed simulation time, no wall-clock assumptions, and explicit fault schedules. Every failure/recovery scenario must assert both emitted events and resulting state. Do not skip or delete tests to make CI pass. A not-yet-implemented integration scenario is a specification until the owning agent adds a real test.
