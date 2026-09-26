/**
 * The console palette and its themes.
 *
 * One source of truth so a colour always means the same thing across the map,
 * the panels, the event log, and the charts. Colours communicate state; they are
 * never decorative.
 *
 * `PALETTE` is deliberately a *mutable* object rather than a frozen constant.
 * Every one of its readers -- the floor, the routes, the sprites, the charts --
 * reads it while drawing, never at module load, so switching a theme is a single
 * assignment and the very next animation frame picks it up. The CSS custom
 * properties are written from the same object by `applyTheme`, which is what
 * keeps the DOM and the canvas from ever disagreeing about a colour.
 */

export interface Palette {
  background: string;
  panel: string;
  panelRaised: string;
  panelSunken: string;
  grid: string;
  gridMajor: string;
  text: string;
  textDim: string;
  textFaint: string;

  safe: string;
  info: string;
  warning: string;
  conflict: string;
  failure: string;
  selected: string;

  floor: string;
  floorAlt: string;
  obstacle: string;
  obstacleTop: string;
  obstacleEdge: string;
  charging: string;
  workstation: string;
  resource: string;
  deadzone: string;

  route: string;
  routeActive: string;
  routeDone: string;
  trail: string;
  conflictCell: string;
  deadlock: string;
}

const INDUSTRIAL: Palette = {
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
};

/**
 * A warm CRT terminal. The same semantic contract -- green healthy, yellow
 * waiting, red conflict -- but pushed toward amber phosphor, which reads as
 * "single-screen control room" rather than "general dashboard". Contrast on the
 * text steps is higher than the industrial default, so it is easier to read on a
 * projector.
 */
const AMBER: Palette = {
  background: "#0A0704",
  panel: "#15100A",
  panelRaised: "#1D160D",
  panelSunken: "#0C0906",
  grid: "#2E2317",
  gridMajor: "#40311F",
  text: "#F5E3C4",
  textDim: "#A88A63",
  textFaint: "#6B563C",

  safe: "#FFB627",
  info: "#63D2FF",
  warning: "#FF8A3D",
  conflict: "#FF5A45",
  failure: "#FF3B2F",
  selected: "#FFE082",

  floor: "#120D08",
  floorAlt: "#161009",
  obstacle: "#241A11",
  obstacleTop: "#33261A",
  obstacleEdge: "#46341F",
  charging: "#63D2FF",
  workstation: "#FFB627",
  resource: "#C79BFF",
  deadzone: "#3A1C14",

  route: "#6B4A22",
  routeActive: "#FFB627",
  routeDone: "#3A2A18",
  trail: "#FFB627",
  conflictCell: "#FF5A45",
  deadlock: "#FF3B2F",
};

/**
 * A cool, clinical scheme. Bluer and flatter than the industrial default, with
 * the dim text steps pushed further apart for long reading sessions. Useful on a
 * bright screen, where the green-on-black of the default washes out.
 */
const ICE: Palette = {
  background: "#070A0F",
  panel: "#0D131B",
  panelRaised: "#131B25",
  panelSunken: "#080C12",
  grid: "#1D2833",
  gridMajor: "#2A3947",
  text: "#DFE9F2",
  textDim: "#8497A8",
  textFaint: "#55677A",

  safe: "#4ADE80",
  info: "#60A5FA",
  warning: "#FBBF24",
  conflict: "#F87171",
  failure: "#EF4444",
  selected: "#A3E635",

  floor: "#0A1017",
  floorAlt: "#0C131B",
  obstacle: "#16202B",
  obstacleTop: "#1F2C39",
  obstacleEdge: "#2C3E4F",
  charging: "#60A5FA",
  workstation: "#FBBF24",
  resource: "#A78BFA",
  deadzone: "#2A1620",

  route: "#2C4A5E",
  routeActive: "#60A5FA",
  routeDone: "#1B2A36",
  trail: "#4ADE80",
  conflictCell: "#F87171",
  deadlock: "#EF4444",
};

export interface Theme {
  id: string;
  label: string;
  /** One-line description, shown in the switcher's tooltip. */
  note: string;
  palette: Palette;
}

export const THEMES: Theme[] = [
  {
    id: "industrial",
    label: "INDUSTRIAL",
    note: "Green-on-black factory console. The default.",
    palette: INDUSTRIAL,
  },
  {
    id: "amber",
    label: "AMBER CRT",
    note: "Warm phosphor terminal. Highest text contrast; good on a projector.",
    palette: AMBER,
  },
  {
    id: "ice",
    label: "ICE",
    note: "Cool clinical blue. Easier to read on a bright screen.",
    palette: ICE,
  },
];

/** The live palette. Reassigned in place by `applyTheme`. */
export const PALETTE: Palette = { ...INDUSTRIAL };

export const DEFAULT_THEME_ID = "industrial";

export function themeById(id: string): Theme {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0]!;
}

/**
 * Switch the whole console to a theme.
 *
 * The canvas reads `PALETTE` as it draws, so copying the new values in is enough
 * for the map. The CSS custom properties are then written from that same object
 * so the panels cannot end up on the old theme.
 */
export function applyTheme(id: string): Theme {
  const theme = themeById(id);
  Object.assign(PALETTE, theme.palette);
  if (typeof document !== "undefined") {
    const root = document.documentElement;
    root.dataset.theme = theme.id;
    const variables: Record<string, string> = {
      "--background": PALETTE.background,
      "--panel": PALETTE.panel,
      "--panel-raised": PALETTE.panelRaised,
      "--panel-sunken": PALETTE.panelSunken,
      "--grid": PALETTE.grid,
      "--grid-major": PALETTE.gridMajor,
      "--text": PALETTE.text,
      "--text-dim": PALETTE.textDim,
      "--text-faint": PALETTE.textFaint,
      "--safe": PALETTE.safe,
      "--info": PALETTE.info,
      "--warning": PALETTE.warning,
      "--conflict": PALETTE.conflict,
      "--failure": PALETTE.failure,
      "--selected": PALETTE.selected,
      "--resource": PALETTE.resource,
      "--charging": PALETTE.charging,
      "--workstation": PALETTE.workstation,
      "--route-active": PALETTE.routeActive,
      "--route-done": PALETTE.routeDone,
      "--trail": PALETTE.trail,
    };
    for (const [name, value] of Object.entries(variables)) {
      root.style.setProperty(name, value);
    }
  }
  return theme;
}

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
