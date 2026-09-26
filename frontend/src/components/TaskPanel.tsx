/**
 * The task panel: every task, its assignment, and a form that submits a new
 * one to the backend.
 *
 * Creating a task is a canonical `CreateTaskCommand`, so the same negotiation,
 * allocation, and planning path runs as for the scenario's own tasks. The
 * backend answers with the events it produced, which the event log shows.
 */

import { useState } from "react";
import type { SnapshotResponse } from "../api/types";
import { priorityColor, priorityLabel } from "../styles/palette";
import { formatCoordinate, formatSeconds, shortRobotId } from "../state/format";

export interface TaskPanelProps {
  snapshot: SnapshotResponse | null;
  placingTask: boolean;
  busy: boolean;
  /** The cell chosen on the map, held by the shell so the map and form agree. */
  cell: { cellX: number; cellY: number } | null;
  onBeginPlacing: () => void;
  onCancelPlacing: () => void;
  onCellChange: (cell: { cellX: number; cellY: number } | null) => void;
  onCreateTask: (input: {
    taskId: string;
    cellX: number;
    cellY: number;
    priority: number;
    requiredCapability: string;
    estimatedDurationS: number;
  }) => void;
}

const CAPABILITIES = ["", "transport", "pick", "tug", "inspect", "deliver"];

export function TaskPanel(props: TaskPanelProps) {
  const [taskId, setTaskId] = useState("");
  const [priority, setPriority] = useState(3);
  const [capability, setCapability] = useState("");
  const [duration, setDuration] = useState(8);
  const [error, setError] = useState<string | null>(null);

  const tasks = props.snapshot?.tasks ?? [];
  const sorted = [...tasks].sort((left, right) => {
    if (left.status === "completed" && right.status !== "completed") return 1;
    if (right.status === "completed" && left.status !== "completed") return -1;
    return right.priority - left.priority;
  });

  const submit = (): void => {
    const identifier = taskId.trim();
    if (identifier === "") {
      setError("task id required");
      return;
    }
    // The contract's identifier pattern is lowercase, hyphen-separated.
    if (!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(identifier)) {
      setError("task id must be lowercase words, e.g. task-7");
      return;
    }
    const target = props.cell;
    if (!target) {
      setError("pick a target cell on the map first");
      return;
    }
    setError(null);
    props.onCreateTask({
      taskId: identifier,
      cellX: target.cellX,
      cellY: target.cellY,
      priority,
      requiredCapability: capability,
      estimatedDurationS: duration,
    });
    setTaskId("");
    props.onCellChange(null);
  };

  return (
    <div className="panel-body task-panel">
      <section className="task-form">
        <div className="field">
          <label htmlFor="task-id">TASK ID</label>
          <input
            id="task-id"
            className="input"
            value={taskId}
            placeholder="task-7"
            onChange={(event) => setTaskId(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="task-priority">PRIORITY</label>
          <select
            id="task-priority"
            className="input"
            value={priority}
            onChange={(event) => setPriority(Number(event.target.value))}
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value} {priorityLabel(value)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="task-capability">REQUIRES</label>
          <select
            id="task-capability"
            className="input"
            value={capability}
            onChange={(event) => setCapability(event.target.value)}
          >
            {CAPABILITIES.map((value) => (
              <option key={value || "any"} value={value}>
                {value === "" ? "any" : value}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="task-duration">EST SECONDS</label>
          <input
            id="task-duration"
            className="input"
            type="number"
            min={1}
            max={600}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
          />
        </div>
        <div className="field field--wide">
          <span className="field-label">TARGET</span>
          <span className={props.cell ? "field-value" : "field-value field-value--unset"}>
            {props.cell ? `${props.cell.cellX}, ${props.cell.cellY}` : "not set — use PICK CELL"}
            {props.placingTask ? <span className="field-hint"> then click the map</span> : null}
          </span>
        </div>
        <div className="button-row">
          <button
            className="button button--primary"
            type="button"
            disabled={props.busy}
            onClick={submit}
          >
            SUBMIT TASK
          </button>
          <button
            className={
              props.placingTask ? "button button--chip button--chip-warn" : "button button--chip"
            }
            type="button"
            onClick={props.onBeginPlacing}
            disabled={props.placingTask}
            title="Choose the target cell on the map"
          >
            PICK CELL
          </button>
          {props.placingTask || props.cell ? (
            <button
              className="button button--chip"
              type="button"
              onClick={() => {
                props.onCellChange(null);
                props.onCancelPlacing();
              }}
            >
              CLEAR TARGET
            </button>
          ) : null}
        </div>
        {error ? <p className="form-error">{error}</p> : null}
      </section>

      <ul className="task-list">
        {sorted.map((task) => (
          <li key={task.task_id} className="task-row">
            <span className="task-priority" style={{ color: priorityColor(task.priority) }}>
              P{task.priority}
            </span>
            <span className="task-name">{task.task_id}</span>
            <span className="task-target">{formatCoordinate(task.target.x, task.target.y)}</span>
            <span className="task-owner">
              {task.assigned_robot_id ? shortRobotId(task.assigned_robot_id) : "--"}
            </span>
            <span className="task-status">{task.status}</span>
            <span className="task-duration">{formatSeconds(task.estimated_duration_s)}</span>
          </li>
        ))}
        {sorted.length === 0 ? <p className="muted">no tasks yet</p> : null}
      </ul>
    </div>
  );
}
