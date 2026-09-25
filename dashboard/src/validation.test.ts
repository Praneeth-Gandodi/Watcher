import { describe, expect, it } from "vitest";
import { parseEvent, parseEvents, parseSnapshot } from "./validation";

const validSnapshot = {
  simulation_time_s: 2.5,
  revision: 4,
  last_event_sequence: 4,
  controller_available: true,
  world: {
    width_m: 40,
    height_m: 24,
    cell_size_m: 2,
    columns: 20,
    rows: 12,
    cells: [{ cell_x: 2, cell_y: 3, cell_type: "obstacle" }],
    revision: 4,
  },
  robots: [{
    robot_id: "robot-001",
    position: { x: 10, y: 12 },
    battery_percent: 78,
    capabilities: ["transport"],
    workload: 1,
    status: "idle",
    current_task_id: null,
    communication_state: "online",
    failure: null,
    last_updated_at_s: 2.5,
  }],
  tasks: [],
  routes: [],
  conflicts: [],
  metrics: {
    active_robots: 1,
    failed_robots: 0,
    communication_lost_robots: 0,
    pending_tasks: 0,
    completed_tasks: 0,
    open_conflicts: 0,
    detected_deadlocks: 0,
    task_reassignments: 0,
    average_battery_percent: 78,
    average_allocation_latency_ms: 1.2,
    event_throughput_per_s: 3,
    controller_available: true,
    extra_metrics: {},
  },
};

const validEvent = {
  event_id: "event-001",
  sequence: 5,
  schema_version: 1,
  event_type: "TASK_CREATED",
  producer: "runtime",
  correlation_id: "task-001",
  occurred_at_s: 2.5,
  payload: { task_id: "task-001" },
};

describe("runtime contract validation", () => {
  it("loads a canonical snapshot projection", () => {
    const snapshot = parseSnapshot(validSnapshot);
    expect(snapshot.world.cells[0].cell_type).toBe("obstacle");
    expect(snapshot.robots[0].robot_id).toBe("robot-001");
  });

  it.each([
    ["world dimensions", { ...validSnapshot, world: { ...validSnapshot.world, width_m: 0 } }],
    ["snapshot cursor", { ...validSnapshot, last_event_sequence: 1 }],
    ["robot fields", { ...validSnapshot, robots: [{ ...validSnapshot.robots[0], battery_percent: "high" }] }],
    ["route waypoints", { ...validSnapshot, routes: [{ route_id: "route-001", robot_id: "robot-001", task_id: "task-001", waypoints: [{ x: 1, y: 1 }], strategy: "astar", status: "active", version: 1, planned_at_s: 0 }] }],
    ["conflict fields", { ...validSnapshot, conflicts: [{ conflict_id: "conflict-001", kind: "right_of_way", severity: "warning", robot_ids: ["robot-001", "robot-002"], task_ids: [], position: { x: -1, y: 1 }, status: "open", detected_at_s: 0 }] }],
  ])("rejects invalid %s", (_label, invalidSnapshot) => {
    expect(() => parseSnapshot(invalidSnapshot)).toThrow();
  });

  it("validates event envelope, type, schema, and sequence", () => {
    expect(parseEvent(validEvent).event_type).toBe("TASK_CREATED");
    expect(() => parseEvent({ ...validEvent, schema_version: 2 })).toThrow(/schema_version/);
    expect(() => parseEvent({ ...validEvent, sequence: 0 })).toThrow(/sequence/);
    expect(() => parseEvent({ ...validEvent, event_type: "UNKNOWN_EVENT" })).toThrow(/event_type/);
    expect(() => parseEvent({ ...validEvent, payload: [] })).toThrow(/payload/);
    expect(parseEvents([validEvent])).toHaveLength(1);
  });
});
