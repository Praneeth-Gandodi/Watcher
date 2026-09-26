/**
 * Route rendering.
 *
 * Draws each active robot's planned path from its current cell to its
 * destination, plus the remaining timed trail the backend supplies. The
 * travelled part is dimmed and the remaining part is bright, so progress along
 * a route is readable at a glance without a separate chart.
 *
 * A shared route is not special-cased: the path is simply the route the
 * backend planned, and two robots using the same corridor are drawn on top of
 * each other, which is exactly what happens in the simulation.
 */

import { PALETTE } from "../styles/palette";
import type { RoutePlan, RobotTelemetry } from "../api/types";
import { cellToPixel, type GridGeometry } from "./grid";

export interface RouteStyle {
  color: string;
  width: number;
  dash: number[];
  alpha: number;
}

export function routeStyleFor(robot: RobotTelemetry): RouteStyle {
  const done = robot.action === "TASK_COMPLETED" || robot.route_status === "completed";
  const waiting = robot.action === "WAITING";
  const blocked = robot.action === "BLOCKED";
  const replanning = robot.action === "REPLANNING";

  if (done) {
    return { color: PALETTE.routeDone, width: 1.5, dash: [], alpha: 0.7 };
  }
  if (blocked) {
    return {
      color: PALETTE.conflict,
      width: 2,
      dash: [4, 3],
      alpha: 0.9,
    };
  }
  if (waiting) {
    return { color: PALETTE.warning, width: 1.75, dash: [6, 4], alpha: 0.85 };
  }
  if (replanning) {
    return { color: PALETTE.selected, width: 2, dash: [2, 3], alpha: 0.95 };
  }
  return { color: PALETTE.routeActive, width: 1.5, dash: [], alpha: 0.55 };
}

/** The remaining timed trail the backend supplied, as pixel points. */
export function trailPoints(
  geometry: GridGeometry,
  robot: RobotTelemetry,
): { x: number; y: number }[] {
  const points: { x: number; y: number }[] = [];
  for (const [xMetres, yMetres] of robot.trail) {
    const cell = cellToPixel(geometry, Math.floor(xMetres), Math.floor(yMetres));
    points.push({ x: cell.x + cell.size / 2, y: cell.y + cell.size / 2 });
  }
  return points;
}

function strokePath(
  ctx: CanvasRenderingContext2D,
  points: { x: number; y: number }[],
  style: RouteStyle,
): void {
  if (points.length < 2) return;
  ctx.save();
  ctx.globalAlpha = style.alpha;
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  if (style.dash.length > 0) ctx.setLineDash(style.dash);
  ctx.beginPath();
  ctx.moveTo(points[0]!.x, points[0]!.y);
  for (let index = 1; index < points.length; index += 1) {
    const point = points[index]!;
    ctx.lineTo(point.x, point.y);
  }
  ctx.stroke();
  ctx.restore();
}

export function drawRobotRoute(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  robot: RobotTelemetry,
): void {
  const style = routeStyleFor(robot);
  const origin = cellToPixel(geometry, robot.cell_x, robot.cell_y);
  const from = { x: origin.x + origin.size / 2, y: origin.y + origin.size / 2 };

  const trail = trailPoints(geometry, robot);
  if (trail.length > 0) {
    // Remaining movement: bright, following the backend's timed placements.
    strokePath(ctx, [from, ...trail], { ...style, alpha: Math.min(1, style.alpha + 0.25) });
    const last = trail[trail.length - 1]!;
    ctx.save();
    ctx.fillStyle = style.color;
    ctx.beginPath();
    ctx.arc(last.x, last.y, Math.max(1.5, geometry.cellPixels * 0.09), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  } else if (robot.destination_x !== null && robot.destination_y !== null) {
    // No timed trail: fall back to the route the snapshot reports.
    const goal = cellToPixel(geometry, robot.destination_x, robot.destination_y);
    strokePath(
      ctx,
      [from, { x: goal.x + goal.size / 2, y: goal.y + goal.size / 2 }],
      style,
    );
  }
}

/** Dim the already-travelled part of a route, from the snapshot's waypoints. */
export function drawTravelledRoute(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  route: RoutePlan | undefined,
  travelledCells: number,
): void {
  if (!route || route.waypoints.length < 2 || travelledCells <= 0) return;
  const limit = Math.min(route.waypoints.length, travelledCells + 1);
  const points = route.waypoints.slice(0, limit).map((waypoint) => {
    const cell = cellToPixel(geometry, Math.floor(waypoint.x), Math.floor(waypoint.y));
    return { x: cell.x + cell.size / 2, y: cell.y + cell.size / 2 };
  });
  strokePath(ctx, points, {
    color: PALETTE.trail,
    width: 1.5,
    dash: [],
    alpha: 0.35,
  });
}

/** Destination marker: a hollow reticle on the task cell. */
export function drawDestination(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  cellX: number,
  cellY: number,
  color: string,
): void {
  const { x, y, size } = cellToPixel(geometry, cellX, cellY);
  const pad = Math.max(1, Math.round(size * 0.16));
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([3, 2]);
  ctx.strokeRect(x + pad + 0.5, y + pad + 0.5, size - pad * 2 - 1, size - pad * 2 - 1);
  ctx.setLineDash([]);
  ctx.fillStyle = color;
  ctx.fillRect(x + size / 2 - 1, y + size / 2 - 1, 2, 2);
  ctx.restore();
}
