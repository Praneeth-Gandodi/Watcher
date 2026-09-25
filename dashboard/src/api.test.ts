import { afterEach, describe, expect, it, vi } from "vitest";

import { buildStreamUrl, connectToEvents, getEvents, getHealth, getSnapshot } from "./api";
import { parseStreamFrame } from "./frames";

const world = {
  width_m: 40,
  height_m: 24,
  cell_size_m: 2,
  columns: 20,
  rows: 12,
  cells: [],
  revision: 1,
};

const metrics = {
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
};

const snapshot = {
  simulation_time_s: 1,
  revision: 1,
  last_event_sequence: 1,
  controller_available: true,
  world,
  robots: [],
  tasks: [],
  routes: [],
  conflicts: [],
  metrics,
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status,
      statusText: ok ? "OK" : "Unavailable",
      json: async () => body,
    }),
  );
}

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

  emit(type: string, payload: unknown): void {
    this.listeners.get(type)?.(payload);
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("snapshot boundary", () => {
  it("loads a validated snapshot", async () => {
    mockResponse(snapshot);
    await expect(getSnapshot()).resolves.toMatchObject({ revision: 1, world: { columns: 20 } });
  });

  it("rejects an invalid snapshot instead of trusting a cast", async () => {
    mockResponse({ ...snapshot, world: { ...world, width_m: 0 } });
    await expect(getSnapshot()).rejects.toThrow(/width_m/);
  });

  it("rejects a snapshot whose cursor is behind its revision", async () => {
    mockResponse({ ...snapshot, last_event_sequence: 0 });
    await expect(getSnapshot()).rejects.toThrow(/last_event_sequence/);
  });

  it("surfaces API errors and offline failures", async () => {
    mockResponse({}, false, 503);
    await expect(getHealth()).rejects.toThrow("API 503");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network offline")));
    await expect(getHealth()).rejects.toThrow("network offline");
  });
});

describe("event history", () => {
  it("validates event history responses", async () => {
    mockResponse({ events: [event], last_event_sequence: 2 });
    await expect(getEvents(1)).resolves.toHaveLength(1);
  });

  it("rejects an event with an unknown type", async () => {
    mockResponse({ events: [{ ...event, event_type: "NOT_A_REAL_EVENT" }], last_event_sequence: 2 });
    await expect(getEvents(0)).rejects.toThrow(/canonical event type/);
  });

  it("names the offending index when history is malformed", async () => {
    mockResponse({ events: [event, { ...event, sequence: 0 }], last_event_sequence: 2 });
    await expect(getEvents(0)).rejects.toThrow(/events\[1\]/);
  });
});

describe("stream frames", () => {
  it("accepts a snapshot frame", () => {
    const frame = parseStreamFrame({ kind: "snapshot", data: snapshot });
    expect(frame.kind).toBe("snapshot");
  });

  it("accepts an event frame", () => {
    const frame = parseStreamFrame({ kind: "event", data: event });
    expect(frame.kind).toBe("event");
  });

  it("accepts a cursor frame", () => {
    const frame = parseStreamFrame({ kind: "cursor", data: { last_event_sequence: 12 } });
    expect(frame).toEqual({ kind: "cursor", lastEventSequence: 12 });
  });

  it("rejects an unknown frame kind", () => {
    expect(() => parseStreamFrame({ kind: "telemetry", data: {} })).toThrow(/unknown stream frame/);
  });

  it("rejects a frame with no payload", () => {
    expect(() => parseStreamFrame({ kind: "event" })).toThrow(/must be an object/);
  });

  it("rejects a cursor with a non-numeric sequence", () => {
    expect(() =>
      parseStreamFrame({ kind: "cursor", data: { last_event_sequence: "twelve" } }),
    ).toThrow(/finite number/);
  });

  it("rejects a snapshot frame carrying an invalid snapshot", () => {
    expect(() =>
      parseStreamFrame({ kind: "snapshot", data: { ...snapshot, world: null } }),
    ).toThrow();
  });
});

describe("event stream", () => {
  function connect(handlers: Parameters<typeof connectToEvents>[1]) {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("window", { location: { origin: "http://dashboard.test" } });
    connectToEvents(1, handlers);
    return FakeWebSocket.instance;
  }

  it("builds the stream url from the api base and cursor", () => {
    vi.stubGlobal("window", { location: { origin: "http://dashboard.test" } });
    expect(buildStreamUrl(7)).toBe("ws://dashboard.test/api/v1/stream?after_sequence=7");
  });

  it("upgrades to a secure socket on an https origin", () => {
    vi.stubGlobal("window", { location: { origin: "https://watcher.example" } });
    expect(buildStreamUrl(1)).toContain("wss://watcher.example/api/v1/stream");
  });

  it("delivers validated event frames", () => {
    const onFrame = vi.fn();
    const socket = connect({
      onFrame,
      onOpen: vi.fn(),
      onClose: vi.fn(),
      onProtocolError: vi.fn(),
    });
    socket.emit("message", { data: JSON.stringify({ kind: "event", data: event }) });
    expect(onFrame).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "event", event: expect.objectContaining({ event_id: "event-001" }) }),
    );
  });

  it("delivers a snapshot frame as a resynchronisation point", () => {
    const onFrame = vi.fn();
    const socket = connect({
      onFrame,
      onOpen: vi.fn(),
      onClose: vi.fn(),
      onProtocolError: vi.fn(),
    });
    socket.emit("message", { data: JSON.stringify({ kind: "snapshot", data: snapshot }) });
    expect(onFrame).toHaveBeenCalledWith(expect.objectContaining({ kind: "snapshot" }));
  });

  it("reports a protocol error for an invalid frame without closing", () => {
    const onProtocolError = vi.fn();
    const socket = connect({
      onFrame: vi.fn(),
      onOpen: vi.fn(),
      onClose: vi.fn(),
      onProtocolError,
    });
    socket.emit("message", { data: JSON.stringify({ kind: "event", data: { bogus: true } }) });
    expect(onProtocolError).toHaveBeenCalledTimes(1);
  });

  it("reports a protocol error for unparseable json", () => {
    const onProtocolError = vi.fn();
    const socket = connect({
      onFrame: vi.fn(),
      onOpen: vi.fn(),
      onClose: vi.fn(),
      onProtocolError,
    });
    socket.emit("message", { data: "{not json" });
    expect(onProtocolError).toHaveBeenCalledTimes(1);
  });

  it("surfaces a close to the caller", () => {
    const onClose = vi.fn();
    const socket = connect({
      onFrame: vi.fn(),
      onOpen: vi.fn(),
      onClose,
      onProtocolError: vi.fn(),
    });
    socket.emit("close", {});
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
