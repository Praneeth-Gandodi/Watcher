# Contributing

## Branches

- `main` is protected; no direct pushes.
- Use `feat/agent-1-negotiation`, `feat/agent-2-safety`, `feat/agent-3-dashboard`, or `feat/agent-4-demo`.
- Keep commits small, focused, and conventionally named, for example `feat(agent-1): add bid validation`.

## Commit workflow

- Do not make one large commit after completing an entire agent task.
- Commit coherent feature slices, fixes, tests, and documentation as they are completed.
- Each commit should have one purpose, a conventional message, and relevant tests passing.
- Recommended sequence: contract/interface if needed → feature implementation → tests → integration/documentation.
- Use `fix(agent-N): ...` for independently understandable bug fixes.
- Before opening a PR, run `git log --oneline main..HEAD` and verify the branch has a useful commit history.
- Preserve the commit sequence when merging; do not squash by default unless the maintainer explicitly requests it.

## Pull requests

Every PR must state:

- what changed and why
- tests run and results
- contracts/models/events/API affected, or `none`
- events consumed and produced
- integration impact and required follow-up
- known limitations

## Reviewer merge gate

A reviewer must inspect the complete diff and all changed files before merge. Automated checks are necessary but never sufficient. Reject or leave unmerged when the change is incorrect, unrelated, duplicated, over-scoped, contract-breaking, insecure, inaccessible, performance-regressive, missing required tests, or likely to break integration. Reviewers must provide concrete findings and must not approve a PR only because CI is green or the author reports completion.

## Ownership and contracts

Read `ARCHITECTURE.md` and `CONTRACTS.md` before changing anything. Any change to `backend/contracts/**` or a documented public API requires an integration PR with contract tests and documentation updates.

## Required checks

```powershell
python -m pytest tests/contract
python -m pytest
cd dashboard
npm run typecheck
npm run build
```

No secrets, local environment files, generated `node_modules`, or fabricated results belong in Git.
