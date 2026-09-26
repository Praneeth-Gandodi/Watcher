/**
 * The event log: canonical events, filterable, with a live cursor.
 *
 * The rows are the backend's own event envelopes, read incrementally with
 * `after_sequence`, so the sequence column is a real cursor: it never repeats
 * a line and it never skips one. Filtering happens in the browser on rows that
 * were already delivered, which keeps the read path simple.
 */

import { useMemo, useState } from "react";
import { robotIdOf, taskIdOf, type EventRow } from "../state/useEventLog";
import { formatNumber } from "../state/format";

export interface EventLogPanelProps {
  rows: EventRow[];
  cursor: number;
  paused: boolean;
  onSelectRobot: (robotId: string) => void;
  selectedRobotId: string | null;
}

interface FilterSpec {
  key: string;
  label: string;
  match: (eventType: string) => boolean;
}

/** Filters over the backend's actual `EventType` values. */
const FILTERS: FilterSpec[] = [
  { key: "all", label: "ALL", match: () => true },
  {
    key: "conflicts",
    label: "CONFLICT",
    match: (type) => type === "CONFLICT_DETECTED" || type === "DEADLOCK_DETECTED",
  },
  {
    key: "negotiation",
    label: "BID",
    match: (type) =>
      type === "BID_SUBMITTED" ||
      type === "NEGOTIATION_STARTED" ||
      type === "TASK_ASSIGNED" ||
      type === "TASK_REASSIGNED",
  },
  {
    key: "recovery",
    label: "RECOVERY",
    match: (type) =>
      type === "RECOVERY_STARTED" || type === "ROUTE_REPLANNED" || type === "ROBOT_FAILED" ||
      type === "COMMUNICATION_LOST" || type === "BATTERY_LOW",
  },
  { key: "routes", label: "ROUTE", match: (type) => type.startsWith("ROUTE") },
];

const TYPE_TONE: Record<string, string> = {
  CONFLICT_DETECTED: "bad",
  DEADLOCK_DETECTED: "bad",
  ROBOT_FAILED: "bad",
  COMMUNICATION_LOST: "bad",
  BATTERY_LOW: "warn",
  RECOVERY_STARTED: "warn",
  ROUTE_REPLANNED: "warn",
  BID_SUBMITTED: "info",
  NEGOTIATION_STARTED: "info",
  TASK_ASSIGNED: "ok",
  TASK_REASSIGNED: "ok",
  TASK_COMPLETED: "ok",
};

export function EventLogPanel(props: EventLogPanelProps) {
  const [filter, setFilter] = useState("all");
  const [scopeToSelection, setScopeToSelection] = useState(false);
  const active = FILTERS.find((candidate) => candidate.key === filter) ?? FILTERS[0]!;

  const rows = useMemo(() => {
    return [...props.rows]
      .reverse()
      .filter((event) => {
        if (!active.match(event.eventType)) return false;
        if (scopeToSelection) {
          const robot = robotIdOf(event.payload);
          if (robot !== props.selectedRobotId) return false;
        }
        return true;
      });
  }, [props.rows, active, scopeToSelection, props.selectedRobotId]);

  return (
    <div className="panel-body event-log">
      <div className="event-controls">
        <div className="seg">
          {FILTERS.map((spec) => (
            <button
              key={spec.key}
              type="button"
              className={filter === spec.key ? "seg-item seg-item--on" : "seg-item"}
              onClick={() => setFilter(spec.key)}
            >
              {spec.label}
            </button>
          ))}
        </div>
        <label className="toggle">
          <input
            type="checkbox"
            checked={scopeToSelection}
            onChange={(event) => setScopeToSelection(event.target.checked)}
          />
          <span>only {props.selectedRobotId ?? "selected"}</span>
        </label>
        <span className="event-cursor">
          CURSOR {props.cursor} {props.paused ? "(POLLING SLOWED)" : ""}
        </span>
      </div>

      <ul className="event-list">
        {rows.map((event) => {
          const robot = robotIdOf(event.payload);
          const task = taskIdOf(event.payload);
          return (
            <li key={event.sequence} className="event-row">
              <span className="event-seq">{event.sequence}</span>
              <span className="event-time">{formatNumber(event.occurredAtS, 1)}s</span>
              <span
                className={`event-type event-type--${TYPE_TONE[event.eventType] ?? "dim"}`}
                title={event.producer}
              >
                {event.eventType}
              </span>
              <span className="event-refs">
                {robot ? (
                  <button
                    className="event-ref"
                    type="button"
                    onClick={() => props.onSelectRobot(robot)}
                  >
                    {robot}
                  </button>
                ) : null}
                {task ? <span className="event-task">{task}</span> : null}
              </span>
            </li>
          );
        })}
        {rows.length === 0 ? <li className="muted event-empty">no matching events</li> : null}
      </ul>
    </div>
  );
}
