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
- Write tests beside implementation changes, but **do not run them** unless the human explicitly asks. Test execution is opt-in per session; do not run `pytest`, `vitest`, `npm test`, or a build to "verify" work on your own initiative. Typecheck and lint are also opt-in.
- Do not delete, skip, or weaken tests to make CI pass. Do not hide failures. When a test run is requested, report the real result, including failures.
- Keep changes local, focused, and reviewable. Use small conventional commits; never rewrite unrelated history.
- Do not commit secrets, generated build output, local environment files, or fabricated screenshots/metrics.
- Do not implement another subsystem, fake backend behavior in the UI, or create hidden coupling.
- Use separate branches and separate clones/worktrees. Never push directly to `main`.

## Scope and blockers

If the contract answers the question, implement it. Choose the simplest reasonable internal design and document the assumption. Ask for human input only for a genuine contract ambiguity, ownership conflict, missing required information, or unavoidable architecture change.

## Definition of done

- Contract compliance is demonstrated by tests.
- New behavior has unit/integration coverage appropriate to the subsystem.
- Interfaces and integration impact are documented.
- No unrelated files changed.
- A PR is opened with summary, tests, contracts affected, integration notes, and limitations.
