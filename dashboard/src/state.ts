import type { ControlCommand, DomainEvent, SimulationSnapshot, Task } from "./types";

export interface DeadlockCycle {
  deadlockId: string;
  robotIds: string[];
  blockedTaskIds: string[];
}

export interface CreateTaskInput {
  taskId: string;
  targetX: number;
  targetY: number;
  priority: number;
  capability: Task["required_capabilities"][number];
  estimatedDurationS: number;
}

export function mergeEvents(current: DomainEvent[], incoming: DomainEvent[]): DomainEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  incoming.forEach((event) => byId.set(event.event_id, event));
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence).slice(-200);
}

function payloadRecord(event: DomainEvent): Record<string, unknown> {
  return event.payload;
}

function payloadStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function intersects(left: string[], right: string[]): boolean {
  const rightValues = new Set(right);
  return left.some((value) => rightValues.has(value));
}

export function applyEventToDeadlockCycles(current: DeadlockCycle[], event: DomainEvent): DeadlockCycle[] {
  const payload = payloadRecord(event);
  if (event.event_type === "DEADLOCK_DETECTED") {
    const report = payload.report;
    if (!report || typeof report !== "object" || Array.isArray(report)) return current;
    const reportRecord = report as Record<string, unknown>;
    const deadlockId = typeof reportRecord.deadlock_id === "string" ? reportRecord.deadlock_id : `deadlock-${event.event_id}`;
    const nextCycle: DeadlockCycle = {
      deadlockId,
      robotIds: payloadStringArray(reportRecord.cycle_robot_ids),
      blockedTaskIds: payloadStringArray(reportRecord.blocked_task_ids),
    };
    if (nextCycle.robotIds.length < 2) return current;
    return [...current.filter((cycle) => cycle.deadlockId !== deadlockId), nextCycle];
  }

  if (event.event_type === "RECOVERY_STARTED") {
    const action = payload.action;
    if (!action || typeof action !== "object" || Array.isArray(action)) return current;
    const actionRecord = action as Record<string, unknown>;
    const targetRobotIds = payloadStringArray(actionRecord.target_robot_ids);
    const affectedTaskIds = payloadStringArray(actionRecord.affected_task_ids);
    return current.filter((cycle) => !intersects(cycle.robotIds, targetRobotIds) && !intersects(cycle.blockedTaskIds, affectedTaskIds));
  }

  if (event.event_type === "TASK_REASSIGNED" || event.event_type === "TASK_COMPLETED") {
    const taskId = payload.task_id;
    if (typeof taskId !== "string") return current;
    return current.filter((cycle) => !cycle.blockedTaskIds.includes(taskId));
  }

  return current;
}

export function getActiveDeadlockCycles(events: DomainEvent[]): DeadlockCycle[] {
  return events.reduce(applyEventToDeadlockCycles, [] as DeadlockCycle[]);
}

export function getRuntimeSpeedMultiplier(snapshot: SimulationSnapshot | null): number {
  const value = snapshot?.metrics.extra_metrics.simulation_speed_multiplier;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 1;
}

export function createDebouncedSnapshotRefresher(
  refresh: () => void | Promise<void>,
  delayMs = 150,
): { schedule: () => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending = false;
  const schedule = () => {
    pending = true;
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = undefined;
      if (!pending) return;
      pending = false;
      void refresh();
    }, delayMs);
  };
  const cancel = () => {
    pending = false;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  return { schedule, cancel };
}

const identifierPattern = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const capabilities = new Set<Task["required_capabilities"][number]>(["transport", "pick", "tug", "inspect", "deliver"]);

export function buildCreateTaskCommand(
  input: CreateTaskInput,
  base: { commandId: string; issuedAtS: number },
): ControlCommand {
  if (!identifierPattern.test(input.taskId)) throw new Error("Task ID must use lowercase kebab case.");
  if (!Number.isFinite(input.targetX) || input.targetX < 0 || !Number.isFinite(input.targetY) || input.targetY < 0) throw new Error("Task target coordinates must be non-negative numbers.");
  if (!Number.isInteger(input.priority) || input.priority < 1 || input.priority > 5) throw new Error("Task priority must be an integer from 1 to 5.");
  if (!capabilities.has(input.capability)) throw new Error("Task capability is not canonical.");
  if (!Number.isFinite(input.estimatedDurationS) || input.estimatedDurationS <= 0) throw new Error("Estimated duration must be greater than zero.");

  const task: Task = {
    task_id: input.taskId,
    target: { x: input.targetX, y: input.targetY },
    priority: input.priority,
    required_capabilities: [input.capability],
    estimated_duration_s: input.estimatedDurationS,
    status: "pending",
    assigned_robot_id: null,
    created_at_s: base.issuedAtS,
  };
  return {
    command_id: base.commandId,
    schema_version: 1,
    command_type: "CREATE_TASK",
    issued_at_s: base.issuedAtS,
    task,
  };
}
