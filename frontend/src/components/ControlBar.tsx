/**
 * Transport controls and the display toggles.
 *
 * Every control is a thin wrapper over one backend command. The console drives
 * the backend's deterministic clock itself, so "Start" resumes the clock and
 * begins advancing ticks, and "Step" advances exactly one tick.
 */

import { SPEED_PRESETS } from "../state/useSimulation";

export interface ControlBarProps {
  running: boolean;
  speed: number;
  busy: string | null;
  connection: "connecting" | "online" | "offline";
  scenario: string;
  showFootprints: boolean;
  showRoutes: boolean;
  showTrails: boolean;
  showLabels: boolean;
  showTaskMarkers: boolean;
  showConflictCells: boolean;
  placingTask: boolean;
  /** Every task in the run is finished, so there is nothing left to animate. */
  runComplete: boolean;
  onStart: () => void;
  onPause: () => void;
  onStep: () => void;
  onSpeed: (multiplier: number) => void;
  onReset: () => void;
  onDispatch: () => void;
  onRandomize: () => void;
  onToggle: (key: DisplayKey, value: boolean) => void;
  onPlaceTask: () => void;
}

export type DisplayKey =
  | "showFootprints"
  | "showRoutes"
  | "showTrails"
  | "showLabels"
  | "showTaskMarkers"
  | "showConflictCells";

const DISPLAYS: { key: DisplayKey; label: string; hint: string }[] = [
  { key: "showRoutes", label: "ROUTES", hint: "Planned path per robot" },
  { key: "showTrails", label: "TRAILS", hint: "Remaining timed placements" },
  { key: "showFootprints", label: "FOOTPRINT", hint: "Debug: every occupied cell" },
  { key: "showLabels", label: "LABELS", hint: "Robot id above each body" },
  { key: "showTaskMarkers", label: "TASKS", hint: "Task crates on the floor" },
  { key: "showConflictCells", label: "CONFLICTS", hint: "Hatch overlapping robots" },
];

export function ControlBar(props: ControlBarProps) {
  const disabled = props.busy !== null;
  return (
    <div className="control-bar">
      <div className="control-group">
        <button
          className={props.running ? "button button--active" : "button button--primary"}
          type="button"
          onClick={props.onStart}
          disabled={disabled || props.running}
          title={
            props.runComplete
              ? "Every task in this run is finished. Start reloads the scenario with fresh work."
              : "Resume the simulation clock"
          }
        >
          {props.running ? "RUNNING" : props.runComplete ? "RESTART RUN" : "START"}
        </button>
        <button
          className="button"
          type="button"
          onClick={props.onPause}
          disabled={disabled || !props.running}
        >
          PAUSE
        </button>
        <button
          className="button"
          type="button"
          onClick={props.onStep}
          disabled={disabled || props.running}
          title="Advance exactly one deterministic tick"
        >
          STEP
        </button>
        <button className="button" type="button" onClick={props.onReset} disabled={disabled}>
          RESET
        </button>
        <button
          className="button"
          type="button"
          onClick={props.onDispatch}
          disabled={disabled}
          title="Negotiate and assign every task that still has no owner"
        >
          DISPATCH TASKS
        </button>
        <button
          className="button button--random"
          type="button"
          onClick={props.onRandomize}
          disabled={disabled}
          title="Release every open task and negotiate again with randomised bid costs. Allocation still awards each task to the cheapest bid."
        >
          RANDOM
        </button>
      </div>

      <div className="control-group">
        <span className="control-label">SPEED</span>
        {SPEED_PRESETS.map((preset) => (
          <button
            key={preset}
            className={props.speed === preset ? "button button--chip button--chip-on" : "button button--chip"}
            type="button"
            onClick={() => props.onSpeed(preset)}
            disabled={disabled}
          >
            {preset}x
          </button>
        ))}
      </div>

      <div className="control-group control-group--wrap">
        <span className="control-label">DISPLAY</span>
        {DISPLAYS.map((display) => (
          <button
            key={display.key}
            className={
              props[display.key] ? "button button--chip button--chip-on" : "button button--chip"
            }
            type="button"
            title={display.hint}
            onClick={() => props.onToggle(display.key, !props[display.key])}
          >
            {display.label}
          </button>
        ))}
        <button
          className={props.placingTask ? "button button--chip button--chip-warn" : "button button--chip"}
          type="button"
          onClick={props.onPlaceTask}
        >
          PLACE TASK
        </button>
      </div>

      <div className="control-group control-group--end">
        <span className={`pill pill--${props.connection}`}>{props.connection}</span>
        <span className="control-label">{props.scenario.toUpperCase()}</span>
        {props.runComplete ? (
          <span className="pill pill--complete" title="Every task in this run is complete">
            RUN COMPLETE
          </span>
        ) : null}
        {props.busy ? <span className="pill pill--busy">{props.busy}</span> : null}
      </div>
    </div>
  );
}
