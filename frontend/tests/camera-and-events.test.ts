/**
 * Camera transform and the event-log fold.
 *
 * The camera tests guard the property the UI depends on: painting and picking
 * must use the same transform, and zooming must keep the pixel under the cursor
 * fixed. The event tests guard the cursor contract: a poll must never duplicate
 * a line, and a scenario load must reset the fold rather than mix two runs.
 */

import { describe, expect, it } from "vitest";
import {
  centreOn,
  clamp,
  fitWorld,
  panBy,
  screenToWorldX,
  screenToWorldY,
  worldToScreenX,
  worldToScreenY,
  zoomAt,
} from "../src/render/camera";
import {
  mergeNegotiations,
  robotIdsOf,
  taskIdOf,
  type EventRow,
} from "../src/state/useEventLog";

describe("camera", () => {
  const camera = fitWorld(40, 25, { width: 800, height: 500 }, 0);

  it("round-trips world to screen", () => {
    const screenX = worldToScreenX(camera, 12.25);
    expect(screenToWorldX(camera, screenX)).toBeCloseTo(12.25, 6);
    const screenY = worldToScreenY(camera, 7.75);
    expect(screenToWorldY(camera, screenY)).toBeCloseTo(7.75, 6);
  });

  it("keeps the pixel under the cursor fixed while zooming", () => {
    const zoomed = zoomAt(camera, 320, 180, 1.5, { width: 800, height: 500 }, 40, 25);
    const before = { x: screenToWorldX(camera, 320), y: screenToWorldY(camera, 180) };
    const after = { x: screenToWorldX(zoomed, 320), y: screenToWorldY(zoomed, 180) };
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  it("clamps the zoom range", () => {
    let current = camera;
    for (let step = 0; step < 40; step += 1) {
      current = zoomAt(current, 400, 250, 1.2, { width: 800, height: 500 }, 40, 25);
    }
    expect(current.scale).toBe(48);
    for (let step = 0; step < 80; step += 1) {
      current = zoomAt(current, 400, 250, 1 / 1.2, { width: 800, height: 500 }, 40, 25);
    }
    expect(current.scale).toBe(4);
  });

  it("keeps the world reachable when panning", () => {
    const panned = panBy(camera, 5000, 5000, { width: 800, height: 500 }, 40, 25);
    expect(screenToWorldX(panned, 0)).toBeGreaterThan(-40);
    expect(screenToWorldX(panned, 0)).toBeLessThanOrEqual(40);
    expect(screenToWorldY(panned, 0)).toBeGreaterThan(-25);
  });

  it("follows a small drag exactly", () => {
    const panned = panBy(camera, 12, -7, { width: 800, height: 500 }, 40, 25);
    expect(panned.offsetX).toBe(camera.offsetX + 12);
    expect(panned.offsetY).toBe(camera.offsetY - 7);
  });

  it("centres on a world position", () => {
    const centred = centreOn(camera, 20, 12, { width: 800, height: 500 });
    expect(worldToScreenX(centred, 20)).toBe(400);
    expect(worldToScreenY(centred, 12)).toBe(250);
  });

  it("clamps values", () => {
    expect(clamp(5, 0, 3)).toBe(3);
    expect(clamp(-5, 0, 3)).toBe(0);
  });
});

function event(
  sequence: number,
  eventType: string,
  payload: Record<string, unknown>,
  occurredAtS = sequence,
): EventRow {
  return {
    sequence,
    eventType,
    producer: "agent1",
    occurredAtS,
    correlationId: `corr-${sequence}`,
    payload,
  };
}

describe("negotiation fold", () => {
  /** A `BID_SUBMITTED` payload, nested exactly as the contract defines it. */
  function bidEvent(
    sequence: number,
    taskId: string,
    robotId: string,
    totalCost: number,
    occurredAtS = sequence,
  ): EventRow {
    return event(
      sequence,
      "BID_SUBMITTED",
      {
        bid: {
          bid_id: `bid-${sequence}`,
          robot_id: robotId,
          task_id: taskId,
          total_cost: totalCost,
          distance_cost: totalCost * 0.6,
          battery_cost: totalCost * 0.2,
          workload_cost: totalCost * 0.2,
          estimated_completion_time_s: 12,
          created_at_s: occurredAtS,
          valid_until_s: occurredAtS + 5,
        },
      },
      occurredAtS,
    );
  }

  it("collects bids per task and reads the nested bid payload", () => {
    let records = mergeNegotiations(
      [],
      [bidEvent(1, "task-1", "robot-002", 9.5), bidEvent(2, "task-1", "robot-001", 4.25)],
    );
    expect(records).toHaveLength(1);
    expect(records[0]!.taskId).toBe("task-1");
    expect(records[0]!.assignedRobotId).toBeNull();
    records = mergeNegotiations(records, []);
    const costs = records[0]!.bids.map((bid) => bid.totalCost).sort((a, b) => a - b);
    expect(costs).toEqual([4.25, 9.5]);
    expect(records[0]!.bids[0]!.completionS).toBe(12);
  });

  it("records the winning assignment from the nested assignment", () => {
    const records = mergeNegotiations(
      [],
      [
        bidEvent(1, "task-2", "robot-001", 3),
        event(2, "TASK_ASSIGNED", {
          assignment: { task_id: "task-2", robot_id: "robot-001", bid_id: "bid-1" },
        }),
      ],
    );
    expect(records[0]!.assignedRobotId).toBe("robot-001");
    expect(records[0]!.status).toBe("assigned");
  });

  it("follows a reassignment to the new owner", () => {
    const records = mergeNegotiations(
      mergeNegotiations(
        [],
        [bidEvent(1, "task-3", "robot-001", 3), event(2, "TASK_ASSIGNED", { assignment: { task_id: "task-3", robot_id: "robot-001" } })],
      ),
      [
        event(3, "TASK_REASSIGNED", {
          task_id: "task-3",
          previous_robot_id: "robot-001",
          new_robot_id: "robot-004",
          reason: "robot failed",
        }),
      ],
    );
    expect(records[0]!.assignedRobotId).toBe("robot-004");
    expect(records[0]!.status).toBe("reassigned");
  });

  it("ignores events with no task reference", () => {
    const records = mergeNegotiations(
      [],
      [
        event(1, "BATTERY_LOW", { robot_id: "robot-001", battery_percent: 9 }),
        event(2, "CONFLICT_DETECTED", { conflict: { robot_ids: ["robot-001", "robot-002"] } }),
      ],
    );
    expect(records).toHaveLength(0);
  });

  it("does not duplicate a bid that arrives twice at the same sequence", () => {
    const first = mergeNegotiations([], [bidEvent(1, "task-4", "robot-001", 3)]);
    const second = mergeNegotiations(first, [bidEvent(1, "task-4", "robot-001", 3)]);
    expect(second[0]!.bids).toHaveLength(1);
  });

  it("keeps the most recently updated task first", () => {
    const records = mergeNegotiations(
      [],
      [bidEvent(1, "task-old", "robot-001", 1, 1), bidEvent(2, "task-new", "robot-001", 1, 9)],
    );
    expect(records[0]!.taskId).toBe("task-new");
  });
});

describe("payload readers", () => {
  it("finds the robot a conflict names", () => {
    const payload = { conflict: { robot_ids: ["robot-003", "robot-004"] } };
    expect(robotIdsOf(payload)).toEqual(["robot-003", "robot-004"]);
  });

  it("finds the robots a recovery action targets", () => {
    const payload = { action: { target_robot_ids: ["robot-002"], action_type: "yield" } };
    expect(robotIdsOf(payload)).toContain("robot-002");
  });

  it("finds the task inside a bid or an assignment", () => {
    expect(taskIdOf({ bid: { task_id: "task-9" } })).toBe("task-9");
    expect(taskIdOf({ assignment: { task_id: "task-8" } })).toBe("task-8");
    expect(taskIdOf({ route: { task_id: "task-7" } })).toBe("task-7");
    expect(taskIdOf({ note: "nothing" })).toBeNull();
  });
});
