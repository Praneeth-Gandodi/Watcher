# Dashboard

This is the React + TypeScript + Vite renderer owned by Agent 3.

## Boundary

The dashboard consumes the backend `SimulationSnapshot` and canonical WebSocket events. Agent 3 implements the 2D grid renderer, robot markers, route lines, conflict/deadlock overlays, metrics, and controls.

Agent 3 does **not** implement:

- World generation
- Robot movement
- Route planning
- Collision or deadlock detection
- Battery scheduling
- Negotiation

Those belong to Agent 2 and Agent 1 respectively.

## Rendering model

Use `WorldState` dimensions and `GridCell` entries from the shared API contract. Render the world in 2D canvas space. Use React for controls, panels, and state; use Canvas for the high-volume robot/grid layer.

Read `../docs/architecture/visualization.md` before implementing the renderer.

## Integration notes

- The dashboard reads `controller_available` from `SimulationSnapshot`. The canonical `ControlCommand` contract has no controller-outage command, so the UI does not invent one. A controller-outage control requires a backend contract and API addition before it can be exposed here.
- `SimulationSnapshot` currently has no deadlock collection or resolution cursor. Deadlock overlays are therefore derived conservatively from canonical `DEADLOCK_DETECTED` events and cleared when a related `RECOVERY_STARTED`, `TASK_REASSIGNED`, or `TASK_COMPLETED` event arrives. The backend should add authoritative active/resolved deadlock state to a future snapshot contract to remove this event-derived limitation.
- The optional `SystemMetrics.extra_metrics.simulation_speed_multiplier` key is used only to initialize the speed selector when the runtime publishes it. The selector remains locally controlled while a command is pending and falls back to `1.0×` when no runtime value is available.
- Live events are validated before entering the event log, batched into an animation-frame update, and followed by a debounced snapshot refresh. The snapshot remains the source of truth for robot, task, route, conflict, and metric state.

