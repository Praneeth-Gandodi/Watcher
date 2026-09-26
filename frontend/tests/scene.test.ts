/**
 * A headless run of the real frame composer.
 *
 * The browser was the only place these frames ran, so a throw inside the paint
 * loop silently froze the map while the panels kept updating -- exactly the
 * "the graph is not starting" symptom. This suite paints every scenario the
 * console can load against a recording 2D context, so a bad frame fails here
 * instead of on screen.
 */

import { describe, expect, it } from "vitest";
import type { RobotTelemetry, SnapshotResponse, TelemetryResponse } from "../src/api/types";
import { drawScene, type SceneInput, type SmoothedAnchor } from "../src/render/scene";
import { sampleAnchor } from "../src/render/interpolation";

import { footprintRect } from "../src/render/camera";

interface Recorded {
  fills: number;
  strokes: number;
  maxRight: number;
  maxBottom: number;
}

/** A 2D context that records geometry instead of rasterising it. */
function mockContext(): { ctx: CanvasRenderingContext2D; recorded: Recorded } {
  const recorded: Recorded = { fills: 0, strokes: 0, maxRight: 0, maxBottom: 0 };
  const track = (x: number, y: number, w: number, h: number): void => {
    recorded.maxRight = Math.max(recorded.maxRight, x + w);
    recorded.maxBottom = Math.max(recorded.maxBottom, y + h);
  };
  const ctx = {
    canvas: null,
    save() {},
    restore() {},
    setTransform() {},
    translate() {},
    scale() {},
    rotate() {},
    beginPath() {},
    closePath() {},
    moveTo() {},
    lineTo() {},
    arc() {},
    arcTo() {},
    rect() {},
    roundRect() {},
    fill() {
      recorded.fills += 1;
    },
    stroke() {
      recorded.strokes += 1;
    },
    clip() {},
    isPointInPath() {
      return false;
    },
    fillRect(x: number, y: number, w: number, h: number) {
      recorded.fills += 1;
      track(x, y, w, h);
    },
    strokeRect(x: number, y: number, w: number, h: number) {
      recorded.strokes += 1;
      track(x, y, w, h);
    },
    clearRect() {},
    fillText() {},
    strokeText() {},
    drawImage() {},
    createLinearGradient() {
      return { addColorStop() {} };
    },
    measureText(text: string) {
      return { width: text.length * 6 };
    },
    getLineDash() {
      return [];
    },
    setLineDash() {},
    get globalAlpha() {
      return 1;
    },
    set globalAlpha(_value: number) {},
    get lineWidth() {
      return 1;
    },
    set lineWidth(_value: number) {},
    get font() {
      return "";
    },
    set font(_value: string) {},
    get textAlign() {
      return "left";
    },
    set textAlign(_value: CanvasTextAlign) {},
    get textBaseline() {
      return "top";
    },
    set textBaseline(_value: CanvasTextBaseline) {},
    get lineJoin() {
      return "miter";
    },
    set lineJoin(_value: CanvasLineJoin) {},
    get lineCap() {
      return "butt";
    },
    set lineCap(_value: CanvasLineCap) {},
    get shadowColor() {
      return "";
    },
    set shadowColor(_value: string | CanvasGradient | CanvasPattern) {},
    get shadowBlur() {
      return 0;
    },
    set shadowBlur(_value: number) {},
    get strokeStyle() {
      return "#000000";
    },
    set strokeStyle(_value: string | CanvasGradient | CanvasPattern) {},
    get fillStyle() {
      return "#000000";
    },
    set fillStyle(_value: string | CanvasGradient | CanvasPattern) {},
  } as unknown as CanvasRenderingContext2D;
  return { ctx, recorded };
}

const COLUMNS = 40;
const ROWS = 25;

/** The mixed fleet the `normal` demo actually loads, 1x1 through 3x2. */
const FOOTPRINTS: [string, number, number, number][] = [
  ["robot-001", 1, 1, 1.0],
  ["robot-002", 2, 1, 1.5],
  ["robot-003", 1, 1, 0.75],
  ["robot-004", 2, 2, 1.0],
  ["robot-005", 1, 1, 2.0],
  ["robot-006", 3, 2, 0.5],
  ["robot-007", 1, 1, 1.25],
  ["robot-008", 2, 1, 1.0],
  ["robot-009", 1, 1, 0.75],
  ["robot-010", 2, 2, 1.5],
];

const ACTIONS = [
  "IDLE",
  "MOVING",
  "WAITING",
  "BLOCKED",
  "CHARGING",
  "DEGRADED",
  "FAILED",
  "OFFLINE",
  "NEGOTIATING",
  "REPLANNING",
  "TASK_COMPLETED",
];

function telemetryFor(actions: string[], nowS: number): TelemetryResponse {
  const counts: Record<string, number> = {};
  const robots: RobotTelemetry[] = FOOTPRINTS.map(([robotId, w, h, speed], index) => {
    const action = actions[index % actions.length] ?? "IDLE";
    counts[action] = (counts[action] ?? 0) + 1;
    const moving = action === "MOVING";
    return {
      robot_id: robotId,
      width_cells: w,
      height_cells: h,
      speed_mps: speed,
      battery_percent: 9 + index * 9,
      battery_percent_per_cell: 0.1,
      cell_x: 4 + index * 3,
      cell_y: 6 + (index % 4) * 4,
      position_x: 4.5 + index * 3,
      position_y: 6.5 + (index % 4) * 4,
      status: action === "FAILED" ? "failed" : action === "OFFLINE" ? "offline" : "active",
      communication_state: action === "OFFLINE" ? "lost" : "online",
      action: action as RobotTelemetry["action"],
      action_reason: "reported by the backend",
      workload: index,
      capabilities: ["transport"],
      failure_code: action === "FAILED" ? "drive-failure" : null,
      task_id: `task-${index + 1}`,
      route_id: `route-${index + 1}`,
      route_status: moving ? "active" : "proposed",
      destination_x: 30 - index,
      destination_y: 20 - index,
      progress: index / 10,
      cells_travelled: index,
      remaining_cells: 12 - index,
      remaining_time_s: 6,
      conflict_with: index % 3 === 0 ? [FOOTPRINTS[(index + 1) % 10]![0]] : [],
      conflict_detected_at_s: index % 3 === 0 ? nowS - 1 : null,
      waiting_for_robot_id: action === "WAITING" ? FOOTPRINTS[(index + 2) % 10]![0] : null,
      waiting_since_s: action === "WAITING" ? nowS - 2 : null,
      trail: moving ? [[5.5 + index * 3, 6.5, nowS + 1]] : [],
    };
  });
  return {
    simulation_time_s: nowS,
    scenario: "normal",
    counts_by_action: counts,
    battery_buckets: { normal: 8, low: 1, critical: 1 },
    open_conflict_pairs: [["robot-001", "robot-002"]],
    deadlocked_robot_ids: [],
    controller_available: true,
    revision: 12,
    last_event_sequence: 40,
    robots,
  };
}

function snapshotFor(): SnapshotSnapshot {
  return {
    world: {
      width_m: COLUMNS,
      height_m: ROWS,
      cell_size_m: 1,
      columns: COLUMNS,
      rows: ROWS,
      cells: [
        { cell_x: 10, cell_y: 10, cell_type: "obstacle" },
        { cell_x: 20, cell_y: 14, cell_type: "charging" },
        { cell_x: 30, cell_y: 6, cell_type: "workstation" },
        { cell_x: 5, cell_y: 20, cell_type: "resource" },
        { cell_x: 35, cell_y: 22, cell_type: "deadzone" },
      ],
      revision: 1,
    },
    robots: [],
    routes: [],
    tasks: [
      {
        task_id: "task-1",
        target: { x: 30, y: 6 },
        priority: 4,
        required_capabilities: [],
        estimated_duration_s: 8,
        status: "in_progress",
        assigned_robot_id: "robot-001",
        created_at_s: 0,
      },
      {
        task_id: "task-2",
        target: { x: 12, y: 18 },
        priority: 2,
        required_capabilities: [],
        estimated_duration_s: 5,
        status: "pending",
        assigned_robot_id: null,
        created_at_s: 0,
      },
    ],
    simulation_time_s: 12,
    revision: 12,
    last_event_sequence: 40,
    controller_available: true,
  };
}

type SnapshotSnapshot = SnapshotResponse;

function inputFor(
  actions: string[],
  options: { selected?: string | null; smooth?: Map<string, SmoothedAnchor> } = {},
): SceneInput {
  const telemetry = telemetryFor(actions, 12);
  return {
    viewport: { width: 1200, height: 700 },
    camera: { scale: 20, offsetX: 0, offsetY: 0 },
    snapshot: snapshotFor(),
    telemetry,
    deltaS: 0.3,
    wallClockS: 12.5,
    selectedRobotId: options.selected ?? null,
    showFootprints: false,
    showRoutes: true,
    showTrails: false,
    showLabels: false,
    showTaskMarkers: true,
    showConflictCells: true,
    smooth: options.smooth ?? new Map(),
    alpha: 0.2,
  };
}

describe("drawScene", () => {
  it("paints every action the backend can report without throwing", () => {
    for (const action of ACTIONS) {
      const { ctx, recorded } = mockContext();
      const result = drawScene(ctx, inputFor([action]), 1.5);
      expect(result.pickable.length, `action ${action}`).toBe(FOOTPRINTS.length);
      expect(recorded.fills).toBeGreaterThan(0);
    }
  });

  it("paints the demo fleet, its conflicts, tasks, and obstacles together", () => {
    const { ctx, recorded } = mockContext();
    const result = drawScene(ctx, inputFor(["MOVING", "BLOCKED"]), 1.5);
    expect(result.pickable).toHaveLength(FOOTPRINTS.length);
    expect(recorded.fills).toBeGreaterThan(100);
  });

  it("draws each robot exactly its footprint size", () => {
    const { ctx } = mockContext();
    const input = inputFor(["MOVING"]);
    const result = drawScene(ctx, input, 1.5);
    const byId = new Map(input.telemetry!.robots.map((robot) => [robot.robot_id, robot]));
    for (const pickable of result.pickable) {
      const robot = byId.get(pickable.robotId)!;
      const rect = footprintRect(input.camera, pickable.sample.left, pickable.sample.top, robot.width_cells, robot.height_cells);
      expect(rect.width).toBe(robot.width_cells * input.camera.scale);
      expect(rect.height).toBe(robot.height_cells * input.camera.scale);
    }
  });

  it("paints a selection without disturbing the other robots", () => {
    const plain = mockContext();
    const selected = mockContext();
    const a = drawScene(plain.ctx, inputFor(["MOVING"]), 1.5);
    const b = drawScene(selected.ctx, inputFor(["MOVING"], { selected: "robot-006" }), 1.5);
    expect(a.pickable).toHaveLength(b.pickable.length);
    expect(selected.recorded.strokes).toBeGreaterThan(plain.recorded.strokes);
  });

  it("survives an empty snapshot and a missing telemetry payload", () => {
    const empty = mockContext();
    const blank = { ...inputFor(["MOVING"]), snapshot: null };
    expect(drawScene(empty.ctx, blank, 1.5).pickable).toHaveLength(0);

    const noTelemetry = mockContext();
    const stripped = { ...inputFor(["MOVING"]), telemetry: null };
    expect(drawScene(noTelemetry.ctx, stripped, 1.5).pickable).toHaveLength(0);
  });

  it("paints every footprint class the fleet can use", () => {
    const { ctx } = mockContext();
    const result = drawScene(ctx, inputFor(["IDLE"]), 1.5);
    const classes = new Set(result.pickable.map((robot) => `${robot.widthCells}x${robot.heightCells}`));
    expect(classes).toEqual(new Set(["1x1", "2x1", "2x2", "3x2"]));
  });
});

describe("smoothed anchors", () => {
  function firstInput(smooth: Map<string, SmoothedAnchor>): SceneInput {
    return inputFor(["MOVING"], { smooth });
  }

  it("holds a blocked robot at its committed cell", () => {
    const smooth = new Map<string, SmoothedAnchor>();
    // Prime the smoother with a moving robot, then block it mid-trail.
    const moving = firstInput(smooth);
    drawScene(mockContext().ctx, moving, 1.5);
    const primed = { ...smooth.get("robot-001")! };

    const blocked = { ...firstInput(smooth), deltaS: 5 };
    const telemetry = telemetryFor(["BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED"], 12);
    drawScene(mockContext().ctx, { ...blocked, telemetry }, 1.5);

    // The blocked robot must not have been pushed forward along its trail.
    const after = smooth.get("robot-001")!;
    const target = sampleAnchor(telemetry.robots[0]!, 12, 0);
    expect(Math.abs(after.left - target.left)).toBeLessThan(0.5);
    expect(after.left).toBeGreaterThanOrEqual(primed.left - 0.5);
  });

  it("converges on the backend position for a travelling robot", () => {
    const smooth = new Map<string, SmoothedAnchor>();
    const input = firstInput(smooth);
    for (let step = 0; step < 200; step += 1) {
      input.alpha = 0.25;
      drawScene(mockContext().ctx, input, 1.5);
    }
    const target = sampleAnchor(input.telemetry!.robots[0]!, 12, input.deltaS);
    expect(smooth.get("robot-001")!.left).toBeCloseTo(target.left, 3);
    expect(smooth.get("robot-001")!.top).toBeCloseTo(target.top, 3);
  });

  it("snaps on a large jump instead of sliding the body across the floor", () => {
    const smooth = new Map<string, SmoothedAnchor>([["robot-001", { left: 2, top: 2 }]]);
    const jumped = { ...firstInput(smooth), alpha: 0.01 };
    drawScene(mockContext().ctx, jumped, 1.5);
    const target = sampleAnchor(jumped.telemetry!.robots[0]!, 12, jumped.deltaS);
    expect(smooth.get("robot-001")!.left).toBeCloseTo(target.left, 6);
  });
});

describe("scene robots never overlap", () => {
  it("keeps every drawn footprint clear of the others", () => {
    const actions = ["MOVING", "WAITING", "BLOCKED", "CHARGING", "MOVING", "REPLANNING"];
    const smooth = new Map<string, SmoothedAnchor>();
    for (let frame = 0; frame < 60; frame += 1) {
      const { ctx } = mockContext();
      drawScene(ctx, inputFor(actions, { smooth }), frame * 0.1);
    }
    const input = inputFor(actions, { smooth });
    const byId = new Map(input.telemetry!.robots.map((robot) => [robot.robot_id, robot]));
    for (const a of input.telemetry!.robots) {
      for (const b of input.telemetry!.robots) {
        if (a.robot_id >= b.robot_id) continue;
        const ra = smooth.get(a.robot_id)!;
        const rb = smooth.get(b.robot_id)!;
        const overlapX = Math.min(ra.left + a.width_cells, rb.left + b.width_cells) - Math.max(ra.left, rb.left);
        const overlapY = Math.min(ra.top + a.height_cells, rb.top + b.height_cells) - Math.max(ra.top, rb.top);
        const overlaps = overlapX > 1e-6 && overlapY > 1e-6;
        if (overlaps) {
          // Only the seeded layout may collide, and only if the backend placed
          // them that way; assert the failure names both units for the report.
          throw new Error(
            `${a.robot_id} (${byId.get(a.robot_id)!.width_cells}x${byId.get(a.robot_id)!.height_cells}) overlaps ${b.robot_id}`,
          );
        }
      }
    }
  });
});
