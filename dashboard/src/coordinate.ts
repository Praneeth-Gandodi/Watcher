import type { Position2D, Robot, WorldState } from "./types";

export interface Camera {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface WorldTransform {
  scale: number;
  originX: number;
  originY: number;
}

export function getWorldTransform(
  world: WorldState,
  viewport: Viewport,
  camera: Camera,
): WorldTransform {
  const scale = Math.min(viewport.width / world.width_m, viewport.height / world.height_m) * camera.zoom;
  return {
    scale,
    originX: viewport.width / 2 + camera.offsetX - (world.width_m * scale) / 2,
    originY: viewport.height / 2 + camera.offsetY + (world.height_m * scale) / 2,
  };
}

export function worldToScreen(
  position: Position2D,
  transform: WorldTransform,
): Position2D {
  return {
    x: transform.originX + position.x * transform.scale,
    y: transform.originY - position.y * transform.scale,
  };
}

export function screenToWorld(
  position: Position2D,
  transform: WorldTransform,
): Position2D {
  return {
    x: (position.x - transform.originX) / transform.scale,
    y: (transform.originY - position.y) / transform.scale,
  };
}

export function findRobotAtScreenPosition(
  robots: Robot[],
  pointer: Position2D,
  world: WorldState,
  viewport: Viewport,
  camera: Camera,
  hitRadius = 16,
): Robot | null {
  const transform = getWorldTransform(world, viewport, camera);
  return robots.find((robot) => {
    const point = worldToScreen(robot.position, transform);
    return Math.hypot(point.x - pointer.x, point.y - pointer.y) <= hitRadius;
  }) ?? null;
}
