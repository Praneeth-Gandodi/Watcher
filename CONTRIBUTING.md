# Contributing

## Branches

- `main` is protected; no direct pushes.
- Use `feat/agent-1-negotiation`, `feat/agent-2-safety`, `feat/agent-3-dashboard`, or `feat/agent-4-demo`.
- Keep commits small, focused, and conventionally named, for example `feat(agent-1): add bid validation`.

## Pull requests

Every PR must state:

- what changed and why
- tests run and results
- contracts/models/events/API affected, or `none`
- events consumed and produced
- integration impact and required follow-up
- known limitations

## Ownership and contracts

Read `AGENTS.md` and the assigned agent prompt. Do not edit another agent's implementation. Any change to `backend/contracts/**` or documented public API requires an integration PR with contract tests and documentation updates.

## Required checks

```powershell
python -m pytest tests/contract
python -m pytest
cd dashboard
npm run typecheck
npm run build
```

No secrets, local environment files, generated `node_modules`, or fabricated results belong in Git.
