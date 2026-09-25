/**
 * Derived views over the canonical projection.
 *
 * The dashboard never computes domain state; it computes *views* over state the
 * backend already decided. Everything here is a pure function of a
 * `SimulationSnapshot` or the event log, which is what makes them testable
 * without a running runtime and keeps the React tree cheap.
 */

import type { DomainEvent, Robot, SimulationSnapshot, SystemMetrics, Task } from "./types";

export type StatusTone = "ok" | "warn" | "crit" | "idle" | "info";

export const ROBOT_STATUS_TONES: Record<Robot["status"], StatusTone> = {
  idle: "idle",
  active: "ok",
  blocked: "warn",
  charging: "info",
  degraded: "warn",
  failed: "crit",
  offline: "crit",
};

export const TASK_STATUS_TONES: Record<Task["status"], StatusTone> = {
  pending: "idle",
  negotiating: "info",
  assigned: "info",
  in_progress: "ok",
  blocked: "warn",
  recovery: "warn",
  completed: "ok",
  cancelled: "idle",
};

export function robotTone(robot: Robot): StatusTone {
  if (robot.communication_state === "lost") return "warn";
  if (robot.battery_percent <= 20) return "warn";
  return ROBOT_STATUS_TONES[robot.status];
}

export function taskTone(task: Task): StatusTone {
  return TASK_STATUS_TONES[task.status];
}

export function eventTone(event: DomainEvent): StatusTone {
  switch (event.event_type) {
    case "ROBOT_FAILED":
    case "DEADLOCK_DETECTED":
      return "crit";
    case "CONFLICT_DETECTED":
    case "COMMUNICATION_LOST":
    case "BATTERY_LOW":
    case "TASK_REASSIGNED":
    case "RECOVERY_STARTED":
      return "warn";
    case "TASK_COMPLETED":
    case "TASK_ASSIGNED":
      return "ok";
    case "NEGOTIATION_STARTED":
    case "BID_SUBMITTED":
    case "ROUTE_REQUESTED":
    case "ROUTE_PLANNED":
    case "ROUTE_REPLANNED":
      return "info";
    default:
      return "idle";
  }
}

export interface FleetCounts {
  total: number;
  active: number;
  idle: number;
  blocked: number;
  charging: number;
  degraded: number;
  failed: number;
  offline: number;
  lowBattery: number;
  unreachable: number;
}

export function fleetCounts(robots: readonly Robot[]): FleetCounts {
  const counts: FleetCounts = {
    total: robots.length,
    active: 0,
    idle: 0,
    blocked: 0,
    charging: 0,
    degraded: 0,
    failed: 0,
    offline: 0,
    lowBattery: 0,
    unreachable: 0,
  };
  for (const robot of robots) {
    if (robot.status === "active") counts.active += 1;
    if (robot.status === "idle") counts.idle += 1;
    if (robot.status === "blocked") counts.blocked += 1;
    if (robot.status === "charging") counts.charging += 1;
    if (robot.status === "degraded") counts.degraded += 1;
    if (robot.status === "failed") counts.failed += 1;
    if (robot.status === "offline") counts.offline += 1;
    if (robot.battery_percent <= 20) counts.lowBattery += 1;
    if (robot.communication_state === "lost") counts.unreachable += 1;
  }
  return counts;
}

export interface TaskCounts {
  total: number;
  pending: number;
  active: number;
  completed: number;
  blocked: number;
  recovery: number;
}

export function taskCounts(tasks: readonly Task[]): TaskCounts {
  const counts: TaskCounts = {
    total: tasks.length,
    pending: 0,
    active: 0,
    completed: 0,
    blocked: 0,
    recovery: 0,
  };
  for (const task of tasks) {
    if (task.status === "pending" || task.status === "negotiating") counts.pending += 1;
    if (task.status === "assigned" || task.status === "in_progress") counts.active += 1;
    if (task.status === "completed") counts.completed += 1;
    if (task.status === "blocked") counts.blocked += 1;
    if (task.status === "recovery") counts.recovery += 1;
  }
  return counts;
}

export interface StatTile {
  key: string;
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
}

/**
 * The headline numbers.
 *
 * Every value is read from the snapshot's own metrics. Where a metric is absent
 * the tile shows a dash and says so, rather than substituting a plausible
 * number — an operations console that invents a figure is worse than one that
 * admits a gap.
 */
export function statTiles(snapshot: SimulationSnapshot | null): StatTile[] {
  if (!snapshot) {
    return [
      { key: "fleet", label: "Fleet", value: "—", detail: "Awaiting snapshot", tone: "idle" },
      { key: "work", label: "Tasks in hand", value: "—", detail: "Awaiting snapshot", tone: "idle" },
      { key: "energy", label: "Mean battery", value: "—", detail: "Awaiting snapshot", tone: "idle" },
      { key: "safety", label: "Open conflicts", value: "—", detail: "Awaiting snapshot", tone: "idle" },
      { key: "throughput", label: "Event rate", value: "—", detail: "Awaiting snapshot", tone: "idle" },
    ];
  }

  const metrics: SystemMetrics = snapshot.metrics;
  const counts = fleetCounts(snapshot.robots);
  const tasks = taskCounts(snapshot.tasks);
  const extra = metrics.extra_metrics;

  return [
    {
      key: "fleet",
      label: "Fleet",
      value: String(counts.total),
      detail: `${counts.active} active · ${counts.idle} idle`,
      // The backend's own failure count is authoritative here: it counts robots
      // the dashboard may not even be holding in its projection.
      tone: metrics.failed_robots > 0 ? "crit" : "ok",
    },
    {
      key: "work",
      label: "Tasks in hand",
      value: String(tasks.active),
      detail: `${tasks.pending} queued · ${tasks.completed} done`,
      tone: tasks.pending > tasks.active * 4 ? "warn" : "ok",
    },
    {
      key: "energy",
      label: "Mean battery",
      value: `${Math.round(metrics.average_battery_percent)}%`,
      detail: counts.lowBattery > 0 ? `${counts.lowBattery} below reserve` : "All above reserve",
      tone: counts.lowBattery > 0 ? "warn" : "ok",
    },
    {
      key: "safety",
      label: "Open conflicts",
      value: String(metrics.open_conflicts),
      detail: `${metrics.detected_deadlocks} deadlocks detected`,
      tone: metrics.open_conflicts > 0 ? "warn" : "ok",
    },
    {
      key: "throughput",
      label: "Event rate",
      value: metrics.event_throughput_per_s.toFixed(0),
      detail: `per second · tick ${formatStage(extra.average_tick_ms)}`,
      tone: "info",
    },
  ];
}

function formatStage(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(0)} ms`;
}

export interface PerformanceFacts {
  routeEfficiency: number | null;
  plannerLatencyMs: number | null;
  tickLatencyMs: number | null;
  allocationLatencyMs: number | null;
  controllerOutages: number | null;
  controllerOutageSeconds: number | null;
  yieldCount: number | null;
  deadlocksResolved: number | null;
  returnsToCharger: number | null;
  batteryLowEvents: number | null;
  stageCosts: Array<{ stage: string; ms: number }>;
}

export function performanceFacts(snapshot: SimulationSnapshot | null): PerformanceFacts {
  const empty: PerformanceFacts = {
    routeEfficiency: null,
    plannerLatencyMs: null,
    tickLatencyMs: null,
    allocationLatencyMs: null,
    controllerOutages: null,
    controllerOutageSeconds: null,
    yieldCount: null,
    deadlocksResolved: null,
    returnsToCharger: null,
    batteryLowEvents: null,
    stageCosts: [],
  };
  if (!snapshot) return empty;

  const extra = snapshot.metrics.extra_metrics;
  const stageCosts = Object.entries(extra)
    .filter(([key]) => key.startsWith("stage_") && key.endsWith("_ms"))
    .map(([key, value]) => ({ stage: key.replace("stage_", "").replace("_ms", ""), ms: value }))
    .sort((left, right) => right.ms - left.ms);

  return {
    routeEfficiency: extra.route_efficiency_ratio ?? null,
    plannerLatencyMs: extra.planner_latency_ms ?? null,
    tickLatencyMs: extra.average_tick_ms ?? null,
    allocationLatencyMs: snapshot.metrics.average_allocation_latency_ms || null,
    controllerOutages: extra.controller_outages ?? null,
    controllerOutageSeconds: extra.controller_outage_seconds ?? null,
    yieldCount: extra.conflict_yields ?? null,
    deadlocksResolved: extra.deadlocks_resolved ?? null,
    returnsToCharger: extra.returns_to_charger ?? null,
    batteryLowEvents: extra.battery_low_events ?? null,
    stageCosts,
  };
}

export type RosterFilter = "all" | "active" | "attention" | "charging" | "low";

export interface RosterQuery {
  text: string;
  filter: RosterFilter;
  sort: RosterSort;
}

export type RosterSort = "id" | "battery" | "status" | "workload";

const ATTENTION_STATUSES: ReadonlySet<Robot["status"]> = new Set([
  "blocked",
  "degraded",
  "failed",
  "offline",
]);

export function matchesFilter(robot: Robot, filter: RosterFilter): boolean {
  switch (filter) {
    case "active":
      return robot.status === "active";
    case "attention":
      return ATTENTION_STATUSES.has(robot.status) || robot.communication_state === "lost";
    case "charging":
      return robot.status === "charging";
    case "low":
      return robot.battery_percent <= 20;
    default:
      return true;
  }
}

export function matchesQuery(robot: Robot, text: string): boolean {
  const needle = text.trim().toLowerCase();
  if (needle === "") return true;
  if (robot.robot_id.toLowerCase().includes(needle)) return true;
  if (robot.current_task_id?.toLowerCase().includes(needle)) return true;
  return robot.capabilities.some((capability) => capability.toLowerCase().includes(needle));
}

const STATUS_ORDER: Record<Robot["status"], number> = {
  failed: 0,
  offline: 1,
  degraded: 2,
  blocked: 3,
  charging: 4,
  active: 5,
  idle: 6,
};

export function compareRobots(sort: RosterSort) {
  return (left: Robot, right: Robot): number => {
    switch (sort) {
      case "battery":
        return left.battery_percent - right.battery_percent || left.robot_id.localeCompare(right.robot_id);
      case "status":
        return (
          STATUS_ORDER[left.status] - STATUS_ORDER[right.status] ||
          left.robot_id.localeCompare(right.robot_id)
        );
      case "workload":
        return right.workload - left.workload || left.robot_id.localeCompare(right.robot_id);
      default:
        return left.robot_id.localeCompare(right.robot_id);
    }
  };
}

export function filterRobots(robots: readonly Robot[], query: RosterQuery): Robot[] {
  return robots
    .filter((robot) => matchesFilter(robot, query.filter) && matchesQuery(robot, query.text))
    .sort(compareRobots(query.sort));
}

export function filterTasks(tasks: readonly Task[], text: string): Task[] {
  const needle = text.trim().toLowerCase();
  return tasks
    .filter((task) => {
      if (needle === "") return true;
      return (
        task.task_id.includes(needle) ||
        (task.assigned_robot_id?.includes(needle) ?? false)
      );
    })
    .sort((left, right) => left.task_id.localeCompare(right.task_id));
}

export interface BidRecord {
  bidId: string;
  robotId: string;
  taskId: string;
  totalCost: number;
  distanceCost: number;
  batteryCost: number;
  workloadCost: number;
  estimatedCompletionTimeS: number;
  sequence: number;
  occurredAtS: number;
}

/**
 * Extract bid records from the canonical event log.
 *
 * This is a view over events the backend already published, not a second
 * negotiation implementation: the dashboard never scores or chooses anything.
 */
export function bidsFromEvents(events: readonly DomainEvent[], limit = 120): BidRecord[] {
  const records: BidRecord[] = [];
  for (let index = events.length - 1; index >= 0 && records.length < limit; index -= 1) {
    const event = events[index];
    if (event.event_type !== "BID_SUBMITTED") continue;
    const bid = event.payload.bid as Record<string, unknown> | undefined;
    if (!bid) continue;
    records.push({
      bidId: String(bid.bid_id ?? "—"),
      robotId: String(bid.robot_id ?? "—"),
      taskId: String(bid.task_id ?? "—"),
      totalCost: numberOr(bid.total_cost),
      distanceCost: numberOr(bid.distance_cost),
      batteryCost: numberOr(bid.battery_cost),
      workloadCost: numberOr(bid.workload_cost),
      estimatedCompletionTimeS: numberOr(bid.estimated_completion_time_s),
      sequence: event.sequence,
      occurredAtS: event.occurred_at_s,
    });
  }
  return records;
}

export interface RecoveryRecord {
  actionId: string;
  actionType: string;
  targetRobotIds: string[];
  affectedTaskIds: string[];
  reason: string;
  sequence: number;
  occurredAtS: number;
}

export function recoveriesFromEvents(
  events: readonly DomainEvent[],
  limit = 120,
): RecoveryRecord[] {
  const records: RecoveryRecord[] = [];
  for (let index = events.length - 1; index >= 0 && records.length < limit; index -= 1) {
    const event = events[index];
    if (event.event_type !== "RECOVERY_STARTED") continue;
    const action = event.payload.action as Record<string, unknown> | undefined;
    if (!action) continue;
    records.push({
      actionId: String(action.action_id ?? "—"),
      actionType: String(action.action_type ?? "—"),
      targetRobotIds: stringArray(action.target_robot_ids),
      affectedTaskIds: stringArray(action.affected_task_ids),
      reason: String(action.reason ?? ""),
      sequence: event.sequence,
      occurredAtS: event.occurred_at_s,
    });
  }
  return records;
}

export interface DeadlockCycle {
  deadlockId: string;
  robotIds: string[];
  blockedTaskIds: string[];
  detectedAtS: number;
  confidence: number | null;
}

/**
 * Track which deadlocks are still unresolved.
 *
 * The snapshot has no deadlock collection, so this is derived conservatively
 * from the canonical events: a cycle stays live until a recovery, a reassignment
 * or a completion touches one of its robots or tasks. That limitation is real
 * and is recorded in `dashboard/README.md` rather than papered over.
 */
export function activeDeadlocks(events: readonly DomainEvent[]): DeadlockCycle[] {
  const cycles = new Map<string, DeadlockCycle>();
  for (const event of events) {
    if (event.event_type === "DEADLOCK_DETECTED") {
      const report = event.payload.report as Record<string, unknown> | undefined;
      if (!report) continue;
      const id = String(report.deadlock_id ?? `deadlock-${event.sequence}`);
      cycles.set(id, {
        deadlockId: id,
        robotIds: stringArray(report.cycle_robot_ids),
        blockedTaskIds: stringArray(report.blocked_task_ids),
        detectedAtS: event.occurred_at_s,
        confidence: typeof report.confidence === "number" ? report.confidence : null,
      });
      continue;
    }

    if (event.event_type === "RECOVERY_STARTED") {
      const action = event.payload.action as Record<string, unknown> | undefined;
      if (!action) continue;
      const robots = new Set(stringArray(action.target_robot_ids));
      const tasks = new Set(stringArray(action.affected_task_ids));
      for (const [id, cycle] of cycles) {
        if (
          cycle.robotIds.some((robotId) => robots.has(robotId)) ||
          cycle.blockedTaskIds.some((taskId) => tasks.has(taskId))
        ) {
          cycles.delete(id);
        }
      }
      continue;
    }

    if (event.event_type === "TASK_REASSIGNED" || event.event_type === "TASK_COMPLETED") {
      const taskId = event.payload.task_id;
      if (typeof taskId !== "string") continue;
      for (const [id, cycle] of cycles) {
        if (cycle.blockedTaskIds.includes(taskId)) cycles.delete(id);
      }
    }
  }
  return [...cycles.values()];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberOr(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
