import { useState } from "react";
import { Gauge, Pause, Play, RotateCcw, Send } from "lucide-react";

import type { SimulationSnapshot } from "../types";

const SPEEDS = [0.5, 1, 2, 4] as const;
const CAPABILITIES = ["transport", "pick", "tug", "inspect", "deliver"] as const;

export interface OperatorControlsProps {
  snapshot: SimulationSnapshot | null;
  pending: boolean;
  onPause: () => void;
  onResume: () => void;
  onReset: () => void;
  onSpeed: (multiplier: number) => void;
  onCreateTask: (task: {
    taskId: string;
    targetX: number;
    targetY: number;
    priority: number;
    capability: string;
    estimatedDurationS: number;
  }) => void;
}

export function OperatorControls({
  snapshot,
  pending,
  onPause,
  onResume,
  onReset,
  onSpeed,
  onCreateTask,
}: OperatorControlsProps) {
  const paused = snapshot?.metrics.extra_metrics.paused === 1;
  const runtimeSpeed = snapshot?.metrics.extra_metrics.simulation_speed_multiplier ?? 1;
  const [speed, setSpeed] = useState<number>(runtimeSpeed);

  const [taskId, setTaskId] = useState("");
  const [targetX, setTargetX] = useState("40");
  const [targetY, setTargetY] = useState("30");
  const [priority, setPriority] = useState("3");
  const [capability, setCapability] = useState<string>("transport");
  const [duration, setDuration] = useState("45");
  const [formError, setFormError] = useState<string | null>(null);

  function submitTask() {
    const identifier = taskId.trim();
    if (!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(identifier)) {
      setFormError("Task ID must be lowercase kebab-case, for example task-042.");
      return;
    }
    const x = Number(targetX);
    const y = Number(targetY);
    const seconds = Number(duration);
    if (!Number.isFinite(x) || x < 0 || !Number.isFinite(y) || y < 0) {
      setFormError("Target coordinates must be non-negative numbers in meters.");
      return;
    }
    if (!Number.isFinite(seconds) || seconds <= 0) {
      setFormError("Estimated duration must be greater than zero.");
      return;
    }
    const world = snapshot?.world;
    if (world && (x > world.width_m || y > world.height_m)) {
      setFormError(
        `Target must sit inside the world: 0–${world.width_m} m by 0–${world.height_m} m.`,
      );
      return;
    }
    setFormError(null);
    onCreateTask({
      taskId: identifier,
      targetX: x,
      targetY: y,
      priority: Number(priority),
      capability,
      estimatedDurationS: seconds,
    });
    setTaskId("");
  }

  return (
    <div className="controls">
      <div className="controls__group">
        <span className="kicker">Simulation</span>
        <div className="controls__row">
          <button
            type="button"
            className="button button--ghost"
            onClick={paused ? onResume : onPause}
            disabled={pending || !snapshot}
          >
            {paused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
            {paused ? "Resume" : "Pause"}
          </button>
          <button
            type="button"
            className="button button--ghost"
            onClick={onReset}
            disabled={pending || !snapshot}
          >
            <RotateCcw size={15} aria-hidden="true" />
            Reset seed
          </button>
        </div>
      </div>

      <label className="controls__group">
        <span className="kicker">
          <Gauge size={13} aria-hidden="true" /> Speed
        </span>
        <select
          value={String(speed)}
          disabled={pending || !snapshot}
          onChange={(event) => {
            const next = Number(event.target.value);
            setSpeed(next);
            onSpeed(next);
          }}
        >
          {SPEEDS.map((option) => (
            <option key={option} value={String(option)}>
              {option.toFixed(1)}×
            </option>
          ))}
        </select>
      </label>

      <div className="controls__group controls__group--grow">
        <span className="kicker">Create task</span>
        <div className="controls__row controls__row--form">
          <input
            type="text"
            value={taskId}
            placeholder="task-042"
            aria-label="Task identifier"
            onChange={(event) => setTaskId(event.target.value)}
          />
          <input
            type="number"
            value={targetX}
            step="1"
            min="0"
            aria-label="Target X in meters"
            onChange={(event) => setTargetX(event.target.value)}
          />
          <input
            type="number"
            value={targetY}
            step="1"
            min="0"
            aria-label="Target Y in meters"
            onChange={(event) => setTargetY(event.target.value)}
          />
          <select
            value={priority}
            aria-label="Task priority"
            onChange={(event) => setPriority(event.target.value)}
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={String(value)}>
                P{value}
              </option>
            ))}
          </select>
          <select
            value={capability}
            aria-label="Required capability"
            onChange={(event) => setCapability(event.target.value)}
          >
            {CAPABILITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <input
            type="number"
            value={duration}
            step="5"
            min="1"
            aria-label="Estimated duration in seconds"
            onChange={(event) => setDuration(event.target.value)}
          />
          <button
            type="button"
            className="button button--primary"
            onClick={submitTask}
            disabled={pending || !snapshot}
          >
            <Send size={15} aria-hidden="true" />
            Queue
          </button>
        </div>
        {formError ? (
          <p className="form-error" role="alert">
            {formError}
          </p>
        ) : null}
      </div>
    </div>
  );
}
