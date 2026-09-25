import { Crosshair, RefreshCcw, TriangleAlert, WifiOff } from "lucide-react";

import {
  formatCoordinate,
  formatPercent,
  formatSeconds,
  humanizeStrategy,
  titleCase,
} from "../format";
import { robotTone } from "../selectors";
import type { Robot, SimulationSnapshot } from "../types";
import { EmptyState, StatusPill } from "./Primitives";

export interface InspectorProps {
  snapshot: SimulationSnapshot | null;
  robot: Robot | null;
  commandPending: boolean;
  onInjectFailure: (robot: Robot) => void;
  onInjectCommsLoss: (robot: Robot) => void;
  onRestore: (robot: Robot) => void;
  onFocusOnMap: (robotId: string) => void;
}

export function Inspector({
  snapshot,
  robot,
  commandPending,
  onInjectFailure,
  onInjectCommsLoss,
  onRestore,
  onFocusOnMap,
}: InspectorProps) {
  if (!robot || !snapshot) {
    return (
      <EmptyState
        title="No robot selected"
        detail="Pick a marker on the map or a row in the roster to inspect telemetry and inject faults."
      />
    );
  }

  const route = snapshot.routes.find((candidate) => candidate.robot_id === robot.robot_id);
  const task = robot.current_task_id
    ? snapshot.tasks.find((candidate) => candidate.task_id === robot.current_task_id)
    : undefined;

  return (
    <div className="inspector">
      <div className="inspector__identity">
        <div>
          <p className="mono inspector__id">{robot.robot_id}</p>
          <p className="muted">{task ? task.task_id : "No task assigned"}</p>
        </div>
        <StatusPill tone={robotTone(robot)}>{titleCase(robot.status)}</StatusPill>
      </div>

      <dl className="inspector__grid">
        <Field label="Battery" value={formatPercent(robot.battery_percent)} warn={robot.battery_percent <= 20} />
        <Field label="Workload" value={String(robot.workload)} />
        <Field
          label="Position"
          value={`${formatCoordinate(robot.position.x)}, ${formatCoordinate(robot.position.y)} m`}
        />
        <Field
          label="Comms"
          value={titleCase(robot.communication_state)}
          warn={robot.communication_state === "lost"}
        />
        <Field label="Updated" value={formatSeconds(robot.last_updated_at_s)} />
        <Field label="Capabilities" value={robot.capabilities.map(titleCase).join(", ") || "None"} />
      </dl>

      {route ? (
        <div className="inspector__section">
          <p className="kicker">Current route</p>
          <dl className="inspector__grid">
            <Field label="Strategy" value={humanizeStrategy(route.strategy)} />
            <Field label="Version" value={`v${route.version}`} />
            <Field label="Status" value={titleCase(route.status)} />
            <Field label="Waypoints" value={String(route.waypoints.length)} />
          </dl>
        </div>
      ) : null}

      {task ? (
        <div className="inspector__section">
          <p className="kicker">Assigned task</p>
          <dl className="inspector__grid">
            <Field label="Task" value={task.task_id} />
            <Field label="Priority" value={`P${task.priority}`} />
            <Field label="Status" value={titleCase(task.status)} />
            <Field label="Estimate" value={formatSeconds(task.estimated_duration_s)} />
          </dl>
        </div>
      ) : null}

      {robot.failure ? (
        <p className="inspector__fault">
          <TriangleAlert size={15} aria-hidden="true" />
          <span>
            {titleCase(robot.failure.kind)} · {robot.failure.code}
            {robot.failure.detail ? ` — ${robot.failure.detail}` : ""}
          </span>
        </p>
      ) : null}

      <div className="inspector__actions">
        <button
          type="button"
          className="button button--ghost"
          onClick={() => onFocusOnMap(robot.robot_id)}
          disabled={commandPending}
        >
          <Crosshair size={15} aria-hidden="true" />
          Centre
        </button>
        <button
          type="button"
          className="button button--ghost"
          onClick={() => onInjectFailure(robot)}
          disabled={commandPending}
        >
          <TriangleAlert size={15} aria-hidden="true" />
          Fail
        </button>
        <button
          type="button"
          className="button button--ghost"
          onClick={() => onInjectCommsLoss(robot)}
          disabled={commandPending}
        >
          <WifiOff size={15} aria-hidden="true" />
          Silence
        </button>
        <button
          type="button"
          className="button button--primary"
          onClick={() => onRestore(robot)}
          disabled={commandPending}
        >
          <RefreshCcw size={15} aria-hidden="true" />
          Restore
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="field">
      <dt className="field__label">{label}</dt>
      <dd className={`field__value${warn ? " field__value--warn" : ""}`}>{value}</dd>
    </div>
  );
}
