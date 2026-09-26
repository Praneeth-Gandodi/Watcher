/**
 * The console palette.
 *
 * One source of truth so a colour always means the same thing across the map,
 * the panels, the event log, and the charts. Colours communicate state; they are
 * never decorative.
 */

export const PALETTE = {
  background: "#0B0F0E",
  panel: "#111817",
  panelRaised: "#18201E",
  panelSunken: "#0A0E0D",
  grid: "#26302C",
  gridMajor: "#33403A",
  text: "#D7E2D9",
  textDim: "#7F9187",
  textFaint: "#4C5B54",

  safe: "#7CFF6B",
  info: "#54C7FF",
  warning: "#FFD166",
  conflict: "#FF6B5E",
  failure: "#FF3B30",
  selected: "#C6FF4A",

  floor: "#0E1413",
  floorAlt: "#101817",
  obstacle: "#1E2A26",
  obstacleTop: "#2B3A34",
  obstacleEdge: "#3A4C44",
  charging: "#54C7FF",
  workstation: "#FFD166",
  resource: "#B58CFF",
  deadzone: "#4A2A2A",

  route: "#3E7A6B",
  routeActive: "#54C7FF",
  routeDone: "#2C4A42",
  trail: "#7CFF6B",
  conflictCell: "#FF6B5E",
  deadlock: "#FF3B30",
} as const;

/** Semantic colour for a robot's backend status. */
export function statusColor(status: string): string {
  switch (status) {
    case "failed":
      return PALETTE.failure;
    case "blocked":
      return PALETTE.conflict;
    case "charging":
      return PALETTE.info;
    case "degraded":
      return PALETTE.warning;
    case "active":
      return PALETTE.safe;
    case "offline":
      return PALETTE.textFaint;
    default:
      return PALETTE.textDim;
  }
}

/** Semantic colour for the dashboard's derived action label. */
export function actionColor(action: string): string {
  switch (action) {
    case "MOVING":
      return PALETTE.safe;
    case "WAITING":
      return PALETTE.warning;
    case "BLOCKED":
      return PALETTE.conflict;
    case "CHARGING":
      return PALETTE.info;
    case "DEGRADED":
      return PALETTE.warning;
    case "FAILED":
      return PALETTE.failure;
    case "OFFLINE":
      return PALETTE.textFaint;
    case "NEGOTIATING":
      return PALETTE.info;
    case "REPLANNING":
      return PALETTE.selected;
    case "TASK_COMPLETED":
      return PALETTE.selected;
    default:
      return PALETTE.textDim;
  }
}

/** Battery band, using the backend's own threshold semantics. */
export function batteryColor(percent: number, low: number, critical: number): string {
  if (percent <= critical) return PALETTE.failure;
  if (percent <= low) return PALETTE.warning;
  return PALETTE.safe;
}

export function priorityColor(priority: number): string {
  if (priority >= 5) return PALETTE.failure;
  if (priority >= 4) return PALETTE.warning;
  if (priority >= 2) return PALETTE.info;
  return PALETTE.textDim;
}

export function priorityLabel(priority: number): string {
  if (priority >= 5) return "CRITICAL";
  if (priority >= 4) return "HIGH";
  if (priority >= 2) return "NORMAL";
  return "LOW";
}
