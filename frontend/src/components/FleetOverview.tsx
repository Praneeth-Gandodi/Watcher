/**
 * Fleet overview charts: action mix, battery bands, and the open conflict and
 * right-of-way relationships the backend reports.
 *
 * The values are the backend's own aggregates, so the charts are a readout, not
 * an analysis. Conflict lines are drawn as links between the two robots the
 * backend actually names, because a chord between two arbitrary robots would
 * imply a conflict the simulation never reported.
 */

import { actionColor, batteryColor } from "../styles/palette";
import type { TelemetryResponse } from "../api/types";
import { formatNumber, shortRobotId } from "../state/format";

export interface FleetOverviewProps {
  telemetry: TelemetryResponse | null;
}

/**
 * The backend's own battery bands, named exactly as `battery_buckets` reports
 * them, so the chart can never claim a different boundary than the simulation
 * uses.
 */
const BATTERY_BANDS: { key: string; label: string; ceiling: number }[] = [
  { key: "critical", label: "CRITICAL", ceiling: 10 },
  { key: "low", label: "LOW", ceiling: 25 },
  { key: "normal", label: "NOMINAL", ceiling: 100 },
];

export function FleetOverview({ telemetry }: FleetOverviewProps) {
  if (!telemetry) {
    return <div className="panel-body panel-body--empty">awaiting telemetry</div>;
  }
  const total = telemetry.robots.length;
  return (
    <div className="panel-body">
      <section className="chart">
        <h3 className="chart-title">ACTION MIX</h3>
        <ActionBars telemetry={telemetry} total={total} />
      </section>

      <section className="chart">
        <h3 className="chart-title">BATTERY BANDS</h3>
        <ul className="bars">
          {BATTERY_BANDS.map((band) => {
            const count = telemetry.battery_buckets[band.key] ?? 0;
            const share = total === 0 ? 0 : (count / total) * 100;
            return (
              <li key={band.key} className="bars-row">
                <span className="bars-label">{band.label}</span>
                <span className="bars-track">
                  <span
                    className="bars-fill"
                    style={{
                      width: `${share}%`,
                      background: batteryColor(band.ceiling, 25, 10),
                    }}
                  />
                </span>
                <span className="bars-value">{count}</span>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="chart">
        <h3 className="chart-title">
          CONFLICTS <span className="chart-count">{telemetry.open_conflict_pairs.length}</span>
        </h3>
        {telemetry.open_conflict_pairs.length === 0 ? (
          <p className="muted">no open conflicts</p>
        ) : (
          <ul className="link-list">
            {telemetry.open_conflict_pairs.map((pair) => (
              <li key={pair.join("|")}>
                <span className="link-a">{shortRobotId(pair[0] ?? "?")}</span>
                <span className="link-arrow">&rarr;</span>
                <span className="link-b">{shortRobotId(pair[1] ?? "?")}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="chart">
        <h3 className="chart-title">
          RIGHT OF WAY <span className="chart-count">{rightOfWayCount(telemetry)}</span>
        </h3>
        {rightOfWay(telemetry).length === 0 ? (
          <p className="muted">nobody is yielding</p>
        ) : (
          <ul className="link-list">
            {rightOfWay(telemetry).map((entry) => (
              <li key={entry.waitee}>
                <span className="link-a">{shortRobotId(entry.yielder)}</span>
                <span className="link-arrow link-arrow--yield">&rarr;</span>
                <span className="link-b">{shortRobotId(entry.waitee)}</span>
                <span className="link-seconds">{formatNumber(entry.waiting, 1)}s</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {telemetry.deadlocked_robot_ids.length > 0 ? (
        <section className="chart">
          <h3 className="chart-title chart-title--bad">DEADLOCKED UNITS</h3>
          <div className="chip-row">
            {telemetry.deadlocked_robot_ids.map((id) => (
              <span key={id} className="chip chip--bad">
                {shortRobotId(id)}
              </span>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function ActionBars({ telemetry, total }: { telemetry: TelemetryResponse; total: number }) {
  const entries = Object.entries(telemetry.counts_by_action)
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1]);
  if (entries.length === 0) return <p className="muted">no units reporting</p>;
  return (
    <ul className="bars">
      {entries.map(([action, count]) => (
        <li key={action} className="bars-row">
          <span className="bars-label">{action}</span>
          <span className="bars-track">
            <span
              className="bars-fill"
              style={{
                width: `${total === 0 ? 0 : (count / total) * 100}%`,
                background: actionColor(action),
              }}
            />
          </span>
          <span className="bars-value">{count}</span>
        </li>
      ))}
    </ul>
  );
}

interface YieldEntry {
  yielder: string;
  waitee: string;
  waiting: number;
}

function rightOfWay(telemetry: TelemetryResponse): YieldEntry[] {
  const now = telemetry.simulation_time_s;
  const entries: YieldEntry[] = [];
  for (const robot of telemetry.robots) {
    if (robot.waiting_for_robot_id === null) continue;
    entries.push({
      yielder: robot.robot_id,
      waitee: robot.waiting_for_robot_id,
      waiting: robot.waiting_since_s === null ? 0 : Math.max(0, now - robot.waiting_since_s),
    });
  }
  return entries.sort((left, right) => right.waiting - left.waiting);
}

function rightOfWayCount(telemetry: TelemetryResponse): number {
  return telemetry.robots.filter((robot) => robot.waiting_for_robot_id !== null).length;
}
