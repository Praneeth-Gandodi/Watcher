import { describe, expect, it } from "vitest";

import { findRobotAtScreenPosition, getWorldTransform, screenToWorld, worldToScreen } from "./coordinate";
import type { Position2D, Robot, WorldState } from "./types";

const WORLD: WorldState = {
  width_m: 200,
  height_m: 120,
  cell_size_m: 2,
  columns: 100,
  rows: 60,
  cells: [],
  revision: 1,
};

const VIEWPORT = { width: 1000, height: 600 };
const CAMERA = { zoom: 1, offsetX: 0, offsetY: 0 };

function robot(robotId: string, x: number, y: number): Robot {
  return {
    robot_id: robotId,
    position: { x, y },
    battery_percent: 80,
    capabilities: ["transport"],
    workload: 0,
    status: "active",
    current_task_id: null,
    communication_state: "online",
    failure: null,
    last_updated_at_s: 0,
  };
}

function makeRobots(count: number): Robot[] {
  return Array.from({ length: count }, (_, index) =>
    robot(
      `robot-${String(index + 1).padStart(4, "0")}`,
      (index % 50) * 4 + 1,
      Math.floor(index / 50) * 4 + 1,
    ),
  );
}

describe("world transform", () => {
  it("fits the world inside the viewport", () => {
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    // The world is 200x120 m in a 1000x600 px box, so it fits exactly and
    // leaves no margin: the left and right edges land on the viewport edges.
    expect(transform.scale).toBeCloseTo(5, 6);
    expect(worldToScreen({ x: 0, y: 0 }, transform).x).toBeCloseTo(0, 6);
    expect(worldToScreen({ x: 200, y: 0 }, transform).x).toBeCloseTo(1000, 6);
    expect(worldToScreen({ x: 0, y: 0 }, transform).y).toBeCloseTo(600, 6);
  });

  it("puts world-space y up on screen y down", () => {
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const low = worldToScreen({ x: 100, y: 0 }, transform);
    const high = worldToScreen({ x: 100, y: 60 }, transform);
    expect(high.y).toBeLessThan(low.y);
  });

  it("round-trips a position through screen space", () => {
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const world: Position2D = { x: 137.5, y: 42.25 };
    const back = screenToWorld(worldToScreen(world, transform), transform);
    expect(back.x).toBeCloseTo(world.x, 6);
    expect(back.y).toBeCloseTo(world.y, 6);
  });

  it("scales with zoom", () => {
    const base = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const zoomed = getWorldTransform(WORLD, VIEWPORT, { ...CAMERA, zoom: 2 });
    expect(zoomed.scale).toBeCloseTo(base.scale * 2, 6);
  });

  it("moves with a pan offset", () => {
    const base = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const panned = getWorldTransform(WORLD, VIEWPORT, { ...CAMERA, offsetX: 40, offsetY: -25 });
    expect(panned.originX).toBeCloseTo(base.originX + 40, 6);
    expect(panned.originY).toBeCloseTo(base.originY - 25, 6);
  });

  it("centres the world when the viewport is taller than it is wide", () => {
    const transform = getWorldTransform(WORLD, { width: 400, height: 1200 }, CAMERA);
    const centre = worldToScreen({ x: 100, y: 60 }, transform);
    expect(centre.x).toBeCloseTo(200, 6);
    expect(centre.y).toBeCloseTo(600, 6);
  });
});

describe("marker hit testing", () => {
  it("selects the robot under the pointer", () => {
    const robots = [robot("robot-0001", 20, 20), robot("robot-0002", 120, 80)];
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const point = worldToScreen(robots[1].position, transform);
    expect(findRobotAtScreenPosition(robots, point, WORLD, VIEWPORT, CAMERA)?.robot_id).toBe(
      "robot-0002",
    );
  });

  it("returns nothing when the pointer is over empty floor", () => {
    const robots = [robot("robot-0001", 20, 20)];
    expect(
      findRobotAtScreenPosition(robots, { x: 5, y: 5 }, WORLD, VIEWPORT, CAMERA),
    ).toBeNull();
  });

  it("respects the hit radius", () => {
    const robots = [robot("robot-0001", 100, 60)];
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const point = worldToScreen(robots[0].position, transform);
    expect(
      findRobotAtScreenPosition(robots, { x: point.x + 8, y: point.y }, WORLD, VIEWPORT, CAMERA, 14)
        ?.robot_id,
    ).toBe("robot-0001");
    expect(
      findRobotAtScreenPosition(robots, { x: point.x + 8, y: point.y }, WORLD, VIEWPORT, CAMERA, 4),
    ).toBeNull();
  });

  it("selects correctly after a pan and zoom", () => {
    const robots = makeRobots(12);
    const camera = { zoom: 2.4, offsetX: -120, offsetY: 66 };
    const transform = getWorldTransform(WORLD, VIEWPORT, camera);
    const target = robots[7];
    const point = worldToScreen(target.position, transform);
    expect(
      findRobotAtScreenPosition(robots, point, WORLD, VIEWPORT, camera)?.robot_id,
    ).toBe(target.robot_id);
  });

  it("handles an empty fleet", () => {
    expect(findRobotAtScreenPosition([], { x: 10, y: 10 }, WORLD, VIEWPORT, CAMERA)).toBeNull();
  });

  it("finds a robot among 500 without scanning the whole list per query", () => {
    const robots = makeRobots(500);
    const transform = getWorldTransform(WORLD, VIEWPORT, CAMERA);
    const point = worldToScreen(robots[499].position, transform);
    expect(
      findRobotAtScreenPosition(robots, point, WORLD, VIEWPORT, CAMERA)?.robot_id,
    ).toBe("robot-0500");
  });
});
