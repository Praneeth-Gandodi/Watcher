/**
 * Grid and facility rendering for the 2D map.
 *
 * Draws the warehouse floor: cell grid, obstacles with an industrial top face,
 * charging pads, workstations, resource pallets, dead zones, and task crates.
 * Everything is drawn once per frame on the canvas, so a 500-robot fleet costs
 * the same as a 10-robot fleet.
 */

import { PALETTE } from "../styles/palette";
import type { GridCell, WorldState } from "../api/types";
import {
  drawCharger,
  drawConflictHatch,
  drawCrate,
  drawDeadzone,
  drawResource,
  drawWorkstation,
  type Pixel,
} from "../assets/ui/glyphs";

export interface GridGeometry {
  cellPixels: number;
  originX: number;
  originY: number;
  columns: number;
  rows: number;
}

export function geometryFor(
  world: Pick<WorldState, "columns" | "rows">,
  width: number,
  height: number,
): GridGeometry {
  const rawCell = Math.floor(
    Math.min(width / world.columns, height / world.rows),
  );
  // Never let the floor fall below a readable size, even on a small window.
  const cellPixels = Math.max(4, rawCell);
  return {
    cellPixels,
    originX: Math.floor((width - cellPixels * world.columns) / 2),
    originY: Math.floor((height - cellPixels * world.rows) / 2),
    columns: world.columns,
    rows: world.rows,
  };
}

/** Cell address -> top-left device pixel. */
export function cellToPixel(
  geometry: GridGeometry,
  cellX: number,
  cellY: number,
): { x: number; y: number; size: number } {
  return {
    x: geometry.originX + cellX * geometry.cellPixels,
    y: geometry.originY + cellY * geometry.cellPixels,
    size: geometry.cellPixels,
  };
}

/** World metres -> top-left device pixel of the cell that contains it. */
export function worldToPixel(
  geometry: GridGeometry,
  xMetres: number,
  yMetres: number,
): { x: number; y: number; size: number } {
  const cellX = Math.floor(xMetres);
  const cellY = Math.floor(yMetres);
  return cellToPixel(geometry, cellX, cellY);
}

export function drawFloor(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  width: number,
  height: number,
): void {
  ctx.fillStyle = PALETTE.background;
  ctx.fillRect(0, 0, width, height);

  const { cellPixels, originX, originY, columns, rows } = geometry;
  const mapWidth = columns * cellPixels;
  const mapHeight = rows * cellPixels;

  // Chequered floor: a very low-contrast weave so the warehouse reads as a
  // surface without competing with the robots.
  ctx.fillStyle = PALETTE.floor;
  ctx.fillRect(originX, originY, mapWidth, mapHeight);
  ctx.fillStyle = PALETTE.floorAlt;
  for (let row = 0; row < rows; row += 1) {
    for (let column = row % 2; column < columns; column += 2) {
      ctx.fillRect(
        originX + column * cellPixels,
        originY + row * cellPixels,
        cellPixels,
        cellPixels,
      );
    }
  }

  // Cell grid, with a heavier line every five cells for scale reference.
  ctx.strokeStyle = PALETTE.grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let column = 0; column <= columns; column += 1) {
    const gx = Math.round(originX + column * cellPixels) + 0.5;
    ctx.moveTo(gx, originY);
    ctx.lineTo(gx, originY + mapHeight);
  }
  for (let row = 0; row <= rows; row += 1) {
    const gy = Math.round(originY + row * cellPixels) + 0.5;
    ctx.moveTo(originX, gy);
    ctx.lineTo(originX + mapWidth, gy);
  }
  ctx.stroke();

  ctx.strokeStyle = PALETTE.gridMajor;
  ctx.beginPath();
  for (let column = 0; column <= columns; column += 5) {
    const gx = Math.round(originX + column * cellPixels) + 0.5;
    ctx.moveTo(gx, originY);
    ctx.lineTo(gx, originY + mapHeight);
  }
  for (let row = 0; row <= rows; row += 5) {
    const gy = Math.round(originY + row * cellPixels) + 0.5;
    ctx.moveTo(originX, gy);
    ctx.lineTo(originX + mapWidth, gy);
  }
  ctx.stroke();

  // Perimeter frame.
  ctx.strokeStyle = PALETTE.gridMajor;
  ctx.lineWidth = 2;
  ctx.strokeRect(originX - 1, originY - 1, mapWidth + 2, mapHeight + 2);
}

function drawObstacle(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  time: number,
): void {
  ctx.fillStyle = PALETTE.obstacle;
  ctx.fillRect(x, y, size, size);
  // Lit top edge: a racking unit, not a flat square.
  ctx.fillStyle = PALETTE.obstacleTop;
  ctx.fillRect(x, y, size, Math.max(1, Math.round(size * 0.26)));
  // Rivets.
  ctx.fillStyle = PALETTE.obstacleEdge;
  const rivet = Math.max(1, Math.round(size * 0.09));
  ctx.fillRect(x + rivet, y + Math.round(size * 0.08), rivet, rivet);
  ctx.fillRect(x + size - rivet * 2, y + Math.round(size * 0.08), rivet, rivet);
  // Subtle scan shading.
  ctx.fillStyle = "rgba(0,0,0,0.18)";
  const shift = Math.floor(time * 2) % 2;
  ctx.fillRect(x, y + Math.round(size * 0.5) + shift, size, 1);
  ctx.strokeStyle = PALETTE.obstacleEdge;
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
}

export function drawWorld(
  ctx: CanvasRenderingContext2D,
  world: WorldState,
  geometry: GridGeometry,
  time: number,
): void {
  for (const cell of world.cells) {
    const { x, y, size } = cellToPixel(geometry, cell.cell_x, cell.cell_y);
    drawCell(ctx, cell, { x, y, size }, time);
  }
}

function drawCell(
  ctx: CanvasRenderingContext2D,
  cell: GridCell,
  pixel: Pixel,
  time: number,
): void {
  switch (cell.cell_type) {
    case "obstacle":
      drawObstacle(ctx, pixel.x, pixel.y, pixel.size, time);
      return;
    case "charging":
      drawCharger(ctx, pixel);
      return;
    case "workstation":
      drawWorkstation(ctx, pixel);
      return;
    case "resource":
      drawResource(ctx, pixel);
      return;
    case "deadzone":
      drawDeadzone(ctx, pixel);
      return;
    default:
      return;
  }
}

export interface TaskMarker {
  taskId: string;
  cellX: number;
  cellY: number;
  color: string;
  assigned: boolean;
}

export function drawTaskMarkers(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  markers: TaskMarker[],
  time: number,
): void {
  for (const marker of markers) {
    const { x, y, size } = cellToPixel(geometry, marker.cellX, marker.cellY);
    drawCrate(
      ctx,
      { x, y, size: Math.round(size * 0.86) },
      marker.color,
      marker.assigned ? time : 0,
    );
  }
}

export function drawConflictCells(
  ctx: CanvasRenderingContext2D,
  geometry: GridGeometry,
  cells: { cellX: number; cellY: number }[],
  time: number,
): void {
  for (const cell of cells) {
    const { x, y, size } = cellToPixel(geometry, cell.cellX, cell.cellY);
    drawConflictHatch(ctx, x, y, size, time);
  }
}
