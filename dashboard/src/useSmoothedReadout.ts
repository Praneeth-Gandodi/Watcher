/**
 * Eased display values for the headline tiles.
 *
 * The runtime republishes every metric five times a second, so a tile that
 * renders the raw number changes faster than anyone can read it: the digits
 * jitter and the eye is drawn to a figure that has not meaningfully moved.
 *
 * This hook eases the *displayed* value toward the raw one. It is deliberately
 * not a filter on the data:
 *
 * - a jump larger than a quarter of the value is treated as a real state
 *   change and applied immediately, so a genuine transition is never hidden
 *   behind a slow animation
 * - the underlying snapshot is untouched, so the map, the roster, the tables
 *   and every command still read exact values
 * - no tone is ever derived from this hook. `statTiles` decides every tone from
 *   the raw metric, so smoothing can delay a warning or soften a failure; it
 *   can only change how fast a number is drawn
 *
 * Only the rate of change is damped. `statTiles` remains the single place that
 * decides what each headline number means.
 */

import { useEffect, useRef, useState } from "react";

import { fleetCounts, taskCounts } from "./selectors";
import type { ReadoutOverrides } from "./selectors";
import type { SimulationSnapshot } from "./types";

/** How often the eased value is advanced. */
const FRAME_MS = 180;

/** Fraction of the remaining distance covered per frame. */
const EASE = 0.22;

type Readout = Required<ReadoutOverrides>;

function readTargets(snapshot: SimulationSnapshot | null): Readout | null {
  if (!snapshot) return null;
  const robots = fleetCounts(snapshot.robots);
  const tasks = taskCounts(snapshot.tasks);
  const metrics = snapshot.metrics;
  const tick = metrics.extra_metrics.average_tick_ms;
  return {
    totalRobots: robots.total,
    activeRobots: robots.active,
    idleRobots: robots.idle,
    blockedRobots: robots.blocked,
    lowBatteryRobots: robots.lowBattery,
    activeTasks: tasks.active,
    pendingTasks: tasks.pending,
    completedTasks: tasks.completed,
    averageBatteryPercent: metrics.average_battery_percent,
    openConflicts: metrics.open_conflicts,
    detectedDeadlocks: metrics.detected_deadlocks,
    eventThroughput: metrics.event_throughput_per_s,
    tickMs: typeof tick === "number" && Number.isFinite(tick) ? tick : 0,
  };
}

/** A change this large is a state transition, not jitter, so it is not eased. */
function isDiscontinuity(from: number, to: number): boolean {
  return Math.abs(to - from) > Math.max(4, Math.abs(to) * 0.25);
}

function ease(current: Readout, target: Readout): { next: Readout; settled: boolean } {
  const next = {} as Readout;
  let settled = true;
  for (const key of Object.keys(target) as Array<keyof Readout>) {
    const to = target[key];
    const from = current[key];
    if (!Number.isFinite(from) || isDiscontinuity(from, to)) {
      next[key] = to;
      continue;
    }
    const value = from + (to - from) * EASE;
    next[key] = value;
    if (Math.abs(value - to) > 0.04) settled = false;
  }
  return { next, settled };
}

/** Stable identity, so `statTiles` is not recomputed on every render. */
const NO_READOUT: ReadoutOverrides = {};

export function useSmoothedReadout(snapshot: SimulationSnapshot | null): ReadoutOverrides {
  const targetRef = useRef<Readout | null>(null);
  const displayRef = useRef<Readout | null>(null);
  const [, bump] = useState(0);

  // The raw figures are re-read on every render, so the interval below always
  // eases toward the latest snapshot rather than a captured one.
  targetRef.current = readTargets(snapshot);

  useEffect(() => {
    if (targetRef.current === null) return;
    const timer = setInterval(() => {
      const target = targetRef.current;
      if (!target) return;
      const current = displayRef.current;
      if (!current) {
        displayRef.current = target;
        bump((n) => n + 1);
        return;
      }
      const { next, settled } = ease(current, target);
      if (settled && displayRef.current !== target) {
        displayRef.current = target;
        bump((n) => n + 1);
        return;
      }
      if (!settled) {
        displayRef.current = next;
        bump((n) => n + 1);
      }
    }, FRAME_MS);
    return () => clearInterval(timer);
  }, []);

  return displayRef.current ?? NO_READOUT;
}
