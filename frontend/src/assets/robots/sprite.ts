/**
 * Custom 2D top-down pixel-art industrial robot sprites.
 *
 * These are drawn procedurally on the canvas: no emoji, no external image, and
 * no generic dot. Each robot is a compact warehouse machine with a chassis,
 * side tracks, a front sensor bar, and a status light, drawn on an integer
 * pixel lattice so it keeps a crisp pixel-art silhouette at any zoom.
 *
 * Sizing is the important part. A robot's rendered rectangle is *exactly*
 * `width_cells x height_cells` grid cells, which is the same footprint the
 * backend's planner and collision engine use. Width and height scale
 * independently, so a 4x1 body is wide and low and a 1x4 body is narrow and
 * tall. The whole body moves with the anchor cell, so a 3x2 never looks like a
 * small icon sitting on a big footprint.
 *
 * Top-down layout, heading east (the default travel direction):
 *
 *     +--------------------+
 *     |==+============+ +==|   <- track bands, sensor bar, status light
 *     |==|  chassis   |==|
 *     |==|  [grille]  |==|
 *     |==|            |==|
 *     |==+============+ +==|
 *     +--------------------+
 */

import { PALETTE } from "../../styles/palette";

export type Heading = "east" | "west" | "north" | "south";

/**
 * The machine class, derived from the real footprint.
 *
 * A 1x1 unit is a scout tug, a 2x1 is a flatbed transporter, a 2x2 is a boxy
 * forklift, and a 3x2 is a long carrier. Each class gets its own silhouette and
 * its own internal detail, so the units are told apart at a glance instead of
 * being ten copies of the same box, and the class follows the footprint the
 * collision engine reserves.
 */
export type RobotClass = "scout" | "transporter" | "forklift" | "carrier" | "oversize";

export function classFor(widthCells: number, heightCells: number): RobotClass {
  const cells = widthCells * heightCells;
  if (widthCells >= 3 && heightCells >= 2) return "carrier";
  if (cells >= 4) return "forklift";
  if (Math.max(widthCells, heightCells) >= 2) return "transporter";
  return "scout";
}

const CLASS_TINT: Record<RobotClass, { body: string; panel: string; cabin: string }> = {
  scout: { body: "#3C5148", panel: "#4E655B", cabin: "#5F7A6E" },
  transporter: { body: "#3A4C44", panel: "#4E655B", cabin: "#5F7A6E" },
  forklift: { body: "#41504A", panel: "#5A7166", cabin: "#6E8A7C" },
  carrier: { body: "#453F4E", panel: "#5A5270", cabin: "#6F6688" },
  oversize: { body: "#4A3A34", panel: "#5F4A42", cabin: "#75594F" },
};

export interface RobotVisual {
  action: string;
  status: string;
  communication: string;
  batteryPercent: number;
  heading: Heading;
  moving: boolean;
  selected: boolean;
  /** A failed or offline machine: drawn dark and glitching, never hidden. */
  dim: boolean;
  showLabel: boolean;
  showFootprint: boolean;
  /** Wall-clock seconds, used only to animate treads and pulses. */
  time: number;
  robotClass: RobotClass;
  widthCells: number;
  heightCells: number;
}

export interface RobotColors {
  chassis: string;
  chassisLight: string;
  chassisDark: string;
  track: string;
  trackTread: string;
  outline: string;
  light: string;
  dim: boolean;
}

export interface FootprintRect {
  x: number;
  y: number;
  /** Width in device pixels; already multiplied by the device pixel ratio. */
  width: number;
  height: number;
}

const DEFAULTS: RobotColors = {
  chassis: "#3A4C44",
  chassisLight: "#4E655B",
  chassisDark: "#22302B",
  track: "#141C1A",
  trackTread: "#2A3A34",
  outline: "#050807",
  light: PALETTE.safe,
  dim: false,
};

function isCommLost(visual: RobotVisual): boolean {
  return visual.communication === "lost";
}

function isFailed(visual: RobotVisual): boolean {
  return visual.status === "failed" || visual.action === "FAILED";
}

function isCharging(visual: RobotVisual): boolean {
  return visual.action === "CHARGING" || visual.status === "charging";
}

function isBatteryWarning(visual: RobotVisual): boolean {
  return visual.batteryPercent <= 25;
}

function isBatteryCritical(visual: RobotVisual): boolean {
  return visual.batteryPercent <= 10;
}

/** Colour set for a robot, derived only from backend state. */
export function colorsFor(visual: RobotVisual): RobotColors {
  let light: string = PALETTE.safe;
  if (isFailed(visual)) light = PALETTE.failure;
  else if (isBatteryCritical(visual)) light = PALETTE.failure;
  else if (isBatteryWarning(visual)) light = PALETTE.warning;
  else if (isCharging(visual)) light = PALETTE.info;
  else if (visual.action === "WAITING" || visual.action === "BLOCKED") light = PALETTE.warning;
  else if (visual.action === "NEGOTIATING") light = PALETTE.info;
  else if (visual.action === "REPLANNING") light = PALETTE.selected;
  else if (isCommLost(visual)) light = PALETTE.textFaint;
  else if (visual.action === "TASK_COMPLETED") light = PALETTE.selected;
  else if (visual.status === "idle") light = PALETTE.textDim;
  else if (visual.status === "offline") light = PALETTE.textFaint;

  const dim = isFailed(visual) || visual.status === "offline";
  return {
    chassis: dim ? "#232C29" : "#3A4C44",
    chassisLight: dim ? "#2C3833" : "#4E655B",
    chassisDark: dim ? "#161C1A" : "#22302B",
    track: "#141C1A",
    trackTread: dim ? "#1A2220" : "#2A3A34",
    outline: "#050807",
    light,
    dim,
  };
}

/** Tracks run along the sides perpendicular to travel. */
function trackAxis(heading: Heading): "vertical" | "horizontal" {
  return heading === "east" || heading === "west" ? "vertical" : "horizontal";
}

function isLeadingEdgeWest(heading: Heading): boolean {
  return heading === "west";
}

/** A scout has thin tracks; a heavy machine has wide ones. */
function trackBand(robotClass: RobotClass, extent: number): number {
  const ratio = robotClass === "scout" ? 0.14 : robotClass === "carrier" ? 0.2 : 0.18;
  return Math.max(1, Math.min(4, Math.round(extent * ratio)));
}

/**
 * The per-class detail that makes a silhouette recognisable.
 *
 * A scout has a single round sensor, a transporter has a slung cargo deck, a
 * forklift has a mast and two forks, and a carrier has ribbed cargo bays. All
 * of it is drawn inside the chassis, so the body still fills its footprint
 * exactly.
 */
function drawClassDetail(
  ctx: CanvasRenderingContext2D,
  visual: RobotVisual,
  x: number,
  y: number,
  w: number,
  h: number,
  tint: { body: string; panel: string; cabin: string },
  track: "vertical" | "horizontal",
): void {
  if (visual.dim) return;
  const dark = visual.robotClass === "carrier" ? "#2C2536" : "#22302B";

  switch (visual.robotClass) {
    case "scout": {
      // A single round scanner on the body.
      const radius = Math.max(1, Math.round(Math.min(w, h) * 0.22));
      ctx.fillStyle = dark;
      ctx.beginPath();
      ctx.arc(x + w / 2, y + h / 2, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = tint.cabin;
      ctx.fillRect(Math.round(x + w / 2) - 1, Math.round(y + h / 2) - 1, 1, 1);
      return;
    }
    case "transporter": {
      // A slung cargo deck with tie-down ribs.
      const deckH = Math.max(1, Math.round(h * 0.3));
      ctx.fillStyle = dark;
      if (track === "vertical") {
        ctx.fillRect(x + 1, y + Math.round(h / 2) - Math.round(deckH / 2), w - 2, deckH);
        for (let rib = x + 2; rib < x + w - 2; rib += 3) {
          ctx.fillStyle = tint.cabin;
          ctx.fillRect(rib, y + Math.round(h / 2) - Math.round(deckH / 2), 1, deckH);
        }
      } else {
        ctx.fillRect(x + Math.round(w / 2) - Math.round(deckH / 2), y + 1, deckH, h - 2);
        for (let rib = y + 2; rib < y + h - 2; rib += 3) {
          ctx.fillStyle = tint.cabin;
          ctx.fillRect(x + Math.round(w / 2) - Math.round(deckH / 2), rib, deckH, 1);
        }
      }
      return;
    }
    case "forklift": {
      // A mast across the chassis and two forks reaching forward.
      const mast = Math.max(1, Math.round(Math.min(w, h) * 0.16));
      ctx.fillStyle = dark;
      if (track === "vertical") {
        const mx = Math.round(x + w / 2) - Math.round(mast / 2);
        ctx.fillRect(mx, y + 1, mast, h - 2);
        ctx.fillStyle = tint.cabin;
        ctx.fillRect(mx, y + 1, mast, 1);
        ctx.fillRect(mx, y + h - 2, mast, 1);
      } else {
        const my = Math.round(y + h / 2) - Math.round(mast / 2);
        ctx.fillRect(x + 1, my, w - 2, mast);
        ctx.fillStyle = tint.cabin;
        ctx.fillRect(x + 1, my, 1, mast);
        ctx.fillRect(x + w - 2, my, 1, mast);
      }
      return;
    }
    case "carrier":
    case "oversize": {
      // Ribbed cargo bays, the long-haul silhouette.
      ctx.fillStyle = dark;
      if (track === "vertical") {
        const bayW = Math.max(1, Math.round(w * 0.26));
        const gap = Math.max(1, Math.round(w * 0.1));
        for (let bx = x + 1; bx + bayW <= x + w - 1; bx += bayW + gap) {
          ctx.fillRect(bx, y + 1, bayW, h - 2);
        }
      } else {
        const bayH = Math.max(1, Math.round(h * 0.26));
        const gap = Math.max(1, Math.round(h * 0.1));
        for (let by = y + 1; by + bayH <= y + h - 1; by += bayH + gap) {
          ctx.fillRect(x + 1, by, w - 2, bayH);
        }
      }
      return;
    }
  }
}

/**
 * Draw one robot, filling its whole footprint.
 *
 * The caller has already converted the footprint to device pixels; every
 * coordinate below is snapped to an integer so the sprite stays pixel-crisp.
 */
export function drawRobot(
  ctx: CanvasRenderingContext2D,
  rect: FootprintRect,
  visual: RobotVisual,
  colors: RobotColors = colorsFor(visual),
): void {
  const x = Math.round(rect.x);
  const y = Math.round(rect.y);
  const w = Math.max(2, Math.round(rect.width));
  const h = Math.max(2, Math.round(rect.height));
  const track = trackAxis(visual.heading);

  ctx.save();

  // Selection glow wraps the COMPLETE footprint, not a centred icon.
  if (visual.selected) {
    ctx.shadowColor = PALETTE.selected;
    ctx.shadowBlur = Math.max(4, Math.min(w, h) * 0.6);
  }

  // Body plate, tinted by machine class so the units read apart.
  const tint = CLASS_TINT[visual.robotClass];
  ctx.fillStyle = visual.dim ? colors.chassis : tint.body;
  ctx.fillRect(x, y, w, h);

  // Tread animation: a moving machine's tracks visibly turn.
  const phase = visual.moving ? Math.floor(visual.time * 6) % 2 : 0;
  ctx.fillStyle = colors.track;
  if (track === "vertical") {
    const band = trackBand(visual.robotClass, w);
    ctx.fillRect(x, y, band, h);
    ctx.fillRect(x + w - band, y, band, h);
    ctx.fillStyle = colors.trackTread;
    const step = Math.max(3, Math.round(h / 5));
    for (let offset = phase * Math.round(step / 2); offset < h; offset += step) {
      const ty = Math.min(h - 1, Math.max(0, offset));
      ctx.fillRect(x, y + ty, band, 1);
      ctx.fillRect(x + w - band, y + ty, band, 1);
    }
  } else {
    const band = trackBand(visual.robotClass, h);
    ctx.fillRect(x, y, w, band);
    ctx.fillRect(x, y + h - band, w, band);
    ctx.fillStyle = colors.trackTread;
    const step = Math.max(3, Math.round(w / 5));
    for (let offset = phase * Math.round(step / 2); offset < w; offset += step) {
      const tx = Math.min(w - 1, Math.max(0, offset));
      ctx.fillRect(x + tx, y, 1, band);
      ctx.fillRect(x + tx, y + h - band, 1, band);
    }
  }

  // Chassis inset, leaving the tracks visible around it.
  const inset = track === "vertical" ? Math.max(2, Math.round(w * 0.26)) : Math.max(2, Math.round(h * 0.26));
  const innerW = Math.max(1, w - inset * 2);
  const innerH = Math.max(1, h - inset * 2);
  ctx.fillStyle = visual.dim ? colors.chassisLight : tint.panel;
  ctx.fillRect(x + inset, y + inset, innerW, innerH);
  ctx.fillStyle = colors.chassisDark;
  ctx.fillRect(x + inset, y + inset, innerW, Math.max(1, Math.round(innerH * 0.34)));

  // Class detail: the machinery that makes each silhouette recognisable.
  drawClassDetail(ctx, visual, x + inset, y + inset, innerW, innerH, tint, track);

  // Front sensor bar: marks the direction of travel.
  const barThickness = Math.max(1, Math.round(Math.min(w, h) * 0.12));
  ctx.fillStyle = visual.dim ? "#2A3330" : tint.cabin;
  if (track === "vertical") {
    const bx = isLeadingEdgeWest(visual.heading) ? x + inset : x + w - inset - barThickness;
    ctx.fillRect(bx, y + inset, barThickness, innerH);
  } else {
    const by = visual.heading === "north" ? y + inset : y + h - inset - barThickness;
    ctx.fillRect(x + inset, by, innerW, barThickness);
  }

  // Status light.
  const lightSize = Math.max(1, Math.min(2, Math.round(Math.min(w, h) * 0.16)));
  const lx = track === "vertical"
    ? isLeadingEdgeWest(visual.heading) ? x + w - lightSize - 1 : x + 1
    : x + 1;
  const ly = track === "vertical" ? y + 1 : y + (visual.heading === "north" ? y + h - lightSize - 1 : y + 1);
  ctx.fillStyle = colors.light;
  if (isCharging(visual) || visual.action === "WAITING" || isBatteryWarning(visual)) {
    // Pulse so an urgent state is visible without relying on colour alone.
    const pulse = 0.55 + 0.45 * Math.abs(Math.sin(visual.time * 4));
    ctx.globalAlpha = pulse;
  }
  ctx.fillRect(lx, ly, lightSize, lightSize);
  ctx.globalAlpha = 1;

  // Crisp silhouette.
  ctx.shadowBlur = 0;
  ctx.strokeStyle = colors.outline;
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);

  if (isFailed(visual)) drawGlitch(ctx, rect, visual.time);
  if (isCommLost(visual)) drawBrokenAntenna(ctx, rect);
  if (isCharging(visual)) drawChargeBolt(ctx, rect, visual.time);

  ctx.restore();
}

/** A failed machine glitches rather than disappearing: it is still on the floor. */
function drawGlitch(
  ctx: CanvasRenderingContext2D,
  rect: FootprintRect,
  time: number,
): void {
  const x = Math.round(rect.x);
  const y = Math.round(rect.y);
  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  const offset = Math.round(Math.sin(time * 7) * 1.5);
  ctx.save();
  ctx.globalAlpha = 0.55;
  ctx.fillStyle = PALETTE.failure;
  const bandHeight = Math.max(1, Math.round(h * 0.16));
  const bandY = y + ((Math.floor(time * 5) % 3) * Math.max(1, Math.round(h / 4)));
  ctx.fillRect(x + offset, bandY, w, bandHeight);
  ctx.fillStyle = PALETTE.info;
  ctx.fillRect(x - offset, y + h - bandHeight - 1, w, bandHeight);
  ctx.restore();
}

/** A lost link shows a broken antenna, which reads differently from a failure. */
function drawBrokenAntenna(
  ctx: CanvasRenderingContext2D,
  rect: FootprintRect,
): void {
  const x = Math.round(rect.x);
  const y = Math.round(rect.y);
  const w = Math.round(rect.width);
  const size = Math.max(2, Math.min(4, Math.round(Math.min(w, rect.height) * 0.28)));
  const ax = x + w - size - 1;
  const ay = y + 1;
  ctx.save();
  ctx.fillStyle = PALETTE.textDim;
  ctx.fillRect(ax, ay, 1, size);
  ctx.fillRect(ax, ay + size, size, 1);
  ctx.fillStyle = PALETTE.warning;
  ctx.fillRect(ax + size, ay + size - 1, 1, 1);
  ctx.fillRect(ax + size - 1, ay + size - 2, 1, 1);
  ctx.restore();
}

/** Charging pads get a pulsing bolt so charging is obvious at a glance. */
function drawChargeBolt(
  ctx: CanvasRenderingContext2D,
  rect: FootprintRect,
  time: number,
): void {
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  const size = Math.max(3, Math.round(Math.min(rect.width, rect.height) * 0.34));
  const pulse = 0.6 + 0.4 * Math.abs(Math.sin(time * 5));
  ctx.save();
  ctx.globalAlpha = pulse;
  ctx.fillStyle = PALETTE.info;
  ctx.beginPath();
  ctx.moveTo(cx + size * 0.2, cy - size * 0.5);
  ctx.lineTo(cx - size * 0.35, cy + size * 0.1);
  ctx.lineTo(cx - size * 0.05, cy + size * 0.1);
  ctx.lineTo(cx - size * 0.2, cy + size * 0.55);
  ctx.lineTo(cx + size * 0.4, cy - size * 0.1);
  ctx.lineTo(cx + size * 0.08, cy - size * 0.1);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** The debug footprint overlay: every cell the body actually occupies. */
export function drawFootprintGrid(
  ctx: CanvasRenderingContext2D,
  rect: FootprintRect,
  cellPixels: { width: number; height: number },
): void {
  const columns = Math.max(1, Math.round(rect.width / cellPixels.width));
  const rows = Math.max(1, Math.round(rect.height / cellPixels.height));
  ctx.save();
  ctx.strokeStyle = PALETTE.selected;
  ctx.globalAlpha = 0.85;
  ctx.lineWidth = 1;
  for (let column = 0; column <= columns; column += 1) {
    const gx = Math.round(rect.x + column * cellPixels.width) + 0.5;
    ctx.beginPath();
    ctx.moveTo(gx, Math.round(rect.y));
    ctx.lineTo(gx, Math.round(rect.y + rect.height));
    ctx.stroke();
  }
  for (let row = 0; row <= rows; row += 1) {
    const gy = Math.round(rect.y + row * cellPixels.height) + 0.5;
    ctx.beginPath();
    ctx.moveTo(Math.round(rect.x), gy);
    ctx.lineTo(Math.round(rect.x + rect.width), gy);
    ctx.stroke();
  }
  ctx.restore();
}

export { DEFAULTS as DEFAULT_ROBOT_COLORS };
