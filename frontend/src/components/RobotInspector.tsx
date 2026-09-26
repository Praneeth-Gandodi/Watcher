/**
 * The live robot inspector.
 *
 * Everything shown is the backend's own live state: the geometry comes from the
 * robot profile in telemetry, progress from the committed trajectory, and the
 * wait / conflict / right-of-way fields from the safety and coordination
 * subsystems. The fault buttons post the same canonical commands the backend
 * already exposes, so injecting a failure is a backend operation and not a
 * local flag.
 */

import type { RobotTelemetry, SnapshotResponse, Task } from "../api/types";
import { actionColor, batteryColor } from "../styles/palette";
import {
  formatNumber,
  formatPercent,
  formatSeconds,
  shortRobotId,
} from "../state/format";

export interface RobotInspectorProps {
  robot: RobotTelemetry | null;
  snapshot: SnapshotResponse | null;
  onFail: (robotId: string) => void;
  onLoseLink: (robotId: string) => void;
  onRestore: (robotId: string) => void;
  busy: boolean;
}

export function RobotInspector(props: RobotInspectorProps) {
  const robot = props.robot;
  if (!robot) {
    return (
      <div className="panel-body panel-body--empty">
        <p>no unit selected</p>
        <p className="muted">
          click a robot on the map, or pick one from the fleet list. Selection covers the
          robot&apos;s entire footprint, not just its centre.
        </p>
      </div>
    );
  }

  const snapshotRobot = props.snapshot?.robots.find(
    (candidate) => candidate.robot_id === robot.robot_id,
  );
  const task = props.snapshot?.tasks.find(
    (candidate) => candidate.task_id === robot.task_id,
  );
  const route = props.snapshot?.routes.find(
    (candidate) => candidate.route_id === robot.route_id,
  );
  const nowS = props.snapshot?.simulation_time_s ?? 0;
  const waitingFor = robot.waiting_since_s === null ? 0 : Math.max(0, nowS - robot.waiting_since_s);
  const conflictAge =
    robot.conflict_detected_at_s === null ? 0 : Math.max(0, nowS - robot.conflict_detected_at_s);
  const low = snapshotRobot ? lowBatteryThreshold(snapshotRobot.battery_percent) : 25;

  return (
    <div className="panel-body inspector">
      <header className="inspector-head">
        <div>
          <h2 className="inspector-id">{robot.robot_id}</h2>
          <p className="inspector-sub">
            {robot.width_cells}x{robot.height_cells} footprint &middot;{" "}
            {formatNumber(robot.speed_mps, 2)} m/s
          </p>
        </div>
        <span className="action-chip" style={{ background: actionColor(robot.action) }}>
          {robot.action}
        </span>
      </header>

      <p className="inspector-reason">{robot.action_reason || "no reason reported"}</p>

      <div className="meter">
        <div className="meter-head">
          <span>BATTERY</span>
          <span style={{ color: batteryColor(robot.battery_percent, low, 10) }}>
            {formatPercent(robot.battery_percent, 1)}
          </span>
        </div>
        <span className="meter-track">
          <span
            className="meter-fill"
            style={{
              width: `${Math.max(0, Math.min(100, robot.battery_percent))}%`,
              background: batteryColor(robot.battery_percent, low, 10),
            }}
          />
        </span>
        <p className="meter-note">
          {formatNumber(robot.battery_percent_per_cell, 3)}% per cell
        </p>
      </div>

      <div className="meter">
        <div className="meter-head">
          <span>ROUTE PROGRESS</span>
          <span>{formatPercent(robot.progress * 100, 0)}</span>
        </div>
        <span className="meter-track">
          <span
            className="meter-fill"
            style={{
              width: `${Math.max(0, Math.min(100, robot.progress * 100))}%`,
              background: actionColor(robot.action),
            }}
          />
        </span>
        <p className="meter-note">
          {robot.cells_travelled} cells travelled &middot; {robot.remaining_cells} remaining
          {robot.remaining_time_s > 0 ? ` in ${formatSeconds(robot.remaining_time_s)}` : ""}
        </p>
      </div>

      <dl className="fact-grid">
        <Fact label="STATUS" value={robot.status} tone={statusTone(robot.status)} />
        <Fact label="LINK" value={robot.communication_state} tone={linkTone(robot.communication_state)} />
        <Fact label="CELL" value={`${robot.cell_x}, ${robot.cell_y}`} />
        <Fact label="WORKLOAD" value={formatNumber(robot.workload, 0)} />
        <Fact
          label="TASK"
          value={robot.task_id ?? "none"}
        />
        <Fact
          label="ROUTE"
          value={route ? `${route.route_id} v${route.version}` : robot.route_id ?? "none"}
        />
        <Fact label="STRATEGY" value={route?.strategy ?? "--"} />
        <Fact
          label="DESTINATION"
          value={
            robot.destination_x === null || robot.destination_y === null
              ? "--"
              : `${robot.destination_x}, ${robot.destination_y}`
          }
        />
        <Fact label="CAPABILITIES" value={robot.capabilities.join(", ") || "none"} />
        <Fact label="FAILURE" value={robot.failure_code ?? "none"} tone={robot.failure_code ? "bad" : "ok"} />
      </dl>

      {robot.conflict_with.length > 0 ? (
        <section className="callout callout--conflict">
          <h4>COLLISION RISK</h4>
          <p>
            blocking with{" "}
            {robot.conflict_with.map((id) => shortRobotId(id)).join(", ")} for{" "}
            {formatSeconds(conflictAge)}
          </p>
        </section>
      ) : null}

      {robot.waiting_for_robot_id !== null ? (
        <section className="callout callout--warning">
          <h4>YIELDING RIGHT OF WAY</h4>
          <p>
            waiting for {shortRobotId(robot.waiting_for_robot_id)} &middot; held{" "}
            {formatSeconds(waitingFor)}
          </p>
        </section>
      ) : null}

      {task ? <TaskSummary task={task} /> : null}

      <div className="button-row">
        <button
          className="button button--danger"
          type="button"
          disabled={props.busy}
          onClick={() => props.onFail(robot.robot_id)}
        >
          INJECT FAILURE
        </button>
        <button
          className="button button--danger"
          type="button"
          disabled={props.busy}
          onClick={() => props.onLoseLink(robot.robot_id)}
        >
          DROP LINK
        </button>
        <button
          className="button"
          type="button"
          disabled={props.busy}
          onClick={() => props.onRestore(robot.robot_id)}
        >
          RESTORE
        </button>
      </div>
    </div>
  );
}

function TaskSummary({ task }: { task: Task }) {
  return (
    <section className="callout">
      <h4>TASK {task.task_id}</h4>
      <p>
        target {Math.floor(task.target.x)}, {Math.floor(task.target.y)} &middot; priority{" "}
        {task.priority} &middot; {task.status}
        {task.required_capabilities.length > 0
          ? ` · needs ${task.required_capabilities.join(", ")}`
          : ""}
      </p>
    </section>
  );
}

function Fact({
  label,
  value,
  tone = "ok",
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "bad" | "dim";
}) {
  return (
    <div className="fact">
      <dt className="fact-label">{label}</dt>
      <dd className={`fact-value fact-value--${tone}`}>{value}</dd>
    </div>
  );
}

function linkTone(state: string): "ok" | "warn" | "bad" {
  if (state === "lost") return "bad";
  if (state === "degraded") return "warn";
  return "ok";
}

/** The CSS tone class that matches the palette colour for a status. */
function statusTone(status: string): "ok" | "warn" | "bad" | "dim" {
  switch (status) {
    case "failed":
      return "bad";
    case "blocked":
    case "degraded":
    case "charging":
      return "warn";
    case "offline":
      return "dim";
    default:
      return "ok";
  }
}

/**
 * The backend reports its own low-battery threshold implicitly by driving
 * `DEGRADED` status, so a degraded robot's current level is the practical
 * threshold to colour against.
 */
function lowBatteryThreshold(percent: number): number {
  return percent <= 25 ? percent : 25;
}
