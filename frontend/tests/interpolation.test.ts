/**
 * Trajectory interpolation.
 *
 * The client only smooths motion along the trajectory the backend published; it
 * never invents one. These tests pin that boundary: a projection must land on
 * the published placements, must never pass the last one, and must not move at
 * all when the simulation is paused.
 */

import { describe, expect, it } from "vitest";
import { sampleAnchor, trailHeading } from "../src/render/interpolation";
import type { RobotTelemetry } from "../src/api/types";

function moving(): RobotTelemetry {
  return {
    robot_id: "robot-001",
    width_cells: 2,
    height_cells: 2,
    speed_mps: 1,
    battery_percent: 80,
    battery_percent_per_cell: 0.1,
    cell_x: 4,
    cell_y: 4,
    position_x: 4.5,
    position_y: 4.5,
    status: "active",
    communication_state: "online",
    action: "MOVING",
    action_reason: "",
    workload: 0,
    capabilities: [],
    failure_code: null,
    task_id: "task-1",
    route_id: "route-1",
    route_status: "active",
    destination_x: 7,
    destination_y: 4,
    progress: 0.2,
    cells_travelled: 1,
    remaining_cells: 2,
    remaining_time_s: 2,
    conflict_with: [],
    conflict_detected_at_s: null,
    waiting_for_robot_id: null,
    waiting_since_s: null,
    trail: [
      [5.5, 4.5, 11],
      [6.5, 4.5, 12],
      [7.5, 4.5, 13],
    ],
  };
}

describe("sampleAnchor", () => {
  it("holds the reported position when no time has passed", () => {
    const sample = sampleAnchor(moving(), 10, 0);
    expect(sample.anchorX).toBe(4.5);
    expect(sample.anchorY).toBe(4.5);
    expect(sample.interpolated).toBe(false);
  });

  it("puts the footprint's top-left half a cell before the anchor", () => {
    const sample = sampleAnchor(moving(), 10, 0);
    expect(sample.left).toBe(4);
    expect(sample.top).toBe(4);
  });

  it("interpolates along the published trail at the published times", () => {
    const sample = sampleAnchor(moving(), 10, 0.5);
    expect(sample.anchorX).toBeCloseTo(5, 6);
    expect(sample.interpolated).toBe(true);
    expect(sample.heading).toBe("east");
  });

  it("lands exactly on a published placement", () => {
    const sample = sampleAnchor(moving(), 10, 1);
    expect(sample.anchorX).toBeCloseTo(5.5, 6);
    const second = sampleAnchor(moving(), 10, 2);
    expect(second.anchorX).toBeCloseTo(6.5, 6);
  });

  it("never runs past the last published placement", () => {
    const sample = sampleAnchor(moving(), 10, 99);
    expect(sample.anchorX).toBeCloseTo(7.5, 6);
  });

  it("does not move an idle robot that has no trail", () => {
    const idle: RobotTelemetry = {
      ...moving(),
      action: "IDLE",
      trail: [],
    };
    const sample = sampleAnchor(idle, 10, 5);
    expect(sample.anchorX).toBe(4.5);
    expect(sample.interpolated).toBe(false);
  });

  it("reads the heading from the next placement", () => {
    const northward = moving();
    northward.trail = [
      [4.5, 3.5, 11],
      [4.5, 2.5, 12],
    ];
    expect(sampleAnchor(northward, 10, 0.1).heading).toBe("north");
    expect(trailHeading(northward)).toBe("north");
  });
});
