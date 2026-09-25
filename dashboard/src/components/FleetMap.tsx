/**
 * The fleet map.
 *
 * This is the page. Everything else in the console is a panel around it, so it
 * gets the canvas, the colour budget, and the performance budget.
 *
 * Rendering rules, in order of importance:
 *
 * 1. The map is a view. Positions, routes, conflicts and deadlocks all come
 *    from the snapshot; nothing here decides anything.
 * 2. 500 robots must stay smooth. The world, its static features, and its
 *    routes are drawn once into offscreen layers and blitted, and only robots
 *    are redrawn per frame.
 * 3. Status must never be carried by colour alone. Each robot has a distinct
 *    shape as well as a tone, and the selected robot is ringed and labelled.
 * 4. Motion exists to show state change. Under `prefers-reduced-motion` the
 *    pulse animations stop and positions are drawn without interpolation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { findRobotAtScreenPosition, getWorldTransform, worldToScreen } from "../coordinate";
import { robotTone } from "../selectors";
import type { DeadlockCycle } from "../selectors";
import type { Conflict, Robot, RoutePlan, SimulationSnapshot } from "../types";

export interface MapLayers {
  routes: boolean;
  conflicts: boolean;
  deadlocks: boolean;
  reservations: boolean;
  cells: boolean;
  labels: boolean;
}

export const DEFAULT_LAYERS: MapLayers = {
  routes: true,
  conflicts: true,
  deadlocks: true,
  reservations: false,
  cells: true,
  labels: true,
};

export interface FleetMapProps {
  snapshot: SimulationSnapshot | null;
  deadlocks: readonly DeadlockCycle[];
  selectedRobotId: string | null;
  hoveredRobotId: string | null;
  layers: MapLayers;
  theme: "dark" | "light";
  stale: boolean;
  onSelectRobot: (robotId: string | null) => void;
  onHoverRobot: (robotId: string | null) => void;
  onToggleLayer: (layer: keyof MapLayers) => void;
  onResetCamera: () => void;
}

/** Marker geometry by status: shape carries meaning so colour is not alone. */
const SHAPE_BY_STATUS: Record<Robot["status"], "circle" | "square" | "triangle" | "diamond" | "cross"> = {
  active: "circle",
  idle: "circle",
  blocked: "triangle",
  charging: "square",
  degraded: "diamond",
  failed: "cross",
  offline: "diamond",
};

const MIN_ZOOM = 0.55;
const MAX_ZOOM = 6;
const HIT_RADIUS_PX = 14;

interface ViewportSize {
  width: number;
  height: number;
}

interface Camera {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Read a resolved design token so canvas colours follow the active theme. */
function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value.trim() === "" ? fallback : value.trim();
}

/**
 * Read every canvas colour from the resolved design tokens.
 *
 * The canvas cannot use CSS custom properties directly, so the values are read
 * back out of the document. That means a theme flip has to re-run this, which
 * is why the memo below depends on the theme name even though it is not passed
 * in: the tokens on the document are what actually change.
 */
function readPalette() {
  return {
    floor: token("--map-floor", "#111"),
    grid: token("--map-grid", "rgba(255,255,255,0.05)"),
    boundary: token("--map-boundary", "#666"),
    obstacle: token("--map-cell-obstacle", "#333"),
    workstation: token("--map-cell-workstation", "#8a6d1f"),
    charging: token("--map-cell-charging", "#1d4ed8"),
    resource: token("--map-cell-resource", "#0e7490"),
    deadzone: token("--map-cell-deadzone", "#4c1d95"),
    cellEdge: token("--map-cell-edge", "rgba(0,0,0,0.18)"),
    ok: token("--ok", "#4ade80"),
    warn: token("--warn", "#fbbf24"),
    crit: token("--crit", "#fb7185"),
    info: token("--accent", "#38bdf8"),
    idle: token("--idle", "#94a3b8"),
    ink: token("--ink", "#eee"),
    inkMuted: token("--ink-3", "#888"),
  };
}

export default function FleetMap({
  snapshot,
  deadlocks,
  selectedRobotId,
  hoveredRobotId,
  layers,
  theme,
  stale,
  onSelectRobot,
  onHoverRobot,
  onToggleLayer,
  onResetCamera,
}: FleetMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const drawRef = useRef<number | undefined>(undefined);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const staticLayerRef = useRef<HTMLCanvasElement | null>(null);
  const routeLayerRef = useRef<HTMLCanvasElement | null>(null);
  const reducedMotionRef = useRef(false);

  const [viewport, setViewport] = useState<ViewportSize>({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ zoom: 1, offsetX: 0, offsetY: 0 });
  const [pointerWorld, setPointerWorld] = useState<{ x: number; y: number } | null>(null);

  const palette = useMemo(
    () => readPalette(),
    // Re-read the tokens whenever the theme flips; see readPalette.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [theme],
  );

  useEffect(() => {
    reducedMotionRef.current =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }, []);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => {
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  /** Robots by id, so overlays can resolve positions without scanning. */
  const robotsById = useMemo(() => {
    const map = new Map<string, Robot>();
    for (const robot of snapshot?.robots ?? []) map.set(robot.robot_id, robot);
    return map;
  }, [snapshot]);

  const conflictIndex = useMemo(
    () => buildConflictIndex(snapshot?.conflicts ?? []),
    [snapshot],
  );

  // ---------------------------------------------------------------- layers
  const drawStaticLayer = useCallback(() => {
    if (!snapshot || viewport.width === 0 || viewport.height === 0) return;
    const layer = ensureLayer(staticLayerRef, viewport);
    const context = layer.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    const world = snapshot.world;
    const transform = getWorldTransform(world, viewport, camera);
    const left = transform.originX;
    const top = transform.originY - world.height_m * transform.scale;
    const worldWidth = world.width_m * transform.scale;
    const worldHeight = world.height_m * transform.scale;

    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, viewport.width, viewport.height);
    context.fillStyle = palette.floor;
    context.fillRect(left, top, worldWidth, worldHeight);

    if (transform.scale > 1.6) {
      context.strokeStyle = palette.grid;
      context.lineWidth = 1;
      context.beginPath();
      for (let column = 0; column <= world.columns; column += 1) {
        const x = left + column * world.cell_size_m * transform.scale;
        context.moveTo(x, top);
        context.lineTo(x, top + worldHeight);
      }
      for (let row = 0; row <= world.rows; row += 1) {
        const y = top + worldHeight - row * world.cell_size_m * transform.scale;
        context.moveTo(left, y);
        context.lineTo(left + worldWidth, y);
      }
      context.stroke();
    }

    if (layers.cells) {
      const cellSize = world.cell_size_m * transform.scale;
      for (const cell of world.cells) {
        const x = left + cell.cell_x * cellSize;
        const y = top + worldHeight - (cell.cell_y + 1) * cellSize;
        const size = Math.max(1, cellSize);
        context.fillStyle = cellColor(cell.cell_type, palette);
        context.fillRect(x, y, size, size);
        // A hairline keeps racking reading as solid structure rather than a
        // tint, and it is the only thing that separates two adjacent blocks
        // in the light theme.
        context.strokeStyle = palette.cellEdge;
        context.lineWidth = 1;
        context.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
      }
    }

    context.strokeStyle = palette.boundary;
    context.lineWidth = 1.5;
    context.strokeRect(left, top, worldWidth, worldHeight);
  }, [snapshot, viewport, camera, palette, layers.cells]);

  const drawRouteLayer = useCallback(() => {
    if (!snapshot || viewport.width === 0 || viewport.height === 0) return;
    const layer = ensureLayer(routeLayerRef, viewport);
    const context = layer.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    const transform = getWorldTransform(snapshot.world, viewport, camera);

    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, viewport.width, viewport.height);
    if (!layers.routes) return;

    // Every active robot contributes a route, so at fleet scale these are drawn
    // as a faint flow field rather than as individual lines. At full strength
    // 400-odd polylines bury the floor they are supposed to explain; the route
    // that matters is the selected one, and it is drawn separately, on top.
    context.globalAlpha = 0.16;
    context.lineWidth = 1;
    for (const route of snapshot.routes) {
      if (route.waypoints.length < 2) continue;
      context.beginPath();
      route.waypoints.forEach((waypoint, index) => {
        const point = worldToScreen(waypoint, transform);
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.strokeStyle = routeColor(route, palette);
      context.stroke();
    }
    context.globalAlpha = 1;
  }, [snapshot, viewport, camera, palette, layers.routes]);

  // Rebuild the cached layers when their inputs change.
  useEffect(() => {
    drawStaticLayer();
  }, [drawStaticLayer]);
  useEffect(() => {
    drawRouteLayer();
  }, [drawRouteLayer]);

  // ------------------------------------------------------------------ draw
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || viewport.width === 0 || viewport.height === 0) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    const width = viewport.width;
    const height = viewport.height;

    const draw = () => {
      drawRef.current = undefined;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      if (staticLayerRef.current) {
        context.drawImage(staticLayerRef.current, 0, 0, width, height);
      }
      if (routeLayerRef.current) {
        context.drawImage(routeLayerRef.current, 0, 0, width, height);
      }
      if (!snapshot) return;

      const transform = getWorldTransform(snapshot.world, viewport, camera);
      const pulses: Map<string, number> = reducedMotionRef.current
        ? new Map()
        : pulsePhases(snapshot.robots);

      // The routes worth reading are the ones the operator is looking at, so
      // they are drawn last, at full strength, over the flow field.
      if (layers.routes) {
        drawFocusedRoutes(
          context,
          snapshot.routes,
          [selectedRobotId, hoveredRobotId],
          transform,
          palette,
        );
      }

      for (const robot of snapshot.robots) {
        const point = worldToScreen(robot.position, transform);
        drawRobot(context, robot, point, {
          selected: robot.robot_id === selectedRobotId,
          hovered: robot.robot_id === hoveredRobotId,
          conflicted: conflictIndex.has(robot.robot_id),
          scale: transform.scale,
          pulse: pulses.get(robot.robot_id) ?? null,
          palette,
        });
      }

      if (layers.labels && transform.scale > 2.2) {
        drawSelectedLabel(context, snapshot, selectedRobotId, transform, palette);
      }

      if (layers.deadlocks) {
        drawDeadlockCycles(context, deadlocks, robotsById, transform, palette);
      }

      if (layers.conflicts) {
        drawConflicts(context, snapshot.conflicts, transform, palette);
      }
    };

    if (drawRef.current === undefined) {
      drawRef.current = window.requestAnimationFrame(draw);
    }
    return () => {
      if (drawRef.current !== undefined) {
        window.cancelAnimationFrame(drawRef.current);
        drawRef.current = undefined;
      }
    };
  }, [
    snapshot,
    viewport,
    camera,
    selectedRobotId,
    hoveredRobotId,
    layers,
    deadlocks,
    robotsById,
    conflictIndex,
    palette,
  ]);

  // ---------------------------------------------------------------- input
  const selectAt = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>) => {
      if (!snapshot) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const robot = findRobotAtScreenPosition(
        snapshot.robots,
        pointer,
        snapshot.world,
        viewport,
        camera,
        HIT_RADIUS_PX,
      );
      onSelectRobot(robot?.robot_id ?? null);
    },
    [snapshot, viewport, camera, onSelectRobot],
  );

  const handlePointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag) {
      const deltaX = event.clientX - drag.x;
      const deltaY = event.clientY - drag.y;
      if (Math.abs(deltaX) + Math.abs(deltaY) > 2) drag.moved = true;
      drag.x = event.clientX;
      drag.y = event.clientY;
      if (drag.moved) {
        setCamera((current) => ({
          ...current,
          offsetX: current.offsetX + deltaX,
          offsetY: current.offsetY + deltaY,
        }));
      }
    }
    if (!snapshot) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    setPointerWorld({
      x: (pointer.x - getWorldTransform(snapshot.world, viewport, camera).originX) /
        getWorldTransform(snapshot.world, viewport, camera).scale,
      y: (getWorldTransform(snapshot.world, viewport, camera).originY - pointer.y) /
        getWorldTransform(snapshot.world, viewport, camera).scale,
    });
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag && !drag.moved) selectAt(event);
  };

  // Zoom is registered as a native non-passive listener below, because a React
  // wheel handler is passive by default and cannot stop the page from scrolling.

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const listener = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 0.9 : 1.1;
      setCamera((current) => ({
        ...current,
        zoom: clamp(current.zoom * factor, MIN_ZOOM, MAX_ZOOM),
      }));
    };
    canvas.addEventListener("wheel", listener, { passive: false });
    return () => canvas.removeEventListener("wheel", listener);
  }, []);

  const layerToggles: Array<{ key: keyof MapLayers; label: string }> = [
    { key: "routes", label: "Routes" },
    { key: "conflicts", label: "Conflicts" },
    { key: "deadlocks", label: "Deadlocks" },
    { key: "cells", label: "Floor" },
    { key: "labels", label: "Labels" },
  ];

  return (
    <div className="map" ref={frameRef}>
      <canvas
        ref={canvasRef}
        className="map__canvas"
        role="img"
        aria-label={
          snapshot
            ? `Fleet world map: ${snapshot.robots.length} robots, ${snapshot.routes.length} active routes, ${snapshot.conflicts.length} open conflicts`
            : "Fleet world map, awaiting the first snapshot"
        }
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => {
          dragRef.current = null;
        }}
        onPointerLeave={() => onHoverRobot(null)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onSelectRobot(null);
          if (event.key === "0") onResetCamera();
        }}
      />

      <div className="map__toolbar">
        <div className="map__layers" role="group" aria-label="Map layers">
          {layerToggles.map((layer) => (
            <button
              key={layer.key}
              type="button"
              className="chip chip--toggle"
              aria-pressed={layers[layer.key]}
              onClick={() => onToggleLayer(layer.key)}
            >
              {layer.label}
            </button>
          ))}
        </div>
        <button type="button" className="chip" onClick={onResetCamera}>
          Reset view
        </button>
      </div>

      <div className="map__readout">
        {pointerWorld ? (
          <span className="mono">
            {pointerWorld.x.toFixed(1)}, {pointerWorld.y.toFixed(1)} m
          </span>
        ) : (
          <span className="muted">Scroll to zoom · drag to pan · 0 resets</span>
        )}
      </div>

      {stale && snapshot ? (
        <div className="map__badge map__badge--warn">Stale projection</div>
      ) : null}

      {!snapshot ? (
        <div className="map__empty" role="status">
          <p className="map__empty-title">Awaiting the first snapshot</p>
          <p className="muted">
            The console shows nothing until the runtime publishes a canonical projection.
          </p>
        </div>
      ) : null}
    </div>
  );
}

// -------------------------------------------------------------------- helpers

function ensureLayer(
  ref: { current: HTMLCanvasElement | null },
  viewport: ViewportSize,
): HTMLCanvasElement {
  if (!ref.current) {
    ref.current = document.createElement("canvas");
  }
  const layer = ref.current;
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(viewport.width * ratio));
  const height = Math.max(1, Math.floor(viewport.height * ratio));
  if (layer.width !== width || layer.height !== height) {
    layer.width = width;
    layer.height = height;
  }
  return layer;
}

function cellColor(
  cellType: string,
  palette: ReturnType<typeof readPalette>,
): string {
  switch (cellType) {
    case "obstacle":
      return palette.obstacle;
    case "workstation":
      return palette.workstation;
    case "charging":
      return palette.charging;
    case "resource":
      return palette.resource;
    case "deadzone":
      return palette.deadzone;
    default:
      return palette.floor;
  }
}

function routeColor(route: RoutePlan, palette: ReturnType<typeof readPalette>): string {
  switch (route.status) {
    case "blocked":
      return palette.warn;
    case "replanned":
      return palette.info;
    case "completed":
      return palette.ok;
    case "invalid":
      return palette.crit;
    default:
      return palette.info;
  }
}

function toneColor(tone: string, palette: ReturnType<typeof readPalette>): string {
  switch (tone) {
    case "ok":
      return palette.ok;
    case "warn":
      return palette.warn;
    case "crit":
      return palette.crit;
    case "info":
      return palette.info;
    default:
      return palette.idle;
  }
}

function pulsePhases(robots: readonly Robot[]): Map<string, number> {
  const phases = new Map<string, number>();
  for (const robot of robots) {
    if (robot.status === "failed" || robot.status === "blocked") {
      phases.set(robot.robot_id, (robot.robot_id.length % 7) / 7);
    }
  }
  return phases;
}

interface DrawRobotOptions {
  selected: boolean;
  hovered: boolean;
  conflicted: boolean;
  scale: number;
  pulse: number | null;
  palette: ReturnType<typeof readPalette>;
}

function drawRobot(
  context: CanvasRenderingContext2D,
  robot: Robot,
  point: { x: number; y: number },
  options: DrawRobotOptions,
): void {
  const { selected, hovered, conflicted, scale, pulse, palette } = options;
  const radius = clamp(3.2 + scale * 0.55, 2.6, 8);
  const color = toneColor(robotTone(robot), palette);
  const shape = SHAPE_BY_STATUS[robot.status];

  context.save();
  context.translate(point.x, point.y);

  if (selected || hovered) {
    context.beginPath();
    context.arc(0, 0, radius + 5, 0, Math.PI * 2);
    context.strokeStyle = selected ? palette.ink : palette.inkMuted;
    context.lineWidth = selected ? 2 : 1;
    context.stroke();
  }

  if (conflicted) {
    context.beginPath();
    context.arc(0, 0, radius + 3.5, 0, Math.PI * 2);
    context.strokeStyle = palette.warn;
    context.lineWidth = 1.4;
    context.stroke();
  }

  if (pulse !== null) {
    const growth = 1 + 0.35 * Math.abs(Math.sin(pulse * Math.PI * 2));
    context.beginPath();
    context.arc(0, 0, radius * growth, 0, Math.PI * 2);
    context.fillStyle = color;
    context.globalAlpha = 0.18;
    context.fill();
    context.globalAlpha = 1;
  }

  context.beginPath();
  switch (shape) {
    case "square":
      context.rect(-radius, -radius, radius * 2, radius * 2);
      break;
    case "triangle":
      context.moveTo(0, -radius - 1);
      context.lineTo(radius + 0.5, radius);
      context.lineTo(-radius - 0.5, radius);
      context.closePath();
      break;
    case "diamond":
      context.moveTo(0, -radius - 1);
      context.lineTo(radius + 1, 0);
      context.lineTo(0, radius + 1);
      context.lineTo(-radius - 1, 0);
      context.closePath();
      break;
    case "cross":
      context.moveTo(-radius, -radius);
      context.lineTo(radius, radius);
      context.moveTo(radius, -radius);
      context.lineTo(-radius, radius);
      context.lineWidth = 2;
      context.strokeStyle = color;
      context.stroke();
      break;
    default:
      context.arc(0, 0, radius, 0, Math.PI * 2);
  }

  if (shape !== "cross") {
    context.fillStyle = color;
    context.globalAlpha = robot.status === "offline" ? 0.55 : 0.92;
    context.fill();
    context.globalAlpha = 1;
    context.lineWidth = selected ? 2 : 0.9;
    context.strokeStyle = palette.floor;
    context.stroke();
  }

  if (robot.battery_percent <= 20 && shape !== "cross") {
    // A low-battery robot gets a second, inner mark so the warning survives a
    // greyscale print and a red-green colour blind viewer.
    context.beginPath();
    context.arc(0, 0, Math.max(1, radius * 0.35), 0, Math.PI * 2);
    context.fillStyle = palette.floor;
    context.fill();
  }

  context.restore();
}

function drawFocusedRoutes(
  context: CanvasRenderingContext2D,
  routes: readonly RoutePlan[],
  robotIds: readonly (string | null)[],
  transform: ReturnType<typeof getWorldTransform>,
  palette: ReturnType<typeof readPalette>,
): void {
  // Trace the routes of one or two robots at full strength.
  //
  // The rest of the fleet is drawn as a faint flow field underneath. This is the
  // detail-on-demand half: when an operator selects a robot, its path is the one
  // line on the map that needs to be legible.

  for (const robotId of robotIds) {
    if (!robotId) continue;
    const route = routes.find((candidate) => candidate.robot_id === robotId);
    if (!route || route.waypoints.length < 2) continue;

    context.save();
    context.beginPath();
    route.waypoints.forEach((waypoint, index) => {
      const point = worldToScreen(waypoint, transform);
      if (index === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.strokeStyle = routeColor(route, palette);
    context.lineWidth = 2.2;
    context.lineJoin = "round";
    context.shadowColor = palette.floor;
    context.shadowBlur = 4;
    context.stroke();
    context.restore();

    // Mark the destination so the end of the route is unambiguous.
    const end = worldToScreen(route.waypoints[route.waypoints.length - 1], transform);
    context.beginPath();
    context.arc(end.x, end.y, 4, 0, Math.PI * 2);
    context.fillStyle = routeColor(route, palette);
    context.fill();
    context.lineWidth = 1.5;
    context.strokeStyle = palette.floor;
    context.stroke();
  }
}

function drawSelectedLabel(
  context: CanvasRenderingContext2D,
  snapshot: SimulationSnapshot,
  selectedRobotId: string | null,
  transform: ReturnType<typeof getWorldTransform>,
  palette: ReturnType<typeof readPalette>,
): void {
  if (!selectedRobotId) return;
  const robot = snapshot.robots.find((candidate) => candidate.robot_id === selectedRobotId);
  if (!robot) return;
  const point = worldToScreen(robot.position, transform);
  context.font = "500 11px 'JetBrains Mono', ui-monospace, monospace";
  context.fillStyle = palette.ink;
  context.textAlign = "left";
  context.textBaseline = "bottom";
  context.fillText(robot.robot_id, point.x + 10, point.y - 8);
}

function drawConflicts(
  context: CanvasRenderingContext2D,
  conflicts: readonly Conflict[],
  transform: ReturnType<typeof getWorldTransform>,
  palette: ReturnType<typeof readPalette>,
): void {
  for (const conflict of conflicts) {
    const point = worldToScreen(conflict.position, transform);
    const color =
      conflict.severity === "critical"
        ? palette.crit
        : conflict.severity === "warning"
          ? palette.warn
          : palette.info;
    context.strokeStyle = color;
    context.lineWidth = 1.6;
    if (conflict.kind === "right_of_way") {
      // Right-of-way is drawn as a diamond so it cannot be mistaken for an
      // imminent collision.
      context.beginPath();
      context.moveTo(point.x, point.y - 9);
      context.lineTo(point.x + 9, point.y);
      context.lineTo(point.x, point.y + 9);
      context.lineTo(point.x - 9, point.y);
      context.closePath();
      context.stroke();
      continue;
    }
    context.beginPath();
    context.arc(point.x, point.y, 9, 0, Math.PI * 2);
    context.stroke();
    context.beginPath();
    context.moveTo(point.x - 4, point.y - 4);
    context.lineTo(point.x + 4, point.y + 4);
    context.moveTo(point.x + 4, point.y - 4);
    context.lineTo(point.x - 4, point.y + 4);
    context.stroke();
  }
}

function drawDeadlockCycles(
  context: CanvasRenderingContext2D,
  deadlocks: readonly DeadlockCycle[],
  robotsById: Map<string, Robot>,
  transform: ReturnType<typeof getWorldTransform>,
  palette: ReturnType<typeof readPalette>,
): void {
  context.save();
  context.setLineDash([5, 4]);
  context.strokeStyle = palette.crit;
  context.lineWidth = 1.8;
  for (const cycle of deadlocks) {
    const points = cycle.robotIds
      .map((robotId) => robotsById.get(robotId))
      .filter((robot): robot is Robot => Boolean(robot))
      .map((robot) => worldToScreen(robot.position, transform));
    if (points.length < 2) continue;
    context.beginPath();
    points.forEach((point, index) => {
      if (index === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.closePath();
    context.stroke();
  }
  context.restore();
}

function buildConflictIndex(conflicts: readonly Conflict[]): Set<string> {
  const involved = new Set<string>();
  for (const conflict of conflicts) {
    for (const robotId of conflict.robot_ids) involved.add(robotId);
  }
  return involved;
}
