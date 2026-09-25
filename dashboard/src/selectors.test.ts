import { describe, expect, it } from "vitest";

import {
  activeDeadlocks,
  bidsFromEvents,
  compareRobots,
  eventTone,
  filterRobots,
  filterTasks,
  fleetCounts,
  matchesFilter,
  matchesQuery,
  performanceFacts,
  recoveriesFromEvents,
  robotTone,
  statTiles,
  taskCounts,
  taskTone,
} from "./selectors";
import type { DomainEvent, Robot, SimulationSnapshot, Task } from "./types";

function robot(overrides: Partial<Robot> = {}): Robot {
  return {
    robot_id: "robot-0001",
    position: { x: 10, y: 10 },
    battery_percent: 80,
    capabilities: ["transport"],
    workload: 0,
    status: "idle",
    current_task_id: null,
    communication_state: "online",
    failure: null,
    last_updated_at_s: 0,
    ...overrides,
  };
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    task_id: "task-0001",
    target: { x: 20, y: 20 },
    priority: 3,
    required_capabilities: [],
    estimated_duration_s: 30,
    status: "pending",
    assigned_robot_id: null,
    created_at_s: 0,
    ...overrides,
  };
}

function event(overrides: Partial<DomainEvent> = {}): DomainEvent {
  return {
    event_id: "event-1",
    sequence: 1,
    schema_version: 1,
    event_type: "TASK_ASSIGNED",
    producer: "runtime",
    correlation_id: "task-0001",
    occurred_at_s: 1,
    payload: {},
    ...overrides,
  };
}

function snapshot(overrides: Partial<SimulationSnapshot> = {}): SimulationSnapshot {
  return {
    simulation_time_s: 12,
    revision: 120,
    last_event_sequence: 400,
    controller_available: true,
    world: {
      width_m: 200,
      height_m: 120,
      cell_size_m: 2,
      columns: 100,
      rows: 60,
      cells: [],
      revision: 1,
    },
    robots: [robot({ status: "active" })],
    tasks: [task()],
    routes: [],
    conflicts: [],
    metrics: {
      active_robots: 1,
      failed_robots: 0,
      communication_lost_robots: 0,
      pending_tasks: 1,
      completed_tasks: 3,
      open_conflicts: 0,
      detected_deadlocks: 0,
      task_reassignments: 0,
      average_battery_percent: 80,
      average_allocation_latency_ms: 0.4,
      event_throughput_per_s: 12.5,
      controller_available: true,
      extra_metrics: { fleet_size: 1, average_tick_ms: 3.2, stage_collision_ms: 1.1 },
    },
    ...overrides,
  };
}

describe("tones", () => {
  it("maps robot status to a tone", () => {
    expect(robotTone(robot({ status: "active" }))).toBe("ok");
    expect(robotTone(robot({ status: "failed" }))).toBe("crit");
    expect(robotTone(robot({ status: "blocked" }))).toBe("warn");
    expect(robotTone(robot({ status: "charging" }))).toBe("info");
    expect(robotTone(robot({ status: "idle" }))).toBe("idle");
  });

  it("escalates a reachable but flat robot to a warning", () => {
    expect(robotTone(robot({ status: "active", battery_percent: 12 }))).toBe("warn");
  });

  it("escalates a lost robot to a warning", () => {
    expect(robotTone(robot({ status: "active", communication_state: "lost" }))).toBe("warn");
  });

  it("maps task status to a tone", () => {
    expect(taskTone(task({ status: "completed" }))).toBe("ok");
    expect(taskTone(task({ status: "blocked" }))).toBe("warn");
  });

  it("marks a robot failure and a deadlock as critical events", () => {
    expect(eventTone(event({ event_type: "ROBOT_FAILED" }))).toBe("crit");
    expect(eventTone(event({ event_type: "DEADLOCK_DETECTED" }))).toBe("crit");
  });

  it("marks routine coordination as informational", () => {
    expect(eventTone(event({ event_type: "BID_SUBMITTED" }))).toBe("info");
  });
});

describe("counts", () => {
  it("counts every robot state", () => {
    const counts = fleetCounts([
      robot({ status: "active" }),
      robot({ status: "idle" }),
      robot({ status: "failed" }),
      robot({ status: "charging" }),
      robot({ battery_percent: 10 }),
      robot({ communication_state: "lost" }),
    ]);
    expect(counts.total).toBe(6);
    expect(counts.active).toBe(1);
    // Three robots keep the default idle status: the explicit one, the
    // low-battery one, and the unreachable one.
    expect(counts.idle).toBe(3);
    expect(counts.failed).toBe(1);
    expect(counts.charging).toBe(1);
    expect(counts.lowBattery).toBe(1);
    expect(counts.unreachable).toBe(1);
  });

  it("counts task states", () => {
    const counts = taskCounts([
      task({ status: "pending" }),
      task({ status: "in_progress" }),
      task({ status: "completed" }),
      task({ status: "recovery" }),
    ]);
    expect(counts.total).toBe(4);
    expect(counts.pending).toBe(1);
    expect(counts.active).toBe(1);
    expect(counts.completed).toBe(1);
    expect(counts.recovery).toBe(1);
  });
});

describe("stat tiles", () => {
  it("shows a dash rather than a number before the first snapshot", () => {
    for (const tile of statTiles(null)) {
      expect(tile.value).toBe("—");
      expect(tile.detail).toBe("Awaiting snapshot");
    }
  });

  it("reads its figures from the snapshot metrics", () => {
    const tiles = statTiles(snapshot());
    const fleet = tiles.find((tile) => tile.key === "fleet");
    // The headline number is the fleet size, with the working split as detail.
    expect(fleet?.value).toBe("1");
    expect(fleet?.detail).toBe("1 active · 0 idle");
    const throughput = tiles.find((tile) => tile.key === "throughput");
    expect(throughput?.value).toBe("13");
  });

  it("flags a fleet that has lost robots", () => {
    const tiles = statTiles(
      snapshot({
        metrics: { ...snapshot().metrics, failed_robots: 2, average_battery_percent: 40 },
      }),
    );
    expect(tiles.find((tile) => tile.key === "fleet")?.tone).toBe("crit");
  });

  it("warns when the queue is far deeper than the working fleet", () => {
    const tiles = statTiles(
      snapshot({
        metrics: { ...snapshot().metrics, pending_tasks: 400 },
      }),
    );
    expect(tiles.find((tile) => tile.key === "work")?.tone).toBe("warn");
  });
});

describe("performance facts", () => {
  it("returns nulls before a snapshot rather than zeroes", () => {
    const facts = performanceFacts(null);
    expect(facts.routeEfficiency).toBeNull();
    expect(facts.tickLatencyMs).toBeNull();
  });

  it("reads the free-form telemetry map", () => {
    const facts = performanceFacts(snapshot());
    expect(facts.tickLatencyMs).toBeCloseTo(3.2);
  });

  it("orders stage costs from most to least expensive", () => {
    const facts = performanceFacts(snapshot());
    expect(facts.stageCosts.length).toBeGreaterThan(0);
    expect(facts.stageCosts[0].ms).toBeGreaterThanOrEqual(
      facts.stageCosts[facts.stageCosts.length - 1].ms,
    );
  });
});

describe("roster filtering", () => {
  const robots = [
    robot({ robot_id: "robot-0001", status: "active" }),
    robot({ robot_id: "robot-0002", status: "failed" }),
    robot({ robot_id: "robot-0003", status: "charging", battery_percent: 44 }),
    robot({ robot_id: "robot-0004", status: "active", battery_percent: 8 }),
    robot({ robot_id: "robot-0005", capabilities: ["inspect"], current_task_id: "task-0009" }),
  ];

  it("returns everything for the all filter", () => {
    expect(filterRobots(robots, { text: "", filter: "all", sort: "id" })).toHaveLength(5);
  });

  it("filters by active", () => {
    const active = filterRobots(robots, { text: "", filter: "active", sort: "id" });
    expect(active.map((item) => item.robot_id)).toEqual(["robot-0001", "robot-0004"]);
  });

  it("filters to robots needing attention", () => {
    const attention = filterRobots(robots, { text: "", filter: "attention", sort: "id" });
    expect(attention.map((item) => item.robot_id)).toEqual(["robot-0002"]);
  });

  it("filters by charging", () => {
    expect(filterRobots(robots, { text: "", filter: "charging", sort: "id" })).toHaveLength(1);
  });

  it("filters by low battery", () => {
    expect(
      filterRobots(robots, { text: "", filter: "low", sort: "id" }).map((item) => item.robot_id),
    ).toEqual(["robot-0004"]);
  });

  it("matches on identifier", () => {
    expect(filterRobots(robots, { text: "0003", filter: "all", sort: "id" })).toHaveLength(1);
  });

  it("matches on capability", () => {
    expect(filterRobots(robots, { text: "inspect", filter: "all", sort: "id" })).toHaveLength(1);
  });

  it("matches on assigned task", () => {
    expect(filterRobots(robots, { text: "task-0009", filter: "all", sort: "id" })).toHaveLength(1);
  });

  it("is case insensitive", () => {
    expect(matchesQuery(robot({ robot_id: "robot-0042" }), "ROBOT-0042")).toBe(true);
    expect(matchesQuery(robot({ robot_id: "robot-0042" }), "0042")).toBe(true);
  });

  it("returns nothing for a query that matches no robot", () => {
    expect(filterRobots(robots, { text: "zzz", filter: "all", sort: "id" })).toHaveLength(0);
  });

  it("combines a text query with a state filter", () => {
    expect(filterRobots(robots, { text: "robot-000", filter: "active", sort: "id" })).toHaveLength(2);
  });

  it("sorts by battery ascending", () => {
    const sorted = [...robots].sort(compareRobots("battery"));
    expect(sorted[0].battery_percent).toBe(8);
  });

  it("sorts failures ahead of healthy robots", () => {
    const sorted = [...robots].sort(compareRobots("status"));
    expect(sorted[0].status).toBe("failed");
  });

  it("sorts by workload descending", () => {
    const sorted = [robot({ workload: 1 }), robot({ workload: 5 })].sort(compareRobots("workload"));
    expect(sorted[0].workload).toBe(5);
  });

  it("treats the all filter as a match for everything", () => {
    expect(matchesFilter(robot({ status: "failed" }), "all")).toBe(true);
  });
});

describe("task filtering", () => {
  const tasks = [
    task({ task_id: "task-0001", assigned_robot_id: "robot-0001" }),
    task({ task_id: "task-0002", assigned_robot_id: null }),
  ];

  it("matches on task identifier", () => {
    expect(filterTasks(tasks, "0002")).toHaveLength(1);
  });

  it("matches on assigned robot", () => {
    expect(filterTasks(tasks, "robot-0001")).toHaveLength(1);
  });

  it("returns everything for an empty query", () => {
    expect(filterTasks(tasks, "")).toHaveLength(2);
  });
});

describe("event-derived views", () => {
  it("reads bid records out of the event log newest first", () => {
    const events = [
      event({
        event_id: "e1",
        sequence: 1,
        event_type: "BID_SUBMITTED",
        payload: {
          bid: {
            bid_id: "bid-1",
            robot_id: "robot-0001",
            task_id: "task-0001",
            total_cost: 42,
            distance_cost: 10,
            battery_cost: 20,
            workload_cost: 12,
            estimated_completion_time_s: 55,
          },
        },
      }),
    ];
    const bids = bidsFromEvents(events);
    expect(bids).toHaveLength(1);
    expect(bids[0].totalCost).toBe(42);
    expect(bids[0].robotId).toBe("robot-0001");
  });

  it("ignores events that are not bids", () => {
    expect(bidsFromEvents([event({ event_type: "TASK_ASSIGNED" })])).toHaveLength(0);
  });

  it("skips a bid event with no payload rather than throwing", () => {
    expect(
      bidsFromEvents([event({ event_type: "BID_SUBMITTED", payload: {} })]),
    ).toHaveLength(0);
  });

  it("caps the number of bid records", () => {
    const many = Array.from({ length: 50 }, (_, index) =>
      event({ event_id: `e${index}`, sequence: index + 1, event_type: "BID_SUBMITTED", payload: { bid: { bid_id: `b${index}` } } }),
    );
    expect(bidsFromEvents(many, 10)).toHaveLength(10);
  });

  it("reads recovery records out of the event log", () => {
    const events = [
      event({
        event_type: "RECOVERY_STARTED",
        payload: {
          action: {
            action_id: "recovery-1",
            action_type: "yield",
            target_robot_ids: ["robot-0002"],
            affected_task_ids: ["task-0001"],
            reason: "robot-0001 holds right of way",
          },
        },
      }),
    ];
    const records = recoveriesFromEvents(events);
    expect(records[0].actionType).toBe("yield");
    expect(records[0].targetRobotIds).toEqual(["robot-0002"]);
  });
});

describe("deadlock tracking", () => {
  const detection = event({
    event_id: "d1",
    sequence: 10,
    event_type: "DEADLOCK_DETECTED",
    payload: {
      report: {
        deadlock_id: "deadlock-001",
        cycle_robot_ids: ["robot-0001", "robot-0002"],
        blocked_task_ids: ["task-0001"],
        confidence: 0.8,
      },
    },
  });

  it("reports a detected cycle as active", () => {
    const cycles = activeDeadlocks([detection]);
    expect(cycles).toHaveLength(1);
    expect(cycles[0].robotIds).toEqual(["robot-0001", "robot-0002"]);
  });

  it("clears a cycle once a recovery targets one of its robots", () => {
    const recovery = event({
      event_id: "r1",
      sequence: 11,
      event_type: "RECOVERY_STARTED",
      payload: { action: { action_id: "a", target_robot_ids: ["robot-0002"], affected_task_ids: [] } },
    });
    expect(activeDeadlocks([detection, recovery])).toHaveLength(0);
  });

  it("clears a cycle once one of its tasks completes", () => {
    const completion = event({
      event_id: "c1",
      sequence: 11,
      event_type: "TASK_COMPLETED",
      payload: { task_id: "task-0001", robot_id: "robot-0001" },
    });
    expect(activeDeadlocks([detection, completion])).toHaveLength(0);
  });

  it("keeps a cycle whose recovery targets something else", () => {
    const recovery = event({
      event_id: "r2",
      sequence: 11,
      event_type: "RECOVERY_STARTED",
      payload: { action: { action_id: "a", target_robot_ids: ["robot-0099"], affected_task_ids: [] } },
    });
    expect(activeDeadlocks([detection, recovery])).toHaveLength(1);
  });

  it("reports nothing before any detection", () => {
    expect(activeDeadlocks([])).toHaveLength(0);
  });
});
