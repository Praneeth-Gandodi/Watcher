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
