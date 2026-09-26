/**
 * The one place that talks to the backend.
 *
 * Components never call `fetch` themselves: they call the functions below, so
 * the base URL, the error shape, and the response types live in one file. The
 * base URL is configuration, not a literal in component code.
 */

import type {
  BackendEvent,
  CommandResponse,
  HealthResponse,
  ScenarioListResponse,
  ScenarioLoadedResponse,
  SnapshotResponse,
  SystemMetrics,
  Task,
  TelemetryResponse,
  WorldState,
} from "./types";

/** In development Vite proxies /api to the backend, so requests are same-origin. */
export const API_BASE_URL: string =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined)?.replace(/\/+$/, "") ?? "";

export const API_PREFIX = "/api/v1";

/** Polling cadence. Slowed automatically for big fleets and while paused. */
export const POLL_ACTIVE_MS = Number(
  import.meta.env?.VITE_POLL_INTERVAL_MS ?? 400,
);
export const POLL_IDLE_MS = Number(
  import.meta.env?.VITE_POLL_INTERVAL_IDLE_MS ?? 1200,
);

export class BackendError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`backend returned ${status}: ${detail}`);
    this.name = "BackendError";
    this.status = status;
    this.detail = detail;
  }
}

function url(path: string): string {
  return `${API_BASE_URL}${API_PREFIX}${path}`;
}

async function detailFrom(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      return JSON.stringify(detail);
    }
    return JSON.stringify(body);
  } catch {
    return response.statusText;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new BackendError(response.status, await detailFrom(response));
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function getSnapshot(): Promise<SnapshotResponse> {
  return request<SnapshotResponse>("/snapshot");
}

export function getWorld(): Promise<WorldState> {
  return request<WorldState>("/world");
}

export function getMetrics(): Promise<SystemMetrics> {
  return request<SystemMetrics>("/metrics");
}

export function getTelemetry(): Promise<TelemetryResponse> {
  return request<TelemetryResponse>("/telemetry");
}

export function getScenarios(): Promise<ScenarioListResponse> {
  return request<ScenarioListResponse>("/scenarios");
}

/** Every registered task, in canonical `task_id` order. */
export function getTasks(): Promise<Task[]> {
  return request<Task[]>("/tasks");
}

export function getEvents(afterSequence: number): Promise<BackendEvent[]> {
  const cursor = Math.max(0, Math.trunc(afterSequence));
  return request<BackendEvent[]>(`/events?after_sequence=${cursor}`);
}

export function advance(ticks: number): Promise<CommandResponse> {
  return request<CommandResponse>(`/simulation/advance?ticks=${Math.max(0, Math.trunc(ticks))}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function pause(): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/pause", { method: "POST", body: "{}" });
}

export function resume(): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/resume", { method: "POST", body: "{}" });
}

export function setSpeed(multiplier: number): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/speed", {
    method: "POST",
    body: JSON.stringify({ multiplier }),
  });
}

export function reset(seed = 2026): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/reset", {
    method: "POST",
    body: JSON.stringify({ seed }),
  });
}

export function dispatchPending(): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/dispatch", {
    method: "POST",
    body: "{}",
  });
}

/**
 * Re-run every open task's negotiation with randomised bid costs.
 *
 * This is a real allocation round on the backend: open tasks lose their owner,
 * bids are collected again with a randomised distance term, and allocation
 * still awards each task to the cheapest bid. Passing a `seed` makes the round
 * reproducible.
 */
export function randomizeAssignment(
  jitter: number,
  seed?: number,
): Promise<CommandResponse> {
  return request<CommandResponse>("/simulation/random-assignment", {
    method: "POST",
    body: JSON.stringify({ jitter, ...(seed === undefined ? {} : { seed }) }),
  });
}

export function loadScenario(body: {
  name: string;
  robot_count?: number;
  columns?: number;
  rows?: number;
  seed?: number;
  run_initial_tasks?: boolean;
}): Promise<ScenarioLoadedResponse> {
  return request<ScenarioLoadedResponse>("/simulation/scenario", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface NewTaskBody {
  taskId: string;
  x: number;
  y: number;
  priority: number;
  requiredCapability: string;
  estimatedDurationS: number;
}

export function createTask(body: NewTaskBody): Promise<CommandResponse> {
  return request<CommandResponse>("/tasks", {
    method: "POST",
    body: JSON.stringify({
      // The canonical `Task`, exactly as the contract defines it. The backend
      // expands this into a `CreateTaskCommand` and runs the normal
      // negotiation, allocation, and planning path.
      task: {
        task_id: body.taskId,
        target: { x: body.x, y: body.y },
        priority: body.priority,
        required_capabilities: body.requiredCapability ? [body.requiredCapability] : [],
        estimated_duration_s: body.estimatedDurationS,
        status: "pending",
        assigned_robot_id: null,
        created_at_s: 0,
      },
    }),
  });
}

/**
 * Drop a working robot's charge below the low threshold.
 *
 * The backend observes the new level on its next movement tick and publishes the
 * ordinary `BATTERY_LOW` event, which is the trigger the coordinator already
 * handles for migrating a task. Nothing about the reassignment is faked here.
 */
export function drainBattery(
  robotId: string,
  percent = 8,
): Promise<CommandResponse> {
  return request<CommandResponse>("/faults/battery-drain", {
    method: "POST",
    body: JSON.stringify({ robot_id: robotId, percent }),
  });
}

export function failRobot(
  robotId: string,
  detectedAtS = 0,
): Promise<CommandResponse> {
  return request<CommandResponse>("/faults/failure", {
    method: "POST",
    body: JSON.stringify({
      robot_id: robotId,
      failure: {
        kind: "actuator",
        code: "drive-failure",
        detected_at_s: detectedAtS,
        detail: "Injected from the command console.",
      },
    }),
  });
}

export function loseCommunication(
  robotId: string,
  timeoutS = 3,
): Promise<CommandResponse> {
  return request<CommandResponse>("/faults/communication-loss", {
    method: "POST",
    body: JSON.stringify({ robot_id: robotId, timeout_s: timeoutS }),
  });
}

export function restoreRobot(robotId: string): Promise<CommandResponse> {
  return request<CommandResponse>("/faults/restore", {
    method: "POST",
    body: JSON.stringify({ robot_id: robotId }),
  });
}
