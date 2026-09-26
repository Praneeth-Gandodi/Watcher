/**
 * Pixel-art glyphs for world features and state markers.
 *
 * Drawn on the canvas from rectangles and paths, so there is no external image
 * to load and no emoji in the UI. Everything snaps to the pixel lattice for a
 * consistent low-resolution look.
 */

import { PALETTE } from "../../styles/palette";

export interface Pixel {
  x: number;
  y: number;
  size: number;
}

/** A sealed cargo crate: the task marker. */
export function drawCrate(
  ctx: CanvasRenderingContext2D,
  pixel: Pixel,
  color: string,
  time: number,
): void {
  const { x, y, size } = pixel;
  const inset = Math.max(1, Math.round(size * 0.16));
  const body = size - inset * 2;

  ctx.save();
  // Drop shadow keeps the marker readable over a route line.
  ctx.fillStyle = "rgba(0,0,0,0.45)";
  ctx.fillRect(x + inset, y + inset + 1, body, body);

  ctx.fillStyle = color;
  ctx.fillRect(x + inset, y + inset, body, body);

  // Cross-brace, the classic crate read.
  ctx.strokeStyle = "rgba(0,0,0,0.55)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x + inset + 0.5, y + inset + 0.5);
  ctx.lineTo(x + inset + body - 0.5, y + inset + body - 0.5);
  ctx.moveTo(x + inset + body - 0.5, y + inset + 0.5);
  ctx.lineTo(x + inset + 0.5, y + inset + body - 0.5);
  ctx.stroke();

  // Assigned pulse: a slow breathing outline.
  ctx.globalAlpha = 0.35 + 0.35 * Math.abs(Math.sin(time * 2));
  ctx.strokeStyle = color;
  ctx.strokeRect(x + inset - 1.5, y + inset - 1.5, body + 3, body + 3);
  ctx.restore();
}

/** A charging pad: bolt between two rails. */
export function drawCharger(
  ctx: CanvasRenderingContext2D,
  pixel: Pixel,
): void {
  const { x, y, size } = pixel;
  const inset = Math.max(1, Math.round(size * 0.2));
  ctx.save();
  ctx.strokeStyle = PALETTE.charging;
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  ctx.strokeRect(x + inset + 0.5, y + inset + 0.5, size - inset * 2 - 1, size - inset * 2 - 1);
  ctx.globalAlpha = 1;
  ctx.fillStyle = PALETTE.charging;
  const cx = x + size / 2;
  const cy = y + size / 2;
  const s = Math.max(2, size * 0.22);
  ctx.beginPath();
  ctx.moveTo(cx + s * 0.3, cy - s);
  ctx.lineTo(cx - s * 0.5, cy + s * 0.15);
  ctx.lineTo(cx - s * 0.1, cy + s * 0.15);
  ctx.lineTo(cx - s * 0.3, cy + s);
  ctx.lineTo(cx + s * 0.55, cy - s * 0.2);
  ctx.lineTo(cx + s * 0.1, cy - s * 0.2);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** A workstation: a console with a screen face. */
export function drawWorkstation(
  ctx: CanvasRenderingContext2D,
  pixel: Pixel,
): void {
  const { x, y, size } = pixel;
  const inset = Math.max(1, Math.round(size * 0.18));
  ctx.save();
  ctx.fillStyle = "#1A2320";
  ctx.fillRect(x + inset, y + inset, size - inset * 2, size - inset * 2);
  ctx.strokeStyle = PALETTE.workstation;
  ctx.lineWidth = 1;
  ctx.strokeRect(x + inset + 0.5, y + inset + 0.5, size - inset * 2 - 1, size - inset * 2 - 1);
  ctx.fillStyle = PALETTE.workstation;
  const screenInset = inset + Math.max(1, Math.round(size * 0.12));
  ctx.fillRect(x + screenInset, y + screenInset, size - screenInset * 2, Math.max(1, Math.round(size * 0.18)));
  ctx.fillStyle = PALETTE.textFaint;
  ctx.fillRect(x + screenInset, y + size - screenInset - 1, size - screenInset * 2, 1);
  ctx.restore();
}

/** A resource pallet: stacked plates. */
export function drawResource(
  ctx: CanvasRenderingContext2D,
  pixel: Pixel,
): void {
  const { x, y, size } = pixel;
  const inset = Math.max(1, Math.round(size * 0.24));
  ctx.save();
  ctx.fillStyle = PALETTE.resource;
  ctx.globalAlpha = 0.85;
  for (let plate = 0; plate < 2; plate += 1) {
    const py = y + inset + plate * Math.max(2, Math.round((size - inset * 2) / 2));
    ctx.fillRect(x + inset, py, size - inset * 2, Math.max(1, Math.round((size - inset * 2) / 3)));
  }
  ctx.restore();
}

/** A dead zone: hatched hazard marking. */
export function drawDeadzone(
  ctx: CanvasRenderingContext2D,
  pixel: Pixel,
): void {
  const { x, y, size } = pixel;
  ctx.save();
  ctx.strokeStyle = "rgba(255,107,94,0.28)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, y + size);
  ctx.lineTo(x + size, y);
  ctx.stroke();
  ctx.restore();
}

/** A small "!" badge, used for warnings above a marker. */
export function drawBadge(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  size: number,
  color: string,
  glyph: "!" | "W" | "R" | "F" | "N",
): void {
  const half = Math.max(2, Math.round(size / 2));
  ctx.save();
  ctx.fillStyle = color;
  ctx.fillRect(cx - half, cy - half, half * 2, half * 2);
  ctx.strokeStyle = "#050807";
  ctx.lineWidth = 1;
  ctx.strokeRect(cx - half + 0.5, cy - half + 0.5, half * 2 - 1, half * 2 - 1);
  ctx.fillStyle = "#0B0F0E";
  ctx.font = `bold ${Math.max(6, half * 1.5)}px ui-monospace, monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, cx, cy + 0.5);
  ctx.restore();
}

/** Corner brackets marking a selection or a highlighted region. */
export function drawSelectionBrackets(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  color: string,
  arm = 4,
): void {
  const x0 = Math.round(x) + 0.5;
  const y0 = Math.round(y) + 0.5;
  const x1 = Math.round(x + width) - 0.5;
  const y1 = Math.round(y + height) - 0.5;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x0, y0 + arm);
  ctx.lineTo(x0, y0);
  ctx.lineTo(x0 + arm, y0);
  ctx.moveTo(x1 - arm, y0);
  ctx.lineTo(x1, y0);
  ctx.lineTo(x1, y0 + arm);
  ctx.moveTo(x1, y1 - arm);
  ctx.lineTo(x1, y1);
  ctx.lineTo(x1 - arm, y1);
  ctx.moveTo(x0 + arm, y1);
  ctx.lineTo(x0, y1);
  ctx.lineTo(x0, y1 - arm);
  ctx.stroke();
  ctx.restore();
}

/** A hatched warning fill for a conflict cell. */
export function drawConflictHatch(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  time: number,
): void {
  const px = Math.round(x);
  const py = Math.round(y);
  const s = Math.round(size);
  ctx.save();
  ctx.globalAlpha = 0.22 + 0.16 * Math.abs(Math.sin(time * 3));
  ctx.fillStyle = PALETTE.conflict;
  ctx.fillRect(px, py, s, s);
  ctx.globalAlpha = 0.7;
  ctx.strokeStyle = PALETTE.conflict;
  ctx.lineWidth = 1;
  ctx.beginPath();
  const slide = (Math.floor(time * 6) % 2) * 3;
  for (let offset = -s; offset < s * 2; offset += 6) {
    ctx.moveTo(px + offset + slide, py);
    ctx.lineTo(px + offset - s + slide, py + s);
  }
  ctx.stroke();
  ctx.restore();
}
