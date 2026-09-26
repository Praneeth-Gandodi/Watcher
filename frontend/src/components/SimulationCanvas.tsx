/**
 * The 2D map: one canvas, one paint loop, zero robot DOM nodes.
 *
 * The canvas owns the paint loop and the camera; React owns the props, so a
 * poll updates the data and the next frame picks it up. Interactions:
 *
 * - click inside a robot's *full* footprint to select it
 * - drag empty floor to pan, wheel to zoom around the cursor
 * - `F` re-fits the world, `Escape` clears the selection
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { SnapshotResponse, TelemetryResponse } from "../api/types";
import { actionColor } from "../styles/palette";
import { createCamera, fitWorld, panBy, zoomAt, type Camera } from "../render/camera";
import { cellUnderPointer, pickRobot, type PickableRobot } from "../render/hitTest";
import { drawScene, type SceneInput, type SmoothedAnchor } from "../render/scene";

export interface SimulationCanvasProps {
  snapshot: SnapshotResponse | null;
  telemetry: TelemetryResponse | null;
  selectedRobotId: string | null;
  onSelectRobot: (robotId: string | null) => void;
  onPlaceTask?: (cell: { cellX: number; cellY: number }) => void;
  placingTask: boolean;
  showFootprints: boolean;
  showRoutes: boolean;
  showTrails: boolean;
  showLabels: boolean;
  showTaskMarkers: boolean;
  showConflictCells: boolean;
  running: boolean;
  speed: number;
  now: number;
}

interface Size {
  width: number;
  height: number;
}

const MIN_CANVAS = 200;
/**
 * Smoothing time constant for a moving body, in milliseconds.
 *
 * The backend is polled a few times a second, so drawing the sampled position
 * directly would make a robot step cell to cell, and projecting by wall time
 * alone would make it twitch whenever a request came back late. Following the
 * authoritative position with this constant removes both: the drawn body
 * accelerates and settles instead of snapping, and it always converges on the
 * cell the backend actually committed to.
 */
const SMOOTHING_TAU_MS = 70;


export function SimulationCanvas(props: SimulationCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const pickableRef = useRef<PickableRobot[]>([]);
  const [size, setSize] = useState<Size>({ width: MIN_CANVAS, height: MIN_CANVAS });
  const [camera, setCamera] = useState<Camera>(createCamera);
  const [hoverRobotId, setHoverRobotId] = useState<string | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  // Latest props for the paint loop, which must not restart on every poll.
  const latest = useRef(props);
  latest.current = props;
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const userAdjustedRef = useRef(false);
  const reportedRef = useRef(false);

  const world = props.snapshot?.world;


  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({
        width: Math.max(MIN_CANVAS, Math.floor(entry.contentRect.width)),
        height: Math.max(MIN_CANVAS, Math.floor(entry.contentRect.height)),
      });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Fit whenever the world shape or the canvas size changes, until the user
  // takes the camera themselves. Keyed on the dimensions, not on the world
  // object: telemetry replaces that object on every poll, and refitting each
  // time would keep resetting the view the user just set up.
  const columns = world?.columns ?? 0;
  const rows = world?.rows ?? 0;
  useEffect(() => {
    if (columns === 0 || userAdjustedRef.current) return;
    setCamera(fitWorld(columns, rows, size));
  }, [columns, rows, size.width, size.height]);

  // The paint loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(size.width * ratio);
    canvas.height = Math.floor(size.height * ratio);

    // The sample the current telemetry came from, and the wall time it landed.
    // Simulated seconds since then drive the client-side projection.
    let sampleSimS = 0;
    let sampleWallMs = latest.current.now;
    let lastFrameMs = sampleWallMs;
    /** Smoothed footprint corners per robot, in world cells. */
    const smooth = new Map<string, SmoothedAnchor>();

    let frame = 0;
    const render = (): void => {
      const current = latest.current;
      const wallMs = current.now;
      const nowS = current.telemetry?.simulation_time_s ?? 0;

      if (nowS !== sampleSimS) {
        sampleSimS = nowS;
        sampleWallMs = wallMs;
      }
      // How much simulated time to project past the last sample. A robot is
      // only ever pushed forward along the trajectory the backend published,
      // and only while the backend says it is actually travelling: a held,
      // waiting, charging, or failed robot is at its committed cell, so
      // projecting it would drive the body straight through whoever is holding
      // it. That is the difference between "a robot yielded" and "robots drove
      // through each other".
      const simSinceSample = Math.max(0, nowS - sampleSimS);
      const gapS = Math.max(0, wallMs - sampleWallMs) / 1000;
      const deltaS = simSinceSample + (current.running ? gapS * current.speed : 0);

      const frameMs = Math.min(64, Math.max(1, wallMs - lastFrameMs));
      lastFrameMs = wallMs;
      // A frame-rate-independent smoothing factor.
      const alpha = 1 - Math.exp(-frameMs / SMOOTHING_TAU_MS);

      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const input: SceneInput = {
        viewport: sizeRef.current,
        camera: cameraRef.current,
        snapshot: current.snapshot,
        telemetry: current.telemetry,
        deltaS,
        wallClockS: wallMs / 1000,
        selectedRobotId: current.selectedRobotId,
        showFootprints: current.showFootprints,
        showRoutes: current.showRoutes,
        showTrails: current.showTrails,
        showLabels: current.showLabels,
        showTaskMarkers: current.showTaskMarkers,
        showConflictCells: current.showConflictCells,
        smooth,
        alpha,
      };
      try {
        pickableRef.current = drawScene(context, input, wallMs / 1000).pickable;
      } catch (cause) {
        // One bad frame must never kill the map. Report it once and keep going,
        // otherwise the canvas silently freezes until the page is reloaded.
        if (!reportedRef.current) {
          reportedRef.current = true;
          console.error("map frame failed", cause);
        }
      }
      frame = window.requestAnimationFrame(render);
    };

    frame = window.requestAnimationFrame(render);
    return () => window.cancelAnimationFrame(frame);
  }, [size.width, size.height]);

  const pointFrom = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const bounds = canvas.getBoundingClientRect();
    return { x: clientX - bounds.left, y: clientY - bounds.top };
  }, []);

  const hitRobot = useCallback(
    (point: { x: number; y: number }) =>
      pickableRef.current.length === 0
        ? null
        : pickRobot(pickableRef.current, cameraRef.current, point),
    [],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>) => {
      const point = pointFrom(event.clientX, event.clientY);
      if (!point) return;
      // Picking a task target wins over selection. Otherwise a click aimed at a
      // cell that happens to hold a robot selected that robot instead of
      // registering the pick, which is what made the control feel dead.
      if (latest.current.placingTask) {
        const snapshot = latest.current.snapshot;
        if (!snapshot) return;
        const cell = cellUnderPointer(
          cameraRef.current,
          point,
          snapshot.world.columns,
          snapshot.world.rows,
        );
        if (cell) latest.current.onPlaceTask?.(cell);
        return;
      }
      const hit = hitRobot(point);
      if (hit !== null) {
        latest.current.onSelectRobot(hit.robotId);
        return;
      }
      dragRef.current = { x: event.clientX, y: event.clientY };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [hitRobot, pointFrom],
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLCanvasElement>) => {
      const drag = dragRef.current;
      if (drag) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (dx !== 0 || dy !== 0) {
          drag.x = event.clientX;
          drag.y = event.clientY;
          userAdjustedRef.current = true;
          const snapshot = latest.current.snapshot;
          if (snapshot) {
            setCamera((current) =>
              panBy(
                current,
                dx,
                dy,
                sizeRef.current,
                snapshot.world.columns,
                snapshot.world.rows,
              ),
            );
          }
        }
        return;
      }
      const point = pointFrom(event.clientX, event.clientY);
      if (point) setHoverRobotId(hitRobot(point)?.robotId ?? null);
    },
    [hitRobot, pointFrom],
  );

  const handlePointerUp = useCallback((event: ReactPointerEvent<HTMLCanvasElement>) => {
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  /**
   * Wheel handling scoped to the map.
   *
   * The listener is on the document because a gesture can start on an overlay
   * that sits above the canvas, but the gesture is only claimed when it lands
   * inside this map: `preventDefault` there and nowhere else. Over the map the
   * wheel zooms the map; anywhere else on the page the browser's own zoom keeps
   * working, which is what makes the two regions behave differently.
   */
  useEffect(() => {
    const pane = wrapperRef.current;
    if (!pane) return;
    const onWheel = (event: WheelEvent): void => {
      if (!pane.contains(event.target as Node)) return;
      const snapshot = latest.current.snapshot;
      if (!snapshot) return;
      const point = pointFrom(event.clientX, event.clientY);
      if (!point) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      userAdjustedRef.current = true;
      setCamera((current) =>
        zoomAt(
          current,
          point.x,
          point.y,
          factor,
          sizeRef.current,
          snapshot.world.columns,
          snapshot.world.rows,
        ),
      );
    };
    document.addEventListener("wheel", onWheel, { passive: false });
    return () => document.removeEventListener("wheel", onWheel);
  }, [pointFrom]);

  /**
   * A non-passive wheel listener.
   *
   * React attaches its own `wheel` handler as a passive listener on the root, so
   * `preventDefault()` from `onWheel` is ignored and the browser zooms or
   * scrolls the whole page. Adding the listener directly on the canvas with
   * `{ passive: false }` is the only way to claim the gesture, which is what
   * keeps the wheel over the map zooming the map and nothing else.
   */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault();
      const point = pointFrom(event.clientX, event.clientY);
      const snapshot = latest.current.snapshot;
      if (!point || !snapshot) return;
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      userAdjustedRef.current = true;
      setCamera((current) =>
        zoomAt(
          current,
          point.x,
          point.y,
          factor,
          sizeRef.current,
          snapshot.world.columns,
          snapshot.world.rows,
        ),
      );
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [pointFrom]);

  const fit = useCallback(() => {
    const snapshot = latest.current.snapshot;
    if (snapshot) {
      setCamera(fitWorld(snapshot.world.columns, snapshot.world.rows, sizeRef.current));
    }
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLSelectElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (event.key === "f" || event.key === "F") fit();
      if (event.key === "Escape") latest.current.onSelectRobot(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fit]);

  const legend = useMemo(() => legendEntries(props.telemetry), [props.telemetry]);

  return (
    <div className="map" ref={wrapperRef}>
      <canvas
        ref={canvasRef}
        className={props.placingTask ? "map-canvas map-canvas--placing" : "map-canvas"}
        style={{ width: size.width, height: size.height }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => {
          dragRef.current = null;
          setHoverRobotId(null);
        }}
      />
      <MapOverlay
        hoverRobotId={hoverRobotId}
        selectedRobotId={props.selectedRobotId}
        placingTask={props.placingTask}
        legend={legend}
        onFit={fit}
      />
    </div>
  );
}

interface LegendEntry {
  label: string;
  count: number;
  color: string;
}

function legendEntries(telemetry: TelemetryResponse | null): LegendEntry[] {
  if (!telemetry) return [];
  return Object.entries(telemetry.counts_by_action)
    .filter(([, count]) => count > 0)
    .map(([action, count]) => ({ label: action, count, color: actionColor(action) }))
    .sort((left, right) => right.count - left.count);
}

interface MapOverlayProps {
  hoverRobotId: string | null;
  selectedRobotId: string | null;
  placingTask: boolean;
  legend: LegendEntry[];
  onFit: () => void;
}

function MapOverlay(props: MapOverlayProps) {
  return (
    <>
      <div className="map-corner map-corner--tl">
        <button className="ghost-button" type="button" onClick={props.onFit} title="Fit world (F)">
          FIT [F]
        </button>
        {props.placingTask ? (
          <span className="map-hint map-hint--warn">CLICK A FREE CELL TO PLACE THE TASK</span>
        ) : null}
        {props.hoverRobotId ? (
          <span className="map-hint">{props.hoverRobotId.toUpperCase()}</span>
        ) : null}
        {props.selectedRobotId ? (
          <span className="map-hint map-hint--selected">
            {props.selectedRobotId.toUpperCase()} SELECTED
          </span>
        ) : null}
      </div>
      <div className="map-corner map-corner--bl">
        <ul className="legend">
          {props.legend.map((entry) => (
            <li key={entry.label}>
              <span className="legend-dot" style={{ background: entry.color }} />
              <span className="legend-label">{entry.label}</span>
              <span className="legend-count">{entry.count}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
