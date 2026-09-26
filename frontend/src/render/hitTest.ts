/**
 * Footprint hit testing.
 *
 * Selection is a click anywhere in a robot's *full* footprint, which is the
 * rectangle the backend's collision engine reserves (`grid[cell_y + h][cell_x +
 * w]`). Because the same camera transform paints and picks, and both read the
 * same anchor-derived top-left corner, a click always lands where the body was
 * drawn. Candidates are tested in reverse draw order, so a robot painted on top
 * wins the click.
 */

import type { AnchorSample } from "./interpolation";
import type { Camera, Point, ScreenRect } from "./camera";
import { pointInRect, worldToScreenX, worldToScreenY } from "./camera";

export interface PickableRobot {
  robotId: string;
  sample: AnchorSample;
  widthCells: number;
  heightCells: number;
  /** Paint order; a higher order is drawn later and therefore on top. */
  order: number;
}

/**
 * The exact rectangle the robot occupies: `width_cells x height_cells` cells,
 * anchored at the top-left corner derived from the robot's anchor cell.
 */
export function rectFor(camera: Camera, robot: PickableRobot): ScreenRect {
  return {
    x: worldToScreenX(camera, robot.sample.left),
    y: worldToScreenY(camera, robot.sample.top),
    width: robot.widthCells * camera.scale,
    height: robot.heightCells * camera.scale,
  };
}

/** The topmost robot whose complete footprint contains the point. */
export function pickRobot(
  robots: readonly PickableRobot[],
  camera: Camera,
  point: Point,
): PickableRobot | null {
  let best: PickableRobot | null = null;
  let bestOrder = -Infinity;
  for (const robot of robots) {
    if (robot.order <= bestOrder) continue;
    if (pointInRect(point, rectFor(camera, robot))) {
      best = robot;
      bestOrder = robot.order;
    }
  }
  return best;
}

/** The world cell address under a screen point, for task placement. */
export function cellUnderPointer(
  camera: Camera,
  point: Point,
  columns: number,
  rows: number,
): { cellX: number; cellY: number } | null {
  const worldX = (point.x - camera.offsetX) / camera.scale;
  const worldY = (point.y - camera.offsetY) / camera.scale;
  if (worldX < 0 || worldY < 0) return null;
  const cellX = Math.floor(worldX);
  const cellY = Math.floor(worldY);
  if (cellX >= columns || cellY >= rows) return null;
  return { cellX, cellY };
}
