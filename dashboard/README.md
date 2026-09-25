# Dashboard

The React + TypeScript + Vite operations console owned by Agent 3. It renders
the canonical projection the runtime publishes and sends canonical commands
back. It is a view: it never computes positions, routes, collisions, deadlocks,
or allocations.

Read `../docs/architecture/visualization.md` and `../CONTRACTS.md` first.

## Boundary

The console consumes:

- `GET /api/v1/snapshot` — the full `SimulationSnapshot`
- `GET /api/v1/events?after_sequence=` — canonical event history
- `WS /api/v1/stream?after_sequence=` — canonical events after a cursor

and produces only the eight canonical `ControlCommand` values. It does not
implement world generation, movement, route planning, collision or deadlock
detection, battery scheduling, or negotiation.

Derived figures in `selectors.ts` — fleet counts, filtered rosters, bid and
recovery records read out of the event log, performance facts — are views over
state the runtime already decided. They are pure functions, which is why they
can be tested without a running backend.

## Structure

The map is the page; every panel is arranged around it.

| File | Responsibility |
|---|---|
| `App.tsx` | Composition, keyboard shortcuts, command wiring |
| `useFleetConnection.ts` | The single connection: snapshot, stream, commands, notices |
| `components/FleetMap.tsx` | Canvas renderer, layers, camera, hit testing |
| `components/CommandBar.tsx` | Identity, readouts, connection state, theme, palette |
| `components/Inspector.tsx` | Selected robot telemetry and targeted fault injection |
| `components/OperatorControls.tsx` | Pause, resume, reset, speed, task creation |
| `components/Dock.tsx` | Tabbed detail: roster, tasks, events, bids, safety, performance |
| `components/Roster.tsx` | Windowed, searchable, filterable fleet list |
| `components/CommandPalette.tsx` | Ctrl+K finder for any robot or task |
| `selectors.ts` | Pure derived views |
| `virtual.ts` | Row windowing maths |
| `frames.ts` | WebSocket frame validation |
| `validation.ts` | Runtime parsing of every payload the API returns |
| `tokens.css` | The locked token system, dark and light |
| `styles.css` | Layout and components, tokens only |

## The stream protocol

The WebSocket is a transport for the canonical event vocabulary, not a second
contract. It sends three tagged frames:

- `{"kind": "snapshot", "data": SimulationSnapshot}` on connect, and whenever
  a client needs to resynchronise
- `{"kind": "event", "data": EventEnvelope}` for each canonical event
- `{"kind": "cursor", "data": {"last_event_sequence": n}}` as a heartbeat

Every frame is validated with the same parsers the REST path uses. A frame that
fails validation is reported and discarded; the socket stays open, because one
bad payload says nothing about the next one.

## Rendering at 500 robots

The world and its static features are drawn once into an offscreen canvas, the
routes into a second, and only robots are redrawn per frame. Events are batched
onto an animation frame and the snapshot is refetched on a debounce, so a burst
of events cannot turn into a burst of React renders. The roster renders only the
rows that can be on screen. The performance panel reports the measured cost of
each simulation stage, read from the runtime's own telemetry.

## Visual system

One token block in `tokens.css` with a dark and a light theme of equal
standing. The stored choice wins; otherwise the OS preference is followed.
`Space Grotesk` for display, `Inter` for body, `JetBrains Mono` for identifiers
and telemetry. No purple, no gradients, no glow.

Status is carried by shape and label as well as colour, so the console stays
readable in greyscale and for a viewer who cannot separate the status hues.
`prefers-reduced-motion` stops the canvas pulses and every CSS transition.

## Known limitations

- **Deadlock overlays are event-derived.** `SimulationSnapshot` has no deadlock
  collection, so a cycle is shown until a recovery, reassignment, or completion
  touches one of its robots or tasks. The backend should publish authoritative
  active deadlock state to remove this.
- **Controller availability is read-only.** The canonical command set has no
  coordinator-outage command, so the console reports the flag and the outage
  counters but cannot trigger an outage itself. Adding
  `INJECT_COORDINATOR_OUTAGE` to `CONTRACTS.md` is the recommended change.
- **Event log is bounded.** The console keeps the most recent 400 events. A
  client that falls behind recovers through a snapshot, not by replay.
- **Task table is capped at 300 rows** for rendering cost; the filter narrows
  the rest.

## Commands

```powershell
npm install
npm run dev        # proxies /api to VITE_BACKEND_URL, default 127.0.0.1:8000
npm run typecheck
npm test
npm run build
npm run preview
```

Set `VITE_API_BASE_URL` to point at a different API origin; the stream URL is
derived from it and upgraded to `wss://` on an https origin.
