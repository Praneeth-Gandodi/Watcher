/**
 * Footprint geometry and hit testing.
 *
 * These are the rules the whole map depends on: a robot occupies exactly its
 * `width_cells x height_cells` rectangle, anchored at the top-left cell the
 * backend reports, and a click anywhere inside that rectangle selects it.
 */

import { describe, expect, it } from "vitest";
import type { Camera } from "../src/render/camera";
import { footprintRect, fitWorld, pointInRect, worldToScreenX } from "../src/render/camera";
import { cellUnderPointer, pickRobot, rectFor, type PickableRobot } from "../src/render/hitTest";
import { sampleAnchor } from "../src/render/interpolation";
import type { RobotTelemetry } from "../src/api/types";

function telemetry(overrides: Partial<RobotTelemetry> = {}): RobotTelemetry {
  return {
    robot_id: "robot-001",
    width_cells: 1,
    height_cells: 1,
    speed_mps: 1,
    battery_percent: 90,
    battery_percent_per_cell: 0.1,
    cell_x: 5,
    cell_y: 5,
    position_x: 5.5,
    position_y: 5.5,
    status: "active",
    communication_state: "online",
    action: "MOVING",
    action_reason: "",
    workload: 0,
    capabilities: ["transport"],
    failure_code: null,
    task_id: null,
    route_id: null,
    route_status: null,
    destination_x: null,
    destination_y: null,
    progress: 0,
    cells_travelled: 0,
    remaining_cells: 0,
    remaining_time_s: 0,
    conflict_with: [],
    conflict_detected_at_s: null,
    waiting_for_robot_id: null,
    waiting_since_s: null,
    trail: [],
    ...overrides,
  };
}

function robot(id: string, cellX: number, cellY: number, w: number, h: number, order: number): PickableRobot {
  return {
    robotId: id,
    sample: sampleAnchor(
      telemetry({ robot_id: id, cell_x: cellX, cell_y: cellY, position_x: cellX + 0.5, position_y: cellY + 0.5 }),
      0,
      0,
    ),
    widthCells: w,
    heightCells: h,
    order,
  };
}

const CAMERA: Camera = { scale: 10, offsetX: 0, offsetY: 0 };

describe("footprint rect", () => {
  it("is exactly width_cells x height_cells cells", () => {
    const rect = footprintRect(CAMERA, 10, 4, 3, 2);
    expect(rect.width).toBe(30);
    expect(rect.height).toBe(20);
  });

  it("scales width and height independently", () => {
    const wide = footprintRect(CAMERA, 0, 0, 4, 1);
    const tall = footprintRect(CAMERA, 0, 0, 1, 4);
    expect(wide.width / wide.height).toBe(4);
    expect(tall.height / tall.width).toBe(4);
  });

  it("anchors the top-left corner at the anchor cell, not the centre", () => {
    // The backend anchors a footprint at (cell_x, cell_y) and occupies
    // grid[cell_y + h][cell_x + w]; the published position is the centre of the
    // anchor cell, so the rectangle starts half a cell before it.
    const rect = rectFor(CAMERA, robot("robot-001", 5, 5, 2, 2, 0));
    expect(rect.x).toBe(50);
    expect(rect.y).toBe(50);
    expect(rect.width).toBe(20);
    expect(rect.height).toBe(20);
  });
});

describe("hit testing", () => {
  const robots = [
    robot("robot-001", 2, 2, 2, 2, 0),
    robot("robot-002", 10, 10, 1, 1, 1),
    robot("robot-003", 20, 6, 3, 1, 2),
  ];

  it("selects on the top-left corner pixel of the footprint", () => {
    const hit = pickRobot(robots, CAMERA, { x: 20, y: 20 });
    expect(hit?.robotId).toBe("robot-001");
  });

  it("selects on the bottom-right corner pixel of the footprint", () => {
    const hit = pickRobot(robots, CAMERA, { x: 39.9, y: 39.9 });
    expect(hit?.robotId).toBe("robot-001");
  });

  it("selects anywhere inside a wide footprint, not just the centre", () => {
    // Far right of robot-003's 3x1 body, well away from its centre cell.
    const hit = pickRobot(robots, CAMERA, { x: 228, y: 65 });
    expect(hit?.robotId).toBe("robot-003");
  });

  it("misses just outside the footprint", () => {
    // The rectangle is half-open, so exactly 40 is the neighbouring cell.
    expect(pickRobot(robots, CAMERA, { x: 40, y: 20 })).toBeNull();
    expect(pickRobot(robots, CAMERA, { x: 39.9, y: 40 })).toBeNull();
  });

  it("misses the row below a one-cell body", () => {
    expect(pickRobot(robots, CAMERA, { x: 105, y: 120 })).toBeNull();
  });

  it("gives the topmost robot the click when bodies overlap", () => {
    const lower = robot("robot-lower", 5, 5, 2, 2, 0);
    const upper = robot("robot-upper", 5, 5, 2, 2, 1);
    expect(pickRobot([lower, upper], CAMERA, { x: 55, y: 55 })?.robotId).toBe("robot-upper");
    expect(pickRobot([upper, lower], CAMERA, { x: 55, y: 55 })?.robotId).toBe("robot-upper");
  });

  it("respects the camera transform for pan and zoom", () => {
    const camera: Camera = { scale: 20, offsetX: -100, offsetY: 40 };
    const moved = robot("robot-001", 2, 2, 2, 2, 0);
    const rect = rectFor(camera, moved);
    expect(pointInRect({ x: rect.x + 1, y: rect.y + 1 }, rect)).toBe(true);
    expect(pickRobot([moved], camera, { x: rect.x + 1, y: rect.y + 1 })?.robotId).toBe("robot-001");
  });
});

describe("cell picking", () => {
  it("maps a point to the cell under it", () => {
    expect(cellUnderPointer(CAMERA, { x: 25, y: 35 }, 40, 25)).toEqual({ cellX: 2, cellY: 3 });
  });

  it("returns null outside the world", () => {
    expect(cellUnderPointer(CAMERA, { x: 900, y: 35 }, 40, 25)).toBeNull();
  });
});

describe("world fit", () => {
  it("centres the world and keeps whole cells", () => {
    const camera = fitWorld(40, 25, { width: 800, height: 500 }, 0);
    expect(camera.scale).toBe(20);
    expect(worldToScreenX(camera, 40)).toBe(800);
  });
});
