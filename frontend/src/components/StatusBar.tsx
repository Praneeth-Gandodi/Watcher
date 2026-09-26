/**
 * The status strip: the live counters, plus any error or notice from the
 * backend. All numbers come straight from `SystemMetrics` and
 * `TelemetryResponse`; nothing here is computed in the browser.
 */

import { PALETTE } from "../styles/palette";
import type { SystemMetrics, TelemetryResponse } from "../api/types";
import { formatInteger, formatNumber, formatSeconds } from "../state/format";

export interface StatusBarProps {
  simulationTimeS: number;
  revision: number;
  lastEventSequence: number;
  scenario: string;
  metrics: SystemMetrics | null;
  telemetry: TelemetryResponse | null;
  error: string | null;
  runComplete: boolean;
  notice: string | null;
  onDismissError: () => void;
  onDismissNotice: () => void;
}

export function StatusBar(props: StatusBarProps) {
  const metrics = props.metrics;
  const telemetry = props.telemetry;
  return (
    <div className="status-bar">
      <Stat label="T+" value={formatSeconds(props.simulationTimeS)} />
      <Stat label="REV" value={formatInteger(props.revision)} />
      <Stat label="SEQ" value={formatInteger(props.lastEventSequence)} />
      <Stat label="ACTIVE" value={metrics ? formatInteger(metrics.active_robots) : "--"} />
      <Stat
        label="TASKS"
        value={metrics ? `${metrics.pending_tasks}/${metrics.completed_tasks}` : "--"}
        hint="pending / completed"
      />
      <Stat
        label="CONFLICT"
        value={metrics ? formatInteger(metrics.open_conflicts) : "--"}
        tone={metrics && metrics.open_conflicts > 0 ? "warn" : "ok"}
      />
      <Stat
        label="DEADLOCK"
        value={
          telemetry
            ? formatInteger(telemetry.deadlocked_robot_ids.length)
            : metrics
              ? formatInteger(metrics.detected_deadlocks)
              : "--"
        }
        tone={telemetry && telemetry.deadlocked_robot_ids.length > 0 ? "bad" : "ok"}
      />
      <Stat
        label="FAILED"
        value={metrics ? formatInteger(metrics.failed_robots) : "--"}
        tone={metrics && metrics.failed_robots > 0 ? "bad" : "ok"}
      />
      <Stat
        label="LOST LINK"
        value={metrics ? formatInteger(metrics.communication_lost_robots) : "--"}
        tone={metrics && metrics.communication_lost_robots > 0 ? "warn" : "ok"}
      />
      <Stat
        label="BATTERY"
        value={metrics ? formatNumber(metrics.average_battery_percent, 1) : "--"}
        hint="percent, fleet average"
      />
      <Stat
        label="ALLOC"
        value={metrics ? formatNumber(metrics.average_allocation_latency_ms, 1) : "--"}
        hint="milliseconds, average"
      />
      <Stat
        label="EVENTS/S"
        value={metrics ? formatNumber(metrics.event_throughput_per_s, 1) : "--"}
      />
      <Stat
        label="CONTROLLER"
        value={metrics ? (metrics.controller_available ? "UP" : "DOWN") : "--"}
        tone={metrics && !metrics.controller_available ? "bad" : "ok"}
      />

      {props.error ? (
        <button className="status-flash status-flash--error" type="button" onClick={props.onDismissError}>
          {props.error}
        </button>
      ) : null}
      {props.runComplete && !props.error ? (
        <span className="status-flash status-flash--complete">
          RUN COMPLETE &mdash; every task in this scenario is finished. Press RESTART RUN or load a
          template for fresh work.
        </span>
      ) : null}
      {props.notice && !props.error && !props.runComplete ? (
        <button
          className="status-flash status-flash--notice"
          type="button"
          onClick={props.onDismissNotice}
        >
          {props.notice}
        </button>
      ) : null}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  tone = "ok",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "warn" | "bad";
}) {
  const color =
    tone === "bad" ? PALETTE.failure : tone === "warn" ? PALETTE.warning : PALETTE.text;
  return (
    <div className="stat" title={hint}>
      <span className="stat-label">{label}</span>
      <span className="stat-value" style={{ color }}>
        {value}
      </span>
    </div>
  );
}
