import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { findRobotAtScreenPosition, getWorldTransform, worldToScreen, type Camera } from "./coordinate";
import type { DeadlockCycle } from "./state";
import type { Robot, SimulationSnapshot } from "./types";

interface WorldCanvasProps {
  snapshot: SimulationSnapshot | null;
  deadlockCycles: DeadlockCycle[];
  selectedRobotId: string | null;
  onSelectRobot: (robotId: string) => void;
}

interface ViewportSize {
  width: number;
  height: number;
}

const robotColors: Record<Robot["status"], string> = {
  idle: "#7dd3fc",
  active: "#38bdf8",
  blocked: "#fbbf24",
  charging: "#a78bfa",
  degraded: "#fb923c",
  failed: "#fb7185",
  offline: "#94a3b8",
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function drawRobotMarker(
  context: CanvasRenderingContext2D,
  robot: Robot,
  point: { x: number; y: number },
  selected: boolean,
  scale: number,
): void {
  const radius = clamp(5 + scale * 0.8, 5, 10);
  const color = robotColors[robot.status];
  context.save();
  context.translate(point.x, point.y);
  context.beginPath();

  if (robot.status === "failed" || robot.status === "offline") {
    context.moveTo(0, -radius - 1);
    context.lineTo(radius + 1, 0);
    context.lineTo(0, radius + 1);
    context.lineTo(-radius - 1, 0);
    context.closePath();
  } else if (robot.status === "charging") {
    context.rect(-radius, -radius, radius * 2, radius * 2);
  } else if (robot.status === "blocked") {
    context.moveTo(0, -radius - 1);
    context.lineTo(radius + 1, radius + 1);
    context.lineTo(-radius - 1, radius + 1);
    context.closePath();
  } else {
    context.arc(0, 0, radius, 0, Math.PI * 2);
  }

  context.fillStyle = color;
  context.globalAlpha = robot.status === "offline" ? 0.55 : 0.95;
  context.fill();
  context.globalAlpha = 1;
  context.lineWidth = selected ? 3 : 1.5;
  context.strokeStyle = selected ? "#ffffff" : "#07111f";
  context.stroke();

  if (robot.status === "failed") {
    context.strokeStyle = "#450a0a";
    context.lineWidth = 1.5;
    context.beginPath();
    context.moveTo(-3, -3);
    context.lineTo(3, 3);
    context.moveTo(3, -3);
    context.lineTo(-3, 3);
    context.stroke();
  }

  if (selected || scale > 3.4) {
    context.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
    context.fillStyle = "#dbeafe";
    context.textAlign = "left";
    context.textBaseline = "bottom";
    context.fillText(robot.robot_id, radius + 5, 0);
  }
  context.restore();
}

export default function WorldCanvas({
  snapshot,
  deadlockCycles,
  selectedRobotId,
  onSelectRobot,
}: WorldCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const drawFrameRef = useRef<number | undefined>(undefined);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const [viewport, setViewport] = useState<ViewportSize>({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ zoom: 1, offsetX: 0, offsetY: 0 });

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => {
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || viewport.width === 0 || viewport.height === 0) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const draw = () => {
      const ratio = window.devicePixelRatio || 1;
    canvas.width = viewport.width * ratio;
    canvas.height = viewport.height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, viewport.width, viewport.height);
    context.fillStyle = "#091421";
    context.fillRect(0, 0, viewport.width, viewport.height);

    if (!snapshot) {
      context.strokeStyle = "rgba(125, 211, 252, 0.08)";
      context.lineWidth = 1;
      const gridSize = 42;
      for (let x = 0; x < viewport.width; x += gridSize) {
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, viewport.height);
        context.stroke();
      }
      for (let y = 0; y < viewport.height; y += gridSize) {
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(viewport.width, y);
        context.stroke();
      }
      return;
    }

    const world = snapshot.world;
    const transform = getWorldTransform(world, viewport, camera);
    const left = transform.originX;
    const top = transform.originY - world.height_m * transform.scale;
    const worldWidth = world.width_m * transform.scale;
    const worldHeight = world.height_m * transform.scale;

    context.fillStyle = "#0b1828";
    context.fillRect(left, top, worldWidth, worldHeight);

    if (transform.scale > 1.8) {
      context.strokeStyle = "rgba(125, 211, 252, 0.09)";
      context.lineWidth = 1;
      for (let column = 0; column <= world.columns; column += 1) {
        const x = left + column * world.cell_size_m * transform.scale;
        context.beginPath();
        context.moveTo(x, top);
        context.lineTo(x, top + worldHeight);
        context.stroke();
      }
      for (let row = 0; row <= world.rows; row += 1) {
        const y = top + worldHeight - row * world.cell_size_m * transform.scale;
        context.beginPath();
        context.moveTo(left, y);
        context.lineTo(left + worldWidth, y);
        context.stroke();
      }
    }

    for (const cell of world.cells) {
      const cellSize = world.cell_size_m * transform.scale;
      const x = left + cell.cell_x * cellSize;
      const y = top + worldHeight - (cell.cell_y + 1) * cellSize;
      const colors: Record<string, string> = {
        obstacle: "#334155",
        resource: "#0e7490",
        charging: "#1d4ed8",
        workstation: "#a16207",
        deadzone: "#4c1d95",
        free: "#0b1828",
      };
      context.fillStyle = colors[cell.cell_type] ?? colors.free;
      context.fillRect(x + 1, y + 1, Math.max(1, cellSize - 2), Math.max(1, cellSize - 2));
    }

    context.strokeStyle = "rgba(125, 211, 252, 0.4)";
    context.lineWidth = 1.5;
    context.strokeRect(left, top, worldWidth, worldHeight);

    for (const route of snapshot.routes) {
      if (route.waypoints.length < 2) continue;
      context.beginPath();
      route.waypoints.forEach((waypoint, index) => {
        const point = worldToScreen(waypoint, transform);
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.strokeStyle = route.status === "blocked" ? "rgba(251, 191, 36, 0.75)" : "rgba(56, 189, 248, 0.55)";
      context.lineWidth = route.status === "blocked" ? 2.5 : 2;
      context.stroke();
      route.waypoints.forEach((waypoint) => {
        const point = worldToScreen(waypoint, transform);
        context.fillStyle = "#bae6fd";
        context.beginPath();
        context.arc(point.x, point.y, 3, 0, Math.PI * 2);
        context.fill();
      });
    }

    for (const conflict of snapshot.conflicts) {
      const point = worldToScreen(conflict.position, transform);
      const color = conflict.severity === "critical" ? "#fb7185" : conflict.severity === "warning" ? "#fbbf24" : "#38bdf8";
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.beginPath();
      context.arc(point.x, point.y, 10, 0, Math.PI * 2);
      context.stroke();
      context.beginPath();
      context.moveTo(point.x - 5, point.y - 5);
      context.lineTo(point.x + 5, point.y + 5);
      context.moveTo(point.x + 5, point.y - 5);
      context.lineTo(point.x - 5, point.y + 5);
      context.stroke();
    }

    const robotById = new Map(snapshot.robots.map((robot) => [robot.robot_id, robot]));
    for (const cycle of deadlockCycles) {
      const points = cycle.robotIds
        .map((id) => robotById.get(id))
        .filter((robot): robot is Robot => Boolean(robot))
        .map((robot) => worldToScreen(robot.position, transform));
      if (points.length < 2) continue;
      context.beginPath();
      points.forEach((point, index) => {
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.closePath();
      context.strokeStyle = "rgba(244, 63, 94, 0.85)";
      context.lineWidth = 2;
      context.setLineDash([5, 5]);
      context.stroke();
      context.setLineDash([]);
    }

      for (const robot of snapshot.robots) {
        const point = worldToScreen(robot.position, transform);
        drawRobotMarker(context, robot, point, robot.robot_id === selectedRobotId, transform.scale);
      }
    };

    if (drawFrameRef.current !== undefined) cancelAnimationFrame(drawFrameRef.current);
    drawFrameRef.current = requestAnimationFrame(() => {
      drawFrameRef.current = undefined;
      draw();
    });
    return () => {
      if (drawFrameRef.current !== undefined) cancelAnimationFrame(drawFrameRef.current);
      drawFrameRef.current = undefined;
    };
  }, [camera, deadlockCycles, selectedRobotId, snapshot, viewport]);

  function selectAt(event: ReactPointerEvent<HTMLCanvasElement>): void {
    if (!snapshot || viewport.width === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const robot = findRobotAtScreenPosition(snapshot.robots, pointer, snapshot.world, viewport, camera);
    if (robot) onSelectRobot(robot.robot_id);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLCanvasElement>): void {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLCanvasElement>): void {
    const drag = dragRef.current;
    if (!drag) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    if (Math.abs(deltaX) + Math.abs(deltaY) > 2) drag.moved = true;
    drag.x = event.clientX;
    drag.y = event.clientY;
    setCamera((current) => ({ ...current, offsetX: current.offsetX + deltaX, offsetY: current.offsetY + deltaY }));
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLCanvasElement>): void {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag && !drag.moved) selectAt(event);
  }

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 0.9 : 1.1;
      setCamera((current) => ({ ...current, zoom: clamp(current.zoom * direction, 0.6, 4) }));
    };
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, []);

  return (
    <div className="map-stage" ref={frameRef}>
      <canvas
        ref={canvasRef}
        className="world-canvas"
        aria-label="Live 2D fleet world map. Use the robot roster to select a robot."
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => { dragRef.current = null; }}
      />
      <div className="map-overlay map-overlay-top">
        <span className="map-label">WORLD SPACE / 2D</span>
        <span className="map-scale">scroll to zoom · drag to pan</span>
      </div>
      <div className="map-overlay map-overlay-bottom">
        <span className="map-key"><i className="key-dot key-active" /> active</span>
        <span className="map-key"><i className="key-dot key-warning" /> attention</span>
        <span className="map-key"><i className="key-dot key-critical" /> critical</span>
        <button className="map-reset" type="button" onClick={() => setCamera({ zoom: 1, offsetX: 0, offsetY: 0 })}>Reset view</button>
      </div>
      {!snapshot && (
        <div className="map-empty-state" role="status">
          <span className="empty-state-mark">+</span>
          <strong>Awaiting canonical snapshot</strong>
          <span>Connect the runtime to populate the world map.</span>
        </div>
      )}
    </div>
  );
}
