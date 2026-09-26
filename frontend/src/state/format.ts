/**
 * Small formatting helpers shared by the panels.
 *
 * Every value shown comes from the backend, so these only change how it is
 * spelled: no derived metrics and no invented units.
 */

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${value.toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toFixed(digits);
}

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return Math.round(value).toString();
}

export function formatCoordinate(x: number | null | undefined, y: number | null | undefined): string {
  if (x === null || x === undefined || y === null || y === undefined) return "--";
  return `${Math.floor(x)}, ${Math.floor(y)}`;
}

/** `robot-007` -> `R07`, for the dense places where space is scarce. */
export function shortRobotId(robotId: string): string {
  return robotId.replace(/^robot-/, "R");
}
