/**
 * The single source of live state for the console.
 *
 * The backend owns the simulation, so this hook does three things and nothing
 * else: poll the authoritative read models, drive the deterministic clock with
 * `/simulation/advance`, and forward user commands. All coordination logic --
 * routing, collision, deadlock, battery, allocation, negotiation -- stays in
 * the backend.
 *
 * The backend has no wall clock, so "running" here means "keep advancing
 * ticks". A single interval owns the ticks and the polls, which keeps the
 * request rate predictable: one advance plus one read pass per tick, not a
 * request storm.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api/backend";
import type {
  HealthResponse,
  ScenarioListResponse,
  SnapshotResponse,
  SystemMetrics,
  TelemetryResponse,
} from "../api/types";
import { useEventLog } from "./useEventLog";

/**
 * One tick of the backend clock per interval while running.
 *
 * The backend ticks at 10 Hz, so the console advances enough ticks to cover the
 * wall time that actually elapsed. That keeps simulated time tracking real time
 * at 1x, which is what makes the client's motion smoothing stable: the next
 * sample always lands where the last one was projected to go.
 */
export const TICK_INTERVAL_MS = 120;
export const BACKEND_TICK_S = 0.1;
export const MAX_TICKS_PER_CALL = 50;
export const SPEED_PRESETS = [0.5, 1, 2, 4] as const;

export type ConnectionState = "connecting" | "online" | "offline";

export interface NewTaskInput {
  taskId: string;
  cellX: number;
  cellY: number;
  priority: number;
  requiredCapability: string;
  estimatedDurationS: number;
}

export interface SimulationState {
  connection: ConnectionState;
  health: HealthResponse | null;
  snapshot: SnapshotResponse | null;
  metrics: SystemMetrics | null;
  telemetry: TelemetryResponse | null;
  scenarios: ScenarioListResponse | null;
  running: boolean;
  speed: number;
  busy: string | null;
  error: string | null;
  notice: string | null;
  /** Wall-clock milliseconds, used only for animation phase. */
  now: number;
  start: () => Promise<void>;
  /** Resume, or reopen the scenario when there is no work left. */
  startOrReopen: () => Promise<void>;
  /**
   * Pull the read models now, without touching the clock. The demo runner uses
   * it so a held stage shows the state the backend just reported instead of
   * whatever the last poll happened to catch.
   */
  refreshNow: () => Promise<void>;
  pause: () => Promise<void>;
  step: () => Promise<void>;
  setSpeed: (multiplier: number) => Promise<void>;
  reset: (seed?: number) => Promise<void>;
  dispatch: () => Promise<void>;
  randomizeAssignment: (jitter: number, seed?: number) => Promise<void>;
  loadScenario: (input: {
    name: string;
    robotCount?: number;
    columns?: number;
    rows?: number;
    seed?: number;
  }) => Promise<void>;
  createTask: (input: NewTaskInput) => Promise<void>;
  injectFailure: (robotId: string) => Promise<void>;
  injectCommunicationLoss: (robotId: string) => Promise<void>;
  restoreRobot: (robotId: string) => Promise<void>;
  clearError: () => void;
  clearNotice: () => void;
  eventLog: ReturnType<typeof useEventLog>;
}

export function useSimulation(): SimulationState {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryResponse | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioListResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [speed, setSpeedState] = useState(1);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const eventLog = useEventLog();
  /**
   * The latest snapshot, readable from callbacks that must not re-create
   * themselves when a poll lands. `startOrReopen` decides between resuming and
   * reopening from it, and a stale closure here would read an empty fleet.
   */
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  // `eventLog` is a fresh object on every render, so the stable callbacks it
  // owns are pulled out by name: the tick loop must not restart every time an
  // event arrives.
  const pollEvents = eventLog.poll;
  const resetEvents = eventLog.reset;
  const setEventsPaused = eventLog.setPaused;

  const runningRef = useRef(false);
  runningRef.current = running;
  const speedRef = useRef(speed);
  speedRef.current = speed;
  /** The preset currently loaded, so RESET can restart the same run. */
  const scenarioRef = useRef<string | null>(null);
  /**
   * Wall time of the last clock advance. Shared with the commands that resume
   * the clock, so resuming never inherits a gap measured while paused and
   * over-advances the simulation on the first tick.
   */
  const lastAdvanceRef = useRef(Date.now());

  const report = useCallback((cause: unknown) => {
    const message = cause instanceof Error ? cause.message : String(cause);
    setError(message);
  }, []);

  /** One read pass over every view model the console renders. */
  const refresh = useCallback(async () => {
    const [nextSnapshot, nextMetrics, nextTelemetry] = await Promise.all([
      api.getSnapshot(),
      api.getMetrics(),
      api.getTelemetry(),
    ]);
    setSnapshot(nextSnapshot);
    setMetrics(nextMetrics);
    setTelemetry(nextTelemetry);
    return nextSnapshot;
  }, []);

  // Bootstrap: health, first read pass, and the scenario catalogue.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [nextHealth, nextScenarios] = await Promise.all([
          api.getHealth(),
          api.getScenarios(),
        ]);
        if (cancelled) return;
        setHealth(nextHealth);
        setScenarios(nextScenarios);
        const first = await refresh();
        if (cancelled) return;
        setConnection("online");
        setError(null);
        // A console is a live view, so the clock starts on its own. The backend
        // owns the determinism: it still only advances when the console asks.
        await api.resume();
        if (cancelled) return;
        setRunning(true);
        void api.dispatchPending().then(() => refresh()).catch(report);
        if (!cancelled && first.tasks.length > 0) setEventsPaused(false);
      } catch (cause) {
        if (cancelled) return;
        setConnection("offline");
        report(cause);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, report]);

  // Animation phase clock, independent of polling.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 120);
    return () => window.clearInterval(timer);
  }, []);

  /**
   * The tick loop.
   *
   * While running it advances the backend clock by exactly the simulated time
   * that has just elapsed, then reads the new state. Because simulated time
   * tracks wall time, the position the renderer projects between two polls
   * matches the position the next poll reports, which is what keeps a moving
   * robot from twitching. While paused it only reads, and it reads more slowly.
   */
  useEffect(() => {
    let stopped = false;
    let timer = 0;

    const loop = async () => {
      if (stopped) return;
      const isRunning = runningRef.current;
      const nowMs = Date.now();
      try {
        if (isRunning) {
          const elapsedS = ((nowMs - lastAdvanceRef.current) / 1000) * speedRef.current;
          const ticks = Math.min(
            MAX_TICKS_PER_CALL,
            Math.max(1, Math.round(elapsedS / BACKEND_TICK_S)),
          );
          await api.advance(ticks);
          lastAdvanceRef.current = nowMs;
          await refresh();
          setConnection("online");
        } else {
          lastAdvanceRef.current = nowMs;
          await refresh();
        }
        await pollEvents();
      } catch (cause) {
        setConnection("offline");
        report(cause);
        // A failed tick must not spin the loop: back off and try again.
        lastAdvanceRef.current = Date.now();
      }
      if (stopped) return;
      timer = window.setTimeout(loop, runningRef.current ? TICK_INTERVAL_MS : api.POLL_IDLE_MS);
    };

    void loop();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [refresh, pollEvents, report]);

  const guard = useCallback(
    async (label: string, action: () => Promise<void>) => {
      setBusy(label);
      try {
        await action();
        setError(null);
      } catch (cause) {
        report(cause);
      } finally {
        setBusy(null);
      }
    },
    [report],
  );

  const start = useCallback(
    () =>
      guard("resume", async () => {
        await api.resume();
        setRunning(true);
        setEventsPaused(false);
        // Resume from now, not from whenever the clock was last advanced.
        lastAdvanceRef.current = Date.now();
      }),
    [guard, setEventsPaused],
  );

  const pause = useCallback(
    () =>
      guard("pause", async () => {
        await api.pause();
        setRunning(false);
        setEventsPaused(true);
      }),
    [guard, setEventsPaused],
  );

  /**
   * Start a run that has nothing left to do.
   *
   * When every task is finished, pressing START used to look broken: the clock
   * advanced but no robot had work, so nothing moved. It now reopens the
   * scenario that is loaded, which is the only way to get fresh work without
   * inventing it in the browser.
   */

  const step = useCallback(
    () =>
      guard("step", async () => {
        await api.advance(1);
        lastAdvanceRef.current = Date.now();
        await refresh();
        await pollEvents();
      }),
    [guard, refresh, pollEvents],
  );

  const setSpeed = useCallback(
    (multiplier: number) =>
      guard("speed", async () => {
        await api.setSpeed(multiplier);
        setSpeedState(multiplier);
        setNotice(`simulation speed ${multiplier}x`);
      }),
    [guard],
  );

  /**
   * Restart the current run.
   *
   * A plain backend reset rebuilds the fleet and leaves no tasks registered, so
   * a reset console has nothing to do and simply looks broken. Reloading the
   * active scenario instead is what a user means by "reset": the same world and
   * fleet, its tasks registered again, negotiated again, and the clock running.
   */
  const reset = useCallback(
    (seed?: number) =>
      guard("reset", async () => {
        const scenario = scenarioRef.current;
        await api.reset(seed);
        if (scenario) {
          const response = await api.loadScenario({ name: scenario, ...(seed === undefined ? {} : { seed }) });
          resetEvents(response.last_event_sequence);
          await refresh();
          await api.dispatchPending();
        }
        await api.resume();
        setRunning(true);
        setEventsPaused(false);
        lastAdvanceRef.current = Date.now();
        await refresh();
        await pollEvents();
        setNotice(
          scenario ? `restarted ${scenario}` : "simulation reset",
        );
      }),
    [guard, refresh, pollEvents, resetEvents, setEventsPaused],
  );

  const dispatch = useCallback(
    () =>
      guard("dispatch", async () => {
        const response = await api.dispatchPending();
        setNotice(`dispatch: ${response.produced_event_types.length} events`);
        await refresh();
        await pollEvents();
      }),
    [guard, refresh, pollEvents],
  );

  const randomizeAssignment = useCallback(
    (jitter: number, seed?: number) =>
      guard("random", async () => {
        const response = await api.randomizeAssignment(jitter, seed);
        await refresh();
        await pollEvents();
        setNotice(
          `random assignment: ${response.produced_event_types.length} events`,
        );
      }),
    [guard, refresh, pollEvents],
  );

  /**
   * Load a scenario and start it.
   *
   * A preset is something to watch, not something to configure, so loading one
   * registers its tasks, negotiates them, and leaves the clock running. Leaving
   * it paused is what made a freshly loaded template look like a dead console.
   */
  const loadScenario = useCallback(
    (input: { name: string; robotCount?: number; columns?: number; rows?: number; seed?: number }) =>
      guard("scenario", async () => {
        scenarioRef.current = input.name;
        const response = await api.loadScenario({
          name: input.name,
          ...(input.robotCount === undefined ? {} : { robot_count: input.robotCount }),
          ...(input.columns === undefined ? {} : { columns: input.columns }),
          ...(input.rows === undefined ? {} : { rows: input.rows }),
          ...(input.seed === undefined ? {} : { seed: input.seed }),
        });
        // The stream was rebuilt, so the event cursor must restart with it.
        resetEvents(response.last_event_sequence);
        // Negotiate the preset's tasks, then let the clock run.
        const dispatched = await api.dispatchPending();
        await api.resume();
        setRunning(true);
        setEventsPaused(false);
        lastAdvanceRef.current = Date.now();
        await refresh();
        setScenarios(await api.getScenarios());
        await pollEvents();
        setNotice(
          `${response.scenario}: ${response.robots} robots, ${response.tasks.length} tasks, ` +
            `${response.columns}x${response.rows}, ${dispatched.produced_event_types.length} events`,
        );
      }),
    [guard, refresh, pollEvents, resetEvents, setEventsPaused],
  );


  /**
   * Read the current state without advancing anything.
   *
   * The demo runner needs this between stages: a stage ends by pausing, and
   * the panels must show the state the pause actually produced rather than the
   * last thing a background poll happened to see.
   */
  const refreshNow = useCallback(async () => {
    try {
      await refresh();
      await pollEvents();
      setConnection("online");
    } catch (cause) {
      setConnection("offline");
      report(cause);
    }
  }, [refresh, pollEvents, report]);

  const startOrReopen = useCallback(async () => {    const openTasks =
      snapshotRef.current?.tasks.filter(
        (task) => task.status !== "completed" && task.status !== "cancelled",
      ).length ?? 0;
    if (openTasks > 0) {
      await start();
      return;
    }
    await loadScenario({ name: scenarioRef.current ?? "normal" });
  }, [start, loadScenario]);

  const createTask = useCallback(
    (input: NewTaskInput) =>
      guard("task", async () => {
        await api.createTask({
          taskId: input.taskId,
          x: input.cellX,
          y: input.cellY,
          priority: input.priority,
          requiredCapability: input.requiredCapability,
          estimatedDurationS: input.estimatedDurationS,
        });
        await refresh();
        await pollEvents();
        setNotice(`task ${input.taskId} submitted`);
      }),
    [guard, refresh, pollEvents],
  );

  const injectFailure = useCallback(
    (robotId: string) =>
      guard("failure", async () => {
        await api.failRobot(robotId);
        await refresh();
        await pollEvents();
        setNotice(`failure injected on ${robotId}`);
      }),
    [guard, refresh, pollEvents],
  );

  const injectCommunicationLoss = useCallback(
    (robotId: string) =>
      guard("comm-loss", async () => {
        await api.loseCommunication(robotId);
        await refresh();
        await pollEvents();
        setNotice(`communication lost on ${robotId}`);
      }),
    [guard, refresh, pollEvents],
  );

  const restoreRobot = useCallback(
    (robotId: string) =>
      guard("restore", async () => {
        await api.restoreRobot(robotId);
        await refresh();
        await pollEvents();
        setNotice(`${robotId} restored`);
      }),
    [guard, refresh, pollEvents],
  );

  return useMemo(
    () => ({
      connection,
      health,
      snapshot,
      metrics,
      telemetry,
      scenarios,
      running,
      speed,
      busy,
      error,
      notice,
      now,
      start,
      startOrReopen,
      refreshNow,
      pause,
      step,
      setSpeed,
      reset,
      dispatch,
      randomizeAssignment,
      loadScenario,
      createTask,
      injectFailure,
      injectCommunicationLoss,
      restoreRobot,
      clearError: () => setError(null),
      clearNotice: () => setNotice(null),
      eventLog,
    }),
    [
      connection,
      health,
      snapshot,
      metrics,
      telemetry,
      scenarios,
      running,
      speed,
      busy,
      error,
      notice,
      now,
      start,
      startOrReopen,
      refreshNow,
      pause,
      step,
      setSpeed,
      reset,
      dispatch,
      randomizeAssignment,
      loadScenario,
      createTask,
      injectFailure,
      injectCommunicationLoss,
      restoreRobot,
      eventLog,
    ],
  );
}
