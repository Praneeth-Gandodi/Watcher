# Global Agent Rulebook

Read this file, `ARCHITECTURE.md`, `CONTRACTS.md`, `TESTING.md`, `INTEGRATION.md`, and your assigned `agents/agent-*.md` before coding.

## Non-negotiable rules

- Stay inside your assigned ownership paths. Do not edit another agent's implementation.
- Treat `CONTRACTS.md` and `backend/contracts/**` as authoritative.
- Treat `ARCHITECTURE.md` as authoritative unless a formal architecture change is merged.
- Inspect existing files before editing; preserve useful work and avoid unrelated refactors.
- Reuse canonical models, events, commands, and interfaces. Do not create duplicate representations.
- Never silently rename/change a public schema, event, API, serialization format, or interface.
- Shared-contract changes require documentation, contract-test updates, and an integration note.
- Write tests beside implementation changes. Run the focused tests, all contract tests, and any affected integration tests.
- Do not delete, skip, or weaken tests to make CI pass. Do not hide failures.
- Keep changes local, focused, and reviewable. Use small conventional commits; never rewrite unrelated history.
- Do not commit secrets, generated build output, local environment files, or fabricated screenshots/metrics.
- Do not implement another subsystem, fake backend behavior in the UI, or create hidden coupling.
- Use separate branches and separate clones/worktrees. Never push directly to `main`.

## Commit policy

- Do not wait until the entire feature is finished to create one giant commit.
- Commit each coherent vertical slice as it becomes reviewable: feature, fix, test, documentation, or integration change.
- Use conventional focused messages such as `feat(agent-1): add bid eligibility`, `fix(agent-2): handle expired routes`, `test(agent-3): cover snapshot loading`, and `docs(agent-4): document recovery demo`.
- Keep each commit limited to one purpose and passing relevant tests. Do not mix an unrelated feature, refactor, generated output, or contract change into a feature commit.
- Before opening a PR, inspect `git log --oneline main..HEAD` and ensure the branch contains a readable sequence of meaningful commits, not one commit containing the whole task.
- Do not rewrite unrelated history, force-push shared branches, or squash the feature branch by default. Preserve the focused commit sequence unless the maintainer explicitly requests a squash.
- A fix discovered during development should be a separate `fix(...)` commit when it is independently understandable.

## Scope and blockers

If the contract answers the question, implement it. Choose the simplest reasonable internal design and document the assumption. Ask for human input only for a genuine contract ambiguity, ownership conflict, missing required information, or unavoidable architecture change.

## Definition of done

- Contract compliance is demonstrated by tests.
- New behavior has unit/integration coverage appropriate to the subsystem.
- Interfaces and integration impact are documented.
- No unrelated files changed.
- A PR is opened with summary, tests, contracts affected, integration notes, and limitations.
