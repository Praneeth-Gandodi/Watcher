/**
 * Camera: the world-to-screen transform for the 2D map.
 *
 * The world is measured in grid cells (1 cell == 1 m, per the backend's
 * `cell_size_m`), so the camera works in cell units and applies zoom, pan, and
 * a follow-selected mode on top. A single transform is shared by the renderer
 * and the hit test, which guarantees a click lands exactly where a robot was
 * painted.
 */

export interface Viewport {
  width: number;
  height: number;
}

export interface Camera {
  /** Device pixels per cell. */
  scale: number;
  offsetX: number;
  offsetY: number;
}

export interface Point {
  x: number;
  y: number;
}

export const MIN_SCALE = 4;
export const MAX_SCALE = 48;

export function createCamera(): Camera {
  return { scale: 16, offsetX: 0, offsetY: 0 };
}

/**
 * Fit a whole world into the viewport, leaving a small margin, and return the
 * camera plus the resulting base cell size.
 */
export function fitWorld(
  columns: number,
  rows: number,
  viewport: Viewport,
  margin = 16,
): Camera {
  const usableWidth = Math.max(1, viewport.width - margin * 2);
  const usableHeight = Math.max(1, viewport.height - margin * 2);
  const scale = clamp(
    Math.min(usableWidth / columns, usableHeight / rows),
    MIN_SCALE,
    MAX_SCALE,
  );
  const worldWidth = columns * scale;
  const worldHeight = rows * scale;
  return {
    scale,
    offsetX: Math.floor((viewport.width - worldWidth) / 2),
    offsetY: Math.floor((viewport.height - worldHeight) / 2),
  };
}

export function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

export function worldToScreenX(camera: Camera, worldX: number): number {
  return worldX * camera.scale + camera.offsetX;
}

export function worldToScreenY(camera: Camera, worldY: number): number {
  return worldY * camera.scale + camera.offsetY;
}

export function screenToWorldX(camera: Camera, screenX: number): number {
  return (screenX - camera.offsetX) / camera.scale;
}

export function screenToWorldY(camera: Camera, screenY: number): number {
  return (screenY - camera.offsetY) / camera.scale;
}

export interface ScreenRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The screen rectangle of a robot footprint anchored at a world position. */
export function footprintRect(
  camera: Camera,
  worldX: number,
  worldY: number,
  widthCells: number,
  heightCells: number,
): ScreenRect {
  return {
    x: worldToScreenX(camera, worldX),
    y: worldToScreenY(camera, worldY),
    width: widthCells * camera.scale,
    height: heightCells * camera.scale,
  };
}

/**
 * Hit test against a rectangle.
 *
 * The rectangle is half-open, matching the cell lattice: with a cell size of
 * 10, the cells `x = 2` and `x = 3` of a 2-cell body cover pixels `[20, 40)`,
 * and the point at exactly `40` belongs to the neighbouring cell.
 */
export function pointInRect(point: Point, rect: ScreenRect): boolean {
  return (
    point.x >= rect.x &&
    point.x < rect.x + rect.width &&
    point.y >= rect.y &&
    point.y < rect.y + rect.height
  );
}

/** Zoom around a fixed screen point, so the pixel under the cursor stays put. */
export function zoomAt(
  camera: Camera,
  screenX: number,
  screenY: number,
  factor: number,
  viewport: Viewport,
  columns: number,
  rows: number,
): Camera {
  const next = clamp(camera.scale * factor, MIN_SCALE, MAX_SCALE);
  if (next === camera.scale) return camera;
  const worldX = screenToWorldX(camera, screenX);
  const worldY = screenToWorldY(camera, screenY);
  const zoomed: Camera = {
    scale: next,
    offsetX: screenX - worldX * next,
    offsetY: screenY - worldY * next,
  };
  const limits = panLimits(zoomed, viewport, columns, rows);
  return { scale: next, offsetX: limits.offsetX, offsetY: limits.offsetY };
}

/** Pan by a screen delta, clamped so the world can never be lost off screen. */
export function panBy(
  camera: Camera,
  dx: number,
  dy: number,
  viewport: Viewport,
  columns: number,
  rows: number,
): Camera {
  const moved: Camera = {
    scale: camera.scale,
    offsetX: camera.offsetX + dx,
    offsetY: camera.offsetY + dy,
  };
  const limits = panLimits(moved, viewport, columns, rows);
  return { scale: moved.scale, offsetX: limits.offsetX, offsetY: limits.offsetY };
}

/** Clamp the pan so the world can never be dragged completely off screen. */
export function panLimits(
  camera: Camera,
  viewport: Viewport,
  columns: number,
  rows: number,
): { offsetX: number; offsetY: number } {
  const worldWidth = columns * camera.scale;
  const worldHeight = rows * camera.scale;
  const slackX = Math.min(viewport.width, worldWidth) * 0.5;
  const slackY = Math.min(viewport.height, worldHeight) * 0.5;
  return {
    offsetX: clamp(camera.offsetX, slackX - viewport.width, viewport.width - slackX),
    offsetY: clamp(camera.offsetY, slackY - viewport.height, viewport.height - slackY),
  };
}

/** Centre the camera on a world position, used by follow-selected. */
export function centreOn(
  camera: Camera,
  worldX: number,
  worldY: number,
  viewport: Viewport,
): Camera {
  return {
    ...camera,
    offsetX: viewport.width / 2 - worldX * camera.scale,
    offsetY: viewport.height / 2 - worldY * camera.scale,
  };
}

/** The visible world window, for culling at large fleet sizes. */
export function visibleBounds(
  camera: Camera,
  viewport: Viewport,
): { minX: number; minY: number; maxX: number; maxY: number } {
  return {
    minX: screenToWorldX(camera, 0),
    minY: screenToWorldY(camera, 0),
    maxX: screenToWorldX(camera, viewport.width),
    maxY: screenToWorldY(camera, viewport.height),
  };
}
