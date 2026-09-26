# Deploying Watcher

Watcher is one process and one URL. The FastAPI service runs the simulation and
serves the built console from the same origin, so there is no separate frontend
to host, no CORS setup, and no reverse proxy to explain while someone is
watching.

There is nothing host-specific in this project. The `Dockerfile` is the whole
contract: any host that can build an image and forward one port can run it. The
steps below are the same everywhere, and the per-host sections are just the
click-path to get there.

## 1. Locally, to prove the image works

Do this before pushing anywhere. If it does not run here, it will not run there.

```powershell
docker build -t watcher .
docker run --rm -p 8000:8000 watcher
```

Open <http://localhost:8000>. To run a larger fleet:

```powershell
docker run --rm -p 8000:8000 -e WATCHER_FLEET_SIZE=500 watcher
```

## 2. Anywhere with a Dockerfile

Every host below needs the same three things:

| Setting | Value |
|---|---|
| Build command | `docker build -t watcher .` (most hosts do this automatically) |
| Start command | nothing, the image `CMD` is correct |
| Health check path | `/api/v1/health` |

That is the entire configuration. Nothing else is required.

### Render

1. New → **Web Service** → connect the repository.
2. Render detects the `Dockerfile`. Leave the build command empty.
3. Environment: `WATCHER_FLEET_SIZE=200`.
4. Health check path: `/api/v1/health`.

### Railway

1. **New Project** → **Deploy from GitHub repo**.
2. Railway detects the `Dockerfile`. No start-command override.
3. Add `WATCHER_FLEET_SIZE=200` under Variables.
4. Railway injects `PORT`; the image reads it.

### Fly.io

```bash
fly launch --no-deploy      # reads the Dockerfile, generates fly.toml
fly deploy
fly scale count 1 --vm-cpu 2 # one VM; see "Sizing" below
```

Set the fleet size with `fly secrets set WATCHER_FLEET_SIZE=200`.

### Google Cloud Run

```bash
gcloud run deploy watcher \
  --source . \
  --region us-central1 \
  --cpu 2 \
  --set-env-vars WATCHER_FLEET_SIZE=200 \
  --allow-unauthenticated
```

Cloud Run injects `PORT`; the image reads it. `--cpu 2` matters at large fleet
sizes, for the reason in the next section.

### Any VPS with Docker

```bash
git clone https://github.com/Praneeth-Gandodi/Watcher.git
cd Watcher
docker build -t watcher .
docker run -d --restart unless-stopped -p 80:8000 \
  -e WATCHER_FLEET_SIZE=200 watcher
```

Put a TLS terminator in front of it if the audience needs `https`.

## 3. Sizing: the one thing that is not free

The simulation is a single authoritative world in one process. It is CPU-bound,
and it is sized so that one tick finishes inside its deadline. A shared or
free-tier CPU is slower than the machine the numbers below were measured on.

Measured on one developer machine, steady state:

| Fleet | Floor | Tick rate | Tick, mean | p95 | Deadlines missed |
|---|---|---|---|---|---|
| 50 | 200 x 120 m | 10 Hz | ~5 ms | ~13 ms | 0% |
| 200 | 340 x 200 m | 5 Hz | ~25 ms | ~38 ms | 0% |
| 500 | 540 x 320 m | 5 Hz | ~70-95 ms | ~115-275 ms | 2-8% |

The tick rate drops from 10 Hz to 5 Hz above 200 robots. That is a deliberate
trade-off, not an accident: five coordination decisions per second per robot is
ample for right of way at walking pace, and it is what keeps the tick inside its
budget at fleet scale.

**Check what your host can actually sustain.** Deploy, wait a minute, then:

```bash
curl -s https://YOUR-URL/api/v1/metrics
```

Read `extra_metrics.average_tick_ms`. If it is at or above `1000 / tick_rate`
(100 ms at 10 Hz, 200 ms at 5 Hz), the host cannot keep up at that fleet size:
lower `WATCHER_FLEET_SIZE` or give it more CPU. `active_robots`,
`completed_tasks`, and `open_conflicts` should all be moving, not frozen.

Reproduce the table yourself on any machine:

```powershell
python -m pytest tests/integration/agent2/test_scenario_h_scalability.py -q -s
```

The test prints its own measurements and asserts its budgets, so it doubles as
the benchmark.

## 4. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WATCHER_FLEET_SIZE` | `50` | Robots in the fleet, 1-2000. Above 200 the floor grows with the fleet, so a bigger number means a bigger warehouse, not a denser one. |
| `WATCHER_SEED` | `2026` | World and fleet seed. The same seed reproduces the same run. |
| `WATCHER_TICK_RATE_HZ` | per fleet size | Overrides the tick rate. Leave unset unless you are deliberately slowing down. |
| `WATCHER_CORS_ORIGINS` | `*` | Comma-separated allowed origins. Only needed if you split the console onto a different origin. Do not split it. |
| `PORT` | `8000` | Injected by the host. The image reads it. Do not set it by hand. |

Non-fatal defaults live in [`config/default.json`](config/default.json).

## 5. Verify before you present

Run these against the deployed URL, not against localhost. A URL that answers
`/health` but serves a blank page is the one failure that will not fix itself.

```bash
curl -s https://YOUR-URL/api/v1/health          # must answer 200
curl -s https://YOUR-URL/api/v1/metrics         # average_tick_ms, active_robots
curl -s https://YOUR-URL/api/v1/snapshot        # robots, world, controller_available
```

Then open the URL in a browser. The console should render the floor, the roster,
and a live event stream. Set the speed to `2x` or `4x` in the console so travel
is visible in seconds rather than minutes.

### If something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| Blank page, `/api/v1/health` is fine | `dashboard/dist` did not make it into the image | The dashboard build stage failed. Check the host's build log for the `npm run build` step. |
| `502` / connection refused | Not listening on the port the host routed to | Should not happen; the image reads `$PORT`. Check the host assigned a port and that it is not overridden. |
| Renders, but robots never move | Tick budget blown on a small CPU | Lower `WATCHER_FLEET_SIZE`. Compare `average_tick_ms` against `1000 / tick_rate`. |
| Everything frozen, `paused: 1` | Simulation paused | The runtime does not pause itself. Check the console controls and the seed. |
| Robots visible but idle, `pending_tasks` climbing | Allocation not draining | Check `average_allocation_latency_ms` and the event stream for allocation events. |

## 6. After you deploy

The deployment URL is a required part of the submission. Add it to the
`Live demo` line at the top of [`README.md`](README.md) and fill in the Team
table at the bottom, then push. The README is a build input for both
`pyproject.toml` and the `Dockerfile`, so editing it triggers a fresh deploy on
hosts that rebuild on push.

Deeper detail, per-host caveats, and a longer troubleshooting list are in
[`docs/deployment.md`](docs/deployment.md).
