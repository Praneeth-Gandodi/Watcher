# Deployment

Watcher is a single service. The FastAPI process runs the simulation and serves
the built dashboard from the same origin, so there is one URL, no CORS
configuration, and no reverse proxy to reason about during a live demo.

## The image

```powershell
docker build -t watcher .
docker run --rm -p 8000:8000 -e WATCHER_FLEET_SIZE=500 watcher
```

Then open <http://localhost:8000>.

The build has two stages. The dashboard is compiled with Node and only the
resulting `dist/` is copied forward, so the runtime image carries no Node
toolchain and no `node_modules`.

The container runs as an unprivileged user and needs no writable volume: the
simulation keeps its world, fleet, and event log in memory and rebuilds from its
seed on restart.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WATCHER_FLEET_SIZE` | `50` | Robots in the fleet, 1–2000. Above 200 the floor and tick rate are sized to match, so a larger number means a larger warehouse rather than a denser one. |
| `WATCHER_SEED` | `2026` | World and fleet seed. The same seed reproduces the same run. |
| `WATCHER_TICK_RATE_HZ` | derived | Overrides the tick rate. Left unset, the rate is chosen per fleet size. |
| `WATCHER_CORS_ORIGINS` | `*` | Comma-separated allowed origins. Only needed if the dashboard is served from a different origin than the API. |
| `PORT` | `8000` | Set by most hosts. The container command reads it via the platform. |

## Sizing a deployment

The simulation is sized to hold real time, and the numbers below are measured on
one developer machine rather than estimated. Re-run them on your own hardware
with:

```powershell
python -m pytest tests/integration/agent2/test_scenario_h_scalability.py -q -s
```

The test prints its own measurements and asserts the budgets, so it doubles as
the benchmark.

| Fleet | Floor | Rate | Tick, mean | p95 | Deadlines missed | Snapshot | Refresh |
|---|---|---|---|---|---|---|---|
| 50 | 200 × 120 m | 10 Hz | ~5 ms | ~13 ms | 0% | 220 KiB | 43 KiB |
| 200 | 340 × 200 m | 5 Hz | ~25 ms | ~38 ms | 0% | — | — |
| 500 | 540 × 320 m | 5 Hz | ~70–95 ms | ~115–275 ms | 2–8% | 1.6 MiB | 439 KiB |

Notes on reading that table:

- **One worker, always.** The runtime is a single authoritative world behind a
  lock. Several workers would each get their own fleet. Scale with
  `WATCHER_FLEET_SIZE`, not with replicas.
- **A full snapshot is 1.6 MiB at 500 robots** because it carries the world. A
  client re-pulls `/robots`, `/tasks`, `/routes`, `/conflicts`, and `/metrics`
  between snapshots, which is 439 KiB, and takes the full snapshot only on
  connect. The dashboard does this already.
- **The tick rate drops to 5 Hz above 200 robots.** That is five coordination
  decisions a second per robot, which is ample for right of way at walking pace,
  and it is what keeps the tick inside its deadline. It is a deliberate,
  documented trade-off, not a discovered overage.
- **A free-tier host may not sustain 500 robots.** One shared vCPU will be
  slower than the machine above. Start at `WATCHER_FLEET_SIZE=200` and raise it
  while `GET /api/v1/metrics` shows `average_tick_ms` comfortably below
  `1000 / tick_rate`.

## Host-specific notes

No host configuration is committed, so the repository does not presume where this
runs. The container is the contract; the host only has to build it and forward
one port.

**Render** — create a Web Service, point it at the repo, and it detects the
Dockerfile. Set `WATCHER_FLEET_SIZE` in the environment. Health check path:
`/api/v1/health`.

**Railway** — same Dockerfile detection. No start command override is needed;
the image's `CMD` is correct.

**Fly.io** — `fly launch` will read the Dockerfile. Allocate at least one
dedicated core for 500 robots and confirm the region is close to the audience.

Whichever host you pick, verify after deploying:

```powershell
curl https://<your-host>/api/v1/health
curl https://<your-host>/api/v1/snapshot | Select-String robots
```

If `/api/v1/health` answers but the page is blank, the dashboard build did not
make it into the image. Check that `dashboard/dist` exists in the build stage.

## Before a live demo

- Set the speed to `2×` or `4×` in the console so travel is visible in seconds
  rather than minutes. The runtime applies the multiplier to the command
  channel, and the sim clock keeps advancing in real time.
- Open the Safety tab first: the coordinator outage is on a seeded schedule, so
  it will fire on its own and the fleet will keep assigning work through local
  claiming. That is the single most convincing thing in the demo.
- Use `Ctrl+K` to pull up a specific robot by identifier. It is the fastest way
  to answer "show me that one" in front of an audience.
