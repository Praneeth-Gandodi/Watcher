/**
 * The frame composer.
 *
 * One pass per animation frame, in a fixed order, so the scene reads the same
 * way every time: floor, facilities, routes, destinations, tasks, conflict
 * cells, robots, then the world HUD. Everything is canvas-drawn, so a
 * 500-robot fleet costs one draw loop instead of 500 DOM nodes.
 */

import { PALETTE, priorityColor } from "../styles/palette";
import type {
  RoutePlan,
  RobotTelemetry,
  SnapshotResponse,
  TelemetryResponse,
  Task,
} from "../api/types";
import { drawSelectionBrackets } from "../assets/ui/glyphs";
import {
  classFor,
  drawFootprintGrid,
  drawRobot,
  type RobotVisual,
} from "../assets/robots/sprite";
import type { Camera, Viewport } from "./camera";
import { visibleBounds } from "./camera";
import {
  drawConflictCells,
  drawFloor,
  drawTaskMarkers,
  drawWorld,
  type GridGeometry,
} from "./grid";
import { drawDestination, drawRobotRoute, drawTravelledRoute } from "./routes";
import { sampleAnchor, type AnchorSample } from "./interpolation";
import type { PickableRobot } from "./hitTest";
import { rectFor } from "./hitTest";

export interface SceneInput {
  viewport: Viewport;
  camera: Camera;
  snapshot: SnapshotResponse | null;
  telemetry: TelemetryResponse | null;
  /** Simulated seconds elapsed since the telemetry sample, for interpolation. */
  deltaS: number;
  wallClockS: number;
  selectedRobotId: string | null;
  showFootprints: boolean;
  showRoutes: boolean;
  showTrails: boolean;
  showLabels: boolean;
  showTaskMarkers: boolean;
  showConflictCells: boolean;
  /**
   * Smoothed footprint corners per robot, in world cells. The renderer updates
   * this in place, so the drawn body eases toward the backend's position
   * instead of snapping to it once per poll.
   */
  smooth: Map<string, SmoothedAnchor>;
  /** Frame-rate-independent smoothing factor for this frame, 0..1. */
  alpha: number;
}

export interface SmoothedAnchor {
  left: number;
  top: number;
}

/** A jump bigger than this is a real discontinuity (a scenario load), not jitter. */
const SNAP_CELLS = 3;

export interface SceneResult {
  pickable: PickableRobot[];
  drawnRobots: number;
}

function visualFor(
  robot: RobotTelemetry,
  sample: AnchorSample,
  moving: boolean,
  selected: boolean,
): RobotVisual {
  return {
    action: robot.action,
    status: robot.status,
    communication: robot.communication_state,
    batteryPercent: robot.battery_percent,
    heading: sample.heading,
    moving,
    selected,
    dim: robot.status === "failed" || robot.status === "offline",
    showFootprint: false,
    showLabel: false,
    time: 0,
    robotClass: classFor(robot.width_cells, robot.height_cells),
    widthCells: robot.width_cells,
    heightCells: robot.height_cells,
  };
}

function taskColor(task: Task): string {
  if (task.status === "completed") return PALETTE.routeDone;
  if (task.status === "cancelled") return PALETTE.textFaint;
  return priorityColor(task.priority);
}

export function drawScene(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  time: number,
): SceneResult {
  const { viewport, camera, snapshot } = input;
  if (!snapshot) {
    ctx.fillStyle = PALETTE.background;
    ctx.fillRect(0, 0, viewport.width, viewport.height);
    drawCenteredMessage(ctx, viewport, "AWAITING BACKEND SNAPSHOT", PALETTE.textDim);
    return { pickable: [], drawnRobots: 0 };
  }

  const world = snapshot.world;
  const geometry: GridGeometry = {
    cellPixels: camera.scale,
    originX: camera.offsetX,
    originY: camera.offsetY,
    columns: world.columns,
    rows: world.rows,
  };

  drawFloor(ctx, geometry, viewport.width, viewport.height);
  drawWorld(ctx, world, geometry, time);
  if (input.showConflictCells) drawConflictOverlay(ctx, input, geometry, time);
  if (input.showTrails) drawTrailOverlay(ctx, input, geometry);
  if (input.showRoutes) drawRouteOverlay(ctx, input, geometry);
  drawDestinationOverlay(ctx, input, geometry);
  if (input.showTaskMarkers) drawTasks(ctx, geometry, snapshot.tasks, time);

  const pickable = drawRobots(ctx, input, geometry, viewport, time);
  drawWorldHud(ctx, geometry, snapshot);

  return { pickable, drawnRobots: pickable.length };
}

function drawConflictOverlay(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  geometry: GridGeometry,
  time: number,
): void {
  const robots = input.telemetry?.robots ?? [];
  const byId = new Map(robots.map((robot) => [robot.robot_id, robot]));
  const seen = new Set<string>();
  const cells: { cellX: number; cellY: number }[] = [];
  for (const robot of robots) {
    for (const otherId of robot.conflict_with) {
      const other = byId.get(otherId);
      if (!other) continue;
      const key = [robot.robot_id, otherId].sort().join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      cells.push({ cellX: robot.cell_x, cellY: robot.cell_y });
      cells.push({ cellX: other.cell_x, cellY: other.cell_y });
    }
  }
  drawConflictCells(ctx, geometry, cells, time);
}

function drawTrailOverlay(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  geometry: GridGeometry,
): void {
  for (const robot of input.telemetry?.robots ?? []) {
    if (robot.trail.length < 1) continue;
    if (robot.status === "failed" || robot.status === "offline") continue;
    const head = {
      x: geometry.originX + robot.position_x * geometry.cellPixels,
      y: geometry.originY + robot.position_y * geometry.cellPixels,
    };
    ctx.save();
    ctx.globalAlpha = 0.32;
    ctx.strokeStyle = PALETTE.trail;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(head.x, head.y);
    for (const [x, y] of robot.trail) {
      ctx.lineTo(
        geometry.originX + x * geometry.cellPixels,
        geometry.originY + y * geometry.cellPixels,
      );
    }
    ctx.stroke();
    ctx.restore();
  }
}

function drawRouteOverlay(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  geometry: GridGeometry,
): void {
  const snapshot = input.snapshot;
  if (!snapshot) return;
  const routesById = new Map<string, RoutePlan>(
    snapshot.routes.map((route) => [route.route_id, route]),
  );
  for (const robot of input.telemetry?.robots ?? []) {
    if (robot.route_id && robot.trail.length === 0) {
      drawTravelledRoute(
        ctx,
        geometry,
        routesById.get(robot.route_id),
        robot.cells_travelled,
      );
    }
    drawRobotRoute(ctx, geometry, robot);
  }
}

function drawDestinationOverlay(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  geometry: GridGeometry,
): void {
  for (const robot of input.telemetry?.robots ?? []) {
    if (robot.destination_x === null || robot.destination_y === null) continue;
    if (robot.route_status === "completed") continue;
    drawDestination(
      ctx,
      geometry,
      robot.destination_x,
      robot.destination_y,
      robot.action === "BLOCKED" ? PALETTE.conflict : PALETTE.routeActive,
    );
  }
}

function drawTasks(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  tasks: Task[],
  time: number,
): void {
  const markers = tasks
    .filter((task) => task.status !== "cancelled")
    .map((task) => ({
      taskId: task.task_id,
      cellX: Math.floor(task.target.x),
      cellY: Math.floor(task.target.y),
      color: taskColor(task),
      assigned: task.assigned_robot_id !== null && task.status !== "pending",
    }));
  drawTaskMarkers(ctx, geometry, markers, time);
}

/**
 * Whether the backend says this robot is actually moving.
 *
 * Only a travelling robot is pushed forward between polls. Everything else --
 * blocked, waiting, negotiating, charging, degraded, failed, offline, idle --
 * holds its committed cell, because that is where the simulation actually put
 * it.
 */
function isTravelling(robot: RobotTelemetry): boolean {
  return robot.action === "MOVING" || robot.action === "REPLANNING";
}

/**
 * Ease the drawn body toward the backend's reported footprint corner.
 *
 * The target is always the backend's own position; this only decides how the
 * body gets there between polls. A large jump means the simulation genuinely
 * moved (a scenario load, a reset, or a reassignment), so it snaps instead of
 * sliding the body across the floor.
 */
function easeToward(
  input: SceneInput,
  robotId: string,
  sample: AnchorSample,
  travelling: boolean,
): SmoothedAnchor {
  const previous = input.smooth.get(robotId);
  if (previous === undefined) {
    const created = { left: sample.left, top: sample.top };
    input.smooth.set(robotId, created);
    return created;
  }
  const jumpX = Math.abs(sample.left - previous.left);
  const jumpY = Math.abs(sample.top - previous.top);
  if (jumpX > SNAP_CELLS || jumpY > SNAP_CELLS) {
    previous.left = sample.left;
    previous.top = sample.top;
    return previous;
  }
  // A body that has just been held should arrive at its committed cell without
  // a long glide, so the hold reads as a stop rather than as a slow drift.
  const alpha = travelling ? input.alpha : Math.min(1, input.alpha * 3);
  previous.left += (sample.left - previous.left) * alpha;
  previous.top += (sample.top - previous.top) * alpha;
  return previous;
}

function drawRobots(
  ctx: CanvasRenderingContext2D,
  input: SceneInput,
  geometry: GridGeometry,
  viewport: Viewport,
  time: number,
): PickableRobot[] {  const robots = input.telemetry?.robots ?? [];
  const nowS = input.telemetry?.simulation_time_s ?? 0;
  const bounds = visibleBounds(input.camera, viewport);
  const pickable: PickableRobot[] = [];

  robots.forEach((robot, index) => {
    const travelling = isTravelling(robot);
    // A robot the backend is holding stays exactly where it committed to. Only
    // a travelling one is projected forward along its published trail, which is
    // what stops a yielded robot from being drawn straight through the robot it
    // yielded to.
    const projectedS = travelling ? input.deltaS : 0;
    const sample = sampleAnchor(robot, nowS, projectedS);
    const anchor = easeToward(input, robot.robot_id, sample, travelling);
    const anchorX = geometry.originX + anchor.left * geometry.cellPixels;
    const anchorY = geometry.originY + anchor.top * geometry.cellPixels;
    const width = robot.width_cells * geometry.cellPixels;
    const height = robot.height_cells * geometry.cellPixels;

    // Cull anything fully outside the viewport before touching the canvas.
    const slack = 2;
    if (
      sample.left + robot.width_cells < bounds.minX - slack ||
      sample.left > bounds.maxX + slack ||
      sample.top + robot.height_cells < bounds.minY - slack ||
      sample.top > bounds.maxY + slack
    ) {
      return;
    }

    const selected = robot.robot_id === input.selectedRobotId;
    const rect = { x: anchorX, y: anchorY, width, height };
    const visual = visualFor(
      robot,
      sample,
      robot.action === "MOVING" || robot.action === "REPLANNING",
      selected,
    );
    drawRobot(ctx, rect, { ...visual, time });

    if (input.showFootprints) {
      drawFootprintGrid(ctx, rect, {
        width: geometry.cellPixels,
        height: geometry.cellPixels,
      });
    }
    if (input.showLabels) drawRobotLabel(ctx, robot, rect);
    pickable.push({
      robotId: robot.robot_id,
      sample: { ...sample, left: anchor.left, top: anchor.top },
      widthCells: robot.width_cells,
      heightCells: robot.height_cells,
      order: index,
    });
  });

  for (const entry of pickable) {
    if (entry.robotId !== input.selectedRobotId) continue;
    const rect = rectFor(input.camera, entry);
    drawSelectionBrackets(
      ctx,
      rect.x - 2,
      rect.y - 2,
      rect.width + 4,
      rect.height + 4,
      PALETTE.selected,
    );
  }
  return pickable;
}

function drawRobotLabel(
  ctx: CanvasRenderingContext2D,
  robot: RobotTelemetry,
  rect: { x: number; y: number; width: number; height: number },
): void {
  const cx = rect.x + rect.width / 2;
  const baseline = Math.max(9, rect.y - 3);
  const text = robot.robot_id.replace("robot-", "R");
  ctx.save();
  ctx.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  const metrics = ctx.measureText(text);
  ctx.fillStyle = "rgba(11,15,14,0.8)";
  ctx.fillRect(cx - metrics.width / 2 - 2, baseline - 10, metrics.width + 4, 10);
  ctx.fillStyle = PALETTE.textDim;
  ctx.fillText(text, cx, baseline);
  ctx.restore();
}

function drawWorldHud(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  snapshot: SnapshotResponse,
): void {
  const label = `${snapshot.world.columns}x${snapshot.world.rows} GRID // ${snapshot.robots.length} UNITS`;
  ctx.save();
  ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillStyle = PALETTE.textFaint;
  ctx.fillText(label, geometry.originX, Math.max(2, geometry.originY - 16));
  ctx.restore();
}

function drawCenteredMessage(
  ctx: CanvasRenderingContext2D,
  viewport: Viewport,
  message: string,
  color: string,
): void {
  ctx.save();
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = color;
  ctx.fillText(message, viewport.width / 2, viewport.height / 2);
  ctx.restore();
}
