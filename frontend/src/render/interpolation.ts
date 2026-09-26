/**
 * Smooth positions from the backend's timed trajectory.
 *
 * The simulation advances in discrete ticks, so a 400 ms poll would make a
 * robot jump cell to cell. The backend already publishes the timestamp of every
 * remaining placement in `trail`, so the client interpolates *along the
 * backend's own trajectory* between two samples. This is presentation only: the
 * path, the arrival times, and every decision still come from the backend. No
 * route is computed here.
 *
 * Coordinate convention, taken from the backend's `GridIndex`: a robot's
 * footprint of `width_cells x height_cells` is anchored at `(cell_x, cell_y)`
 * and occupies `grid[cell_y + h][cell_x + w]`. The published `position_x` /
 * `position_y` is the *centre of the anchor cell*, so the footprint's top-left
 * corner is always `anchor - 0.5` cells. Both the renderer and the hit test
 * read `left`/`top` from here, which is what keeps a click on a drawn pixel and
 * a click on the same footprint in agreement.
 */

import type { RobotTelemetry } from "../api/types";
import type { Heading } from "../assets/robots/sprite";

export interface AnchorSample {
  /** Anchor cell centre, in world cells (== metres). */
  anchorX: number;
  anchorY: number;
  /** Footprint top-left corner, in world cells. */
  left: number;
  top: number;
  heading: Heading;
  /** True while the projection sits between two published placements. */
  interpolated: boolean;
}

const EPSILON = 1e-6;

function headingFor(dx: number, dy: number, fallback: Heading): Heading {
  if (Math.abs(dx) < EPSILON && Math.abs(dy) < EPSILON) return fallback;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "east" : "west";
  return dy >= 0 ? "south" : "north";
}

function sample(anchorX: number, anchorY: number, heading: Heading, interpolated: boolean): AnchorSample {
  return { anchorX, anchorY, left: anchorX - 0.5, top: anchorY - 0.5, heading, interpolated };
}

/**
 * Project a robot `deltaS` simulated seconds past the sample the backend
 * produced, following the published trail.
 *
 * `nowS` is the backend's simulation time at the moment of the sample and
 * `deltaS` is the simulated time that has elapsed since, derived from the
 * simulation clock rather than the wall clock, so a paused or sped-up run
 * still interpolates correctly.
 */
export function sampleAnchor(
  robot: RobotTelemetry,
  nowS: number,
  deltaS: number,
): AnchorSample {
  const startX = robot.position_x;
  const startY = robot.position_y;

  if (deltaS <= 0 || robot.trail.length === 0) {
    return sample(startX, startY, trailHeading(robot), false);
  }

  const target = nowS + deltaS;
  let previousX = startX;
  let previousY = startY;
  let previousT = nowS;

  for (const [x, y, timestamp] of robot.trail) {
    if (timestamp >= target) {
      const span = timestamp - previousT;
      const ratio = span <= EPSILON ? 1 : Math.min(1, (target - previousT) / span);
      return sample(
        previousX + (x - previousX) * ratio,
        previousY + (y - previousY) * ratio,
        headingFor(x - previousX, y - previousY, "east"),
        true,
      );
    }
    previousX = x;
    previousY = y;
    previousT = timestamp;
  }

  // Past the last published placement: hold the final position rather than
  // overshooting into a cell the backend has not committed to.
  return sample(previousX, previousY, trailHeading(robot), true);
}

/** Heading implied by the next published placement; used for idle robots. */
export function trailHeading(robot: RobotTelemetry): Heading {
  const first = robot.trail[0];
  if (!first) return "east";
  return headingFor(first[0] - robot.position_x, first[1] - robot.position_y, "east");
}
