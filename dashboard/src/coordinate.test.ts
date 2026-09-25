import { describe, expect, it } from "vitest";
import { findRobotAtScreenPosition, getWorldTransform, screenToWorld, worldToScreen } from "./coordinate";
import type { Robot, WorldState } from "./types";

const world: WorldState = {
  width_m: 40,
  height_m: 24,
  cell_size_m: 2,
  columns: 20,
  rows: 12,
  cells: [],
  revision: 1,
};

const viewport = { width: 800, height: 480 };

describe("world coordinate conversion", () => {
  it("keeps world y increasing upward on the canvas", () => {
    const transform = getWorldTransform(world, viewport, { zoom: 1, offsetX: 0, offsetY: 0 });
    const bottom = worldToScreen({ x: 0, y: 0 }, transform);
    const top = worldToScreen({ x: 0, y: 24 }, transform);

    expect(top.y).toBeLessThan(bottom.y);
    expect(bottom.y).toBeCloseTo(480);
    expect(top.y).toBeCloseTo(0);
  });

  it("round trips a world point through screen space", () => {
    const transform = getWorldTransform(world, viewport, { zoom: 1.7, offsetX: 32, offsetY: -18 });
    const point = { x: 17.25, y: 9.5 };
    const roundTrip = screenToWorld(worldToScreen(point, transform), transform);

    expect(roundTrip.x).toBeCloseTo(point.x);
    expect(roundTrip.y).toBeCloseTo(point.y);
  });

  it("centers the world when camera offsets are zero", () => {
    const transform = getWorldTransform(world, viewport, { zoom: 1, offsetX: 0, offsetY: 0 });
    const center = worldToScreen({ x: 20, y: 12 }, transform);

    expect(center.x).toBeCloseTo(400);
    expect(center.y).toBeCloseTo(240);
  });

  it("selects the robot under the pointer", () => {
    const robot: Robot = {
      robot_id: "robot-001",
      position: { x: 20, y: 12 },
      battery_percent: 80,
      capabilities: ["transport"],
      workload: 0,
      status: "idle",
      current_task_id: null,
      communication_state: "online",
      failure: null,
      last_updated_at_s: 0,
    };
    const camera = { zoom: 1, offsetX: 0, offsetY: 0 };
    const transform = getWorldTransform(world, viewport, camera);
    expect(findRobotAtScreenPosition([robot], worldToScreen(robot.position, transform), world, viewport, camera)?.robot_id).toBe("robot-001");
    expect(findRobotAtScreenPosition([robot], { x: 10, y: 10 }, world, viewport, camera)).toBeNull();
  });
});
