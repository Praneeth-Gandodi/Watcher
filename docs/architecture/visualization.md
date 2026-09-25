# Visualization Contract

## Ownership

- **Agent 2 owns the backend world and grid.** It creates the canonical `WorldState`, maintains robot movement state, updates route/conflict state, and publishes simulation events.
- **Agent 3 owns the visual renderer.** It consumes `WorldState`, `Robot`, `RoutePlan`, `Conflict`, `DeadlockReport`, `SimulationSnapshot`, and WebSocket events. It does not calculate positions, paths, collisions, or robot behavior.

## Backend-to-dashboard flow

```text
WorldState + Robot state + RoutePlan + Conflict
                    |
           SimulationSnapshot
              /             \
       REST snapshot       WebSocket events
              \             /
                 React renderer
```

The snapshot includes `world`, `robots`, `routes`, `conflicts`, `tasks`, metrics, `revision`, and `last_event_sequence`. The dashboard first receives a snapshot, then requests events after the snapshot cursor. Events update the renderer without the UI inventing domain state.

## Grid rules

- MVP is 2D world space in meters, not screen pixels.
- `WorldState.width_m`, `height_m`, `cell_size_m`, `columns`, and `rows` define the grid.
- `cell_x` increases from left to right; `cell_y` increases from bottom to top.
- Sparse `GridCell` entries describe obstacles, resources, charging points, workstations, and dead zones. Unlisted cells are free.
- Robot positions use `Position2D` in world meters.
- Routes use `RoutePlan.waypoints` in world meters.
- Agent 3 converts world coordinates to screen coordinates using the current camera and zoom.

## Rendering requirements

Agent 3 should implement:

1. Grid lines and world boundary
2. Obstacles, resource points, workstations, and chargers
3. Robot markers with status text/shape, not color alone
4. Route polylines and waypoints
5. Conflict markers and right-of-way states
6. Deadlock cycle markers
7. Robot selection and detail panel
8. Pan, zoom, reset camera, pause, resume, reset, and fault-injection controls
9. Loading, disconnected, stale, empty, and error states

For 500+ robots, render the world with Canvas rather than one React DOM element per robot. Batch WebSocket updates, draw on animation frames, and avoid re-rendering the entire React tree for every event.

## 2D versus 3D

Use 2D for the hackathon MVP because it is deterministic, testable, and fast to deploy. A later 3D mode may consume the same contracts but is not required for the initial implementation. If 3D is introduced, it must be a renderer-only change and must not alter backend world or safety contracts.

## Tests

Agent 3 tests coordinate conversion, camera controls, marker selection, loading/error states, and rendering with 500+ fixture robots. Agent 2 tests world dimensions, cell indexing, robot-to-cell mapping, movement boundaries, and event emission.
