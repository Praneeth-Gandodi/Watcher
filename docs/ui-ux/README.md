# Project UI/UX Skill

The project includes the Hallmark design skill at:

```text
.agents/skills/hallmark/
```

Hallmark is a documentation-only design and audit guide. It was reviewed for project use and contains rules for:

- Avoiding generic AI-generated layouts
- Professional visual hierarchy
- Responsive behavior
- Accessible interaction states
- Honest copy and metrics
- Motion restraint
- Token-based styling

## Project boundaries

Hallmark does not override:

- `AGENTS.md`
- `CONTRACTS.md`
- `ARCHITECTURE.md`
- Agent ownership boundaries
- Backend event/API contracts

Agent 3 may use Hallmark for dashboard audits and visual refactors, but the dashboard must remain an operations console connected to canonical backend state. Do not use it to introduce fabricated telemetry, fake metrics, or business logic.

## Recommended use

```text
hallmark audit dashboard
```

For a redesign, preserve the real data flow and replace only the visual/interaction layer. Run the relevant frontend tests and build after any UI change.
