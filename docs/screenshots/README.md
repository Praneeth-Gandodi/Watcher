# Screenshots

Captured from the running system: `python -m uvicorn backend.app.main:app` with
`WATCHER_FLEET_SIZE` set to the fleet shown, and the dashboard built with
`npm run build` in `dashboard/`. Every figure visible in them comes from the
canonical projection the runtime published at the moment of capture.

| File | What it shows |
|---|---|
| `console-dark.png` | 500 robots, dark theme, a robot selected in the inspector |
| `console-light.png` | 200 robots, light theme |
| `console-mobile.png` | 375 px viewport |

These are records of a run, not fixtures. Nothing here is hand-drawn, retouched,
or composed to flatter the numbers, and the scalability figures quoted in the
README come from `pytest tests/integration/agent2/test_scenario_h_scalability.py`,
which prints its own measurements.
