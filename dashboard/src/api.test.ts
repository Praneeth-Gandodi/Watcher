import { afterEach, describe, expect, it, vi } from "vitest";
import { connectToEvents, getEvents, getHealth, getSnapshot } from "./api";

const snapshot = {
  simulation_time_s: 1,
  revision: 1,
  last_event_sequence: 1,
  controller_available: true,
  world: { width_m: 40, height_m: 24, cell_size_m: 2, columns: 20, rows: 12, cells: [], revision: 1 },
  robots: [],
  tasks: [],
  routes: [],
  conflicts: [],
  metrics: {
    active_robots: 0,
    failed_robots: 0,
    communication_lost_robots: 0,
    pending_tasks: 0,
    completed_tasks: 0,
    open_conflicts: 0,
    detected_deadlocks: 0,
    task_reassignments: 0,
    average_battery_percent: 0,
    average_allocation_latency_ms: 0,
    event_throughput_per_s: 0,
    controller_available: true,
    extra_metrics: {},
  },
};

const event = {
  event_id: "event-001",
  sequence: 2,
  schema_version: 1,
  event_type: "TASK_CREATED",
  producer: "runtime",
  correlation_id: "task-001",
  occurred_at_s: 1,
  payload: { task_id: "task-001" },
};

function mockResponse(body: unknown, ok = true, status = 200): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? "OK" : "Unavailable",
    json: async () => body,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("dashboard API boundary", () => {
  it("loads a validated snapshot", async () => {
    mockResponse(snapshot);
    await expect(getSnapshot()).resolves.toMatchObject({ revision: 1, world: { columns: 20 } });
  });

  it("rejects an invalid snapshot instead of trusting a cast", async () => {
    mockResponse({ ...snapshot, world: { ...snapshot.world, width_m: 0 } });
    await expect(getSnapshot()).rejects.toThrow(/width_m/);
  });

  it("surfaces API errors and offline failures", async () => {
    mockResponse({}, false, 503);
    await expect(getHealth()).rejects.toThrow("API 503");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network offline")));
    await expect(getHealth()).rejects.toThrow("network offline");
  });

  it("validates event history responses", async () => {
    mockResponse([event]);
    await expect(getEvents(1)).resolves.toHaveLength(1);
    mockResponse([{ ...event, schema_version: 2 }]);
    await expect(getEvents(1)).rejects.toThrow(/schema_version/);
  });

  it("validates WebSocket frames before publishing them", () => {
    class FakeWebSocket {
      static instance: FakeWebSocket;
      readonly url: string;
      private readonly listeners = new Map<string, (event: unknown) => void>();
      constructor(url: string) {
        this.url = url;
        FakeWebSocket.instance = this;
      }
      addEventListener(type: string, listener: (event: unknown) => void): void {
        this.listeners.set(type, listener);
      }
      emit(type: string, event: unknown): void {
        this.listeners.get(type)?.(event);
      }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("window", { location: { origin: "http://dashboard.test" } });
    const onEvent = vi.fn();
    const onError = vi.fn();
    connectToEvents(1, onEvent, vi.fn(), vi.fn(), onError);
    const socket = FakeWebSocket.instance;
    socket.emit("message", { data: JSON.stringify(event) });
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ event_id: "event-001" }));
    expect(socket.url).toContain("/api/v1/stream?after_sequence=1");
    socket.emit("message", { data: JSON.stringify({ ...event, event_type: "BAD_EVENT" }) });
    expect(onError).toHaveBeenCalledTimes(1);
  });
});
