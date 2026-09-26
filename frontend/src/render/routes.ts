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

  // Every path is a solid, continuous line. Dashes were used to distinguish
  // states, but with the dim travelled line and the trail overlay also drawn,
  // three overlapping strokes per robot read as one broken line rather than as
  // information. State is carried by colour and width alone, and a held robot
  // gets a stop bar at the cell it is waiting on instead.
  if (done) {
    return { color: PALETTE.routeDone, width: 1.5, dash: [], alpha: 0.6 };
  }
  if (blocked) {
    return { color: PALETTE.conflict, width: 2.25, dash: [], alpha: 0.85 };
  }
  if (waiting) {
    return { color: PALETTE.warning, width: 2, dash: [], alpha: 0.8 };
  }
  if (replanning) {
    return { color: PALETTE.selected, width: 2, dash: [], alpha: 0.9 };
  }
  return { color: PALETTE.routeActive, width: 1.75, dash: [], alpha: 0.5 };
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

/**
 * The trail, but as one unbroken run from the body.
 *
 * The backend caps how many placements it publishes, so a long route arrives as
 * a finite window of points. The window always continues the route, but if the
 * first point is far from the body -- which happens when the body is mid-hold
 * and the trail is stale -- a straight connector would cut across the floor and
 * read as a broken line. In that case the path stops at the first point that is
 * actually adjacent, so the line is always truthful about where the machine is
 * going next.
 */
function continuousFrom(
  from: { x: number; y: number },
  trail: { x: number; y: number }[],
): { x: number; y: number }[] {
  const points = [from];
  for (const point of trail) {
    const previous = points[points.length - 1]!;
    const gap = Math.hypot(point.x - previous.x, point.y - previous.y);
    // A hop of more than one cell diagonally is a stale trail, not a step.
    if (points.length > 1 && gap > 1.75) break;
    if (points.length === 1 && gap > 2.5) break;
    points.push(point);
  }
  return points.length > 1 ? points : [from, ...trail.slice(0, 1)];
}

/** A bar across a cell, marking a robot that is being held on it. */
function drawStopBar(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  cellX: number,
  cellY: number,
  color: string,
): void {
  const { x, y, size } = cellToPixel(geometry, cellX, cellY);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(x + size * 0.28, y + size * 0.5);
  ctx.lineTo(x + size * 0.72, y + size * 0.5);
  ctx.stroke();
  ctx.restore();
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

/** The centre of a drawn footprint corner, in pixels. */
function anchorCentre(
  geometry: GridGeometry,
  anchor: { left: number; top: number },
): { x: number; y: number } {
  return {
    x: geometry.originX + (anchor.left + 0.5) * geometry.cellPixels,
    y: geometry.originY + (anchor.top + 0.5) * geometry.cellPixels,
  };
}

export function drawRobotRoute(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  robot: RobotTelemetry,
  /** The corner the body is actually drawn at, so the line starts on the body. */
  anchor: { left: number; top: number },
): void {
  const style = routeStyleFor(robot);
  const from = anchorCentre(geometry, anchor);

  const trail = trailPoints(geometry, robot);
  if (trail.length > 0) {
    // One continuous line from the body through the backend's remaining timed
    // placements. It starts on the machine, so a hold or a replan can never
    // leave it trailing off from the body.
    const continuous = continuousFrom(from, trail);
    strokePath(ctx, continuous, { ...style, alpha: Math.min(1, style.alpha + 0.2) });
    const last = continuous[continuous.length - 1]!;
    // Destination marker: a reticle rather than a dot, so it reads as a goal.
    ctx.save();
    ctx.strokeStyle = style.color;
    ctx.lineWidth = 1.5;
    const arm = Math.max(2, geometry.cellPixels * 0.22);
    ctx.beginPath();
    ctx.moveTo(last.x - arm, last.y);
    ctx.lineTo(last.x + arm, last.y);
    ctx.moveTo(last.x, last.y - arm);
    ctx.lineTo(last.x, last.y + arm);
    ctx.stroke();
    ctx.restore();

    if (robot.action === "BLOCKED" || robot.action === "WAITING") {
      // A held robot gets a stop bar across the cell it is waiting on, which
      // says "held here" without breaking the line.
      drawStopBar(ctx, geometry, robot.cell_x, robot.cell_y, style.color);
    }
    return;
  }

  if (robot.destination_x !== null && robot.destination_y !== null) {
    // No timed trail: fall back to the destination the robot reports.
    const goal = cellToPixel(geometry, robot.destination_x, robot.destination_y);
    strokePath(
      ctx,
      [from, { x: goal.x + goal.size / 2, y: goal.y + goal.size / 2 }],
      style,
    );
  }
}

/**
 * Dim the already-travelled part of a route.
 *
 * Only drawn when the snapshot's route still belongs to the robot's current
 * position. After a replan or a retreat the route describes a different path,
 * and drawing it would put a stray line on the floor that the robot is not
 * following -- which is what made a route look broken mid-recovery.
 */
export function drawTravelledRoute(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  route: RoutePlan | undefined,
  robot: RobotTelemetry,
  anchor: { left: number; top: number },
): void {
  if (!route || route.waypoints.length < 2) return;
  const first = route.waypoints[0]!;
  const startsHere =
    Math.floor(first.x) === robot.cell_x && Math.floor(first.y) === robot.cell_y;
  if (!startsHere) return;
  const from = anchorCentre(geometry, anchor);
  const travelled = robot.cells_travelled;
  if (travelled <= 0) return;
  const limit = Math.min(route.waypoints.length, travelled + 1);
  const points = [from];
  for (let index = 1; index < limit; index += 1) {
    const waypoint = route.waypoints[index]!;
    const cell = cellToPixel(geometry, Math.floor(waypoint.x), Math.floor(waypoint.y));
    points.push({ x: cell.x + cell.size / 2, y: cell.y + cell.size / 2 });
  }
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
