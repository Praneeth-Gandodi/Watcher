/**
 * The task panel: every task, its assignment, and a form that submits a new
 * one to the backend.
 *
 * Creating a task is a canonical `CreateTaskCommand`, so the same negotiation,
 * allocation, and planning path runs as for the scenario's own tasks. The
 * backend answers with the events it produced, which the event log shows.
 */

import { useRef, useState } from "react";
import type { SnapshotResponse } from "../api/types";
import { priorityColor, priorityLabel } from "../styles/palette";
import { formatCoordinate, formatSeconds, shortRobotId } from "../state/format";
import { Divider } from "./Divider";

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

/**
 * Column widths for the task table, in pixels.
 *
 * Each one is draggable and the width is applied to both the header and every
 * row from a single custom property, so the two can never drift out of
 * alignment. `flex-basis` rather than `width` lets a column shrink below its
 * preferred size when the dock is narrow, with the text ellipsised.
 */
interface TaskColumn {
  key: TaskColumnKey;
  label: string;
  width: number;
  min: number;
  max: number;
}

type TaskColumnKey =
  | "priority"
  | "id"
  | "needs"
  | "target"
  | "owner"
  | "status"
  | "eta";

const COLUMNS: TaskColumn[] = [
  { key: "priority", label: "PRI", width: 40, min: 30, max: 90 },
  { key: "id", label: "TASK ID", width: 110, min: 70, max: 260 },
  { key: "needs", label: "NEEDS", width: 120, min: 60, max: 240 },
  { key: "target", label: "TARGET", width: 80, min: 50, max: 160 },
  { key: "owner", label: "OWNER", width: 70, min: 50, max: 150 },
  { key: "status", label: "STATUS", width: 100, min: 60, max: 200 },
  { key: "eta", label: "ETA", width: 60, min: 40, max: 130 },
];

export function TaskPanel(props: TaskPanelProps) {
  const [taskId, setTaskId] = useState("");
  const [priority, setPriority] = useState(3);
  const [capability, setCapability] = useState("");
  const [duration, setDuration] = useState(8);
  const [error, setError] = useState<string | null>(null);
  const tableRef = useRef<HTMLDivElement | null>(null);
  const [widths, setWidth] = useState<Record<TaskColumnKey, number>>(() => ({
    priority: 40,
    id: 110,
    needs: 120,
    target: 80,
    owner: 70,
    status: 100,
    eta: 60,
  }));

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

      <div className="task-table" ref={tableRef}>
        <div
          className="task-list-head"
          style={{
            ["--col-priority" as string]: `${widths.priority}px`,
            ["--col-id" as string]: `${widths.id}px`,
            ["--col-needs" as string]: `${widths.needs}px`,
            ["--col-target" as string]: `${widths.target}px`,
            ["--col-owner" as string]: `${widths.owner}px`,
            ["--col-status" as string]: `${widths.status}px`,
            ["--col-eta" as string]: `${widths.eta}px`,
          }}
        >
          {COLUMNS.map((column, index) => (
            <div key={column.key} className="task-head-cell">
              <span className="task-col" data-col={column.key} style={{ ["--col-width" as string]: `var(--col-${column.key})` }}>
                {column.label}
              </span>
              {index < COLUMNS.length - 1 ? (
                <Divider
                  orientation="vertical"
                  className="col-resizer"
                  label={`${column.label} column width`}
                  current={() => widths[column.key]}
                  onResize={(value) => setWidth((current) => ({ ...current, [column.key]: value }))}
                  min={column.min}
                  max={column.max}
                />
              ) : null}
            </div>
          ))}
        </div>

        <ul className="task-list">
          {sorted.map((task) => (
            <li
              key={task.task_id}
              className="task-row"
              style={{
                ["--col-priority" as string]: `${widths.priority}px`,
                ["--col-id" as string]: `${widths.id}px`,
                ["--col-needs" as string]: `${widths.needs}px`,
                ["--col-target" as string]: `${widths.target}px`,
                ["--col-owner" as string]: `${widths.owner}px`,
                ["--col-status" as string]: `${widths.status}px`,
                ["--col-eta" as string]: `${widths.eta}px`,
              }}
            >
              <span className="task-col" data-col="priority">
                <span style={{ color: priorityColor(task.priority) }}>P{task.priority}</span>
              </span>
              <span className="task-col task-col--id" data-col="id">
                {task.task_id}
              </span>
              <span
                className="task-col task-col--needs"
                data-col="needs"
                title={
                  task.required_capabilities.length > 0
                    ? `requires ${task.required_capabilities.join(", ")}`
                    : "any unit may take it"
                }
              >
                {task.required_capabilities.length > 0
                  ? task.required_capabilities.join(", ")
                  : "any"}
              </span>
              <span className="task-col" data-col="target">
                {formatCoordinate(task.target.x, task.target.y)}
              </span>
              <span className="task-col task-col--owner" data-col="owner">
                {task.assigned_robot_id ? shortRobotId(task.assigned_robot_id) : "--"}
              </span>
              <span className="task-col" data-col="status">
                {task.status}
              </span>
              <span className="task-col" data-col="eta">
                {formatSeconds(task.estimated_duration_s)}
              </span>
            </li>
          ))}
          {sorted.length === 0 ? <p className="muted">no tasks yet</p> : null}
        </ul>
      </div>
    </div>
  );
}
