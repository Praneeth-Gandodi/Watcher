/**
 * The fleet list: every unit, searchable, without 500 DOM robot components.
 *
 * This list renders text rows, not robot bodies, and it windows what it draws
 * so a 500-robot scenario stays responsive. Selecting a row selects the robot
 * on the map, so the two views always agree.
 */

import { useMemo, useState } from "react";
import type { RobotTelemetry, TelemetryResponse } from "../api/types";
import { actionColor, batteryColor, statusColor } from "../styles/palette";
import { formatPercent, shortRobotId } from "../state/format";

export interface RobotListProps {
  telemetry: TelemetryResponse | null;
  selectedRobotId: string | null;
  onSelect: (robotId: string) => void;
}

type Filter = "all" | "active" | "attention";

const ROW_HEIGHT = 22;
/** Rows drawn per frame budget; the rest are reachable by scrolling. */
const WINDOW_SIZE = 120;

export function RobotList({ telemetry, selectedRobotId, onSelect }: RobotListProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [start, setStart] = useState(0);

  const rows = useMemo(() => {
    const robots = telemetry?.robots ?? [];
    const needle = query.trim().toLowerCase();
    return robots.filter((robot) => {
      if (needle && !robot.robot_id.toLowerCase().includes(needle)) return false;
      if (filter === "active") return robot.action === "MOVING";
      if (filter === "attention") {
        return (
          robot.action === "BLOCKED" ||
          robot.action === "WAITING" ||
          robot.action === "FAILED" ||
          robot.action === "DEGRADED" ||
          robot.communication_state === "lost" ||
          robot.battery_percent <= 25
        );
      }
      return true;
    });
  }, [telemetry, query, filter]);

  const visible = rows.slice(start, start + WINDOW_SIZE);
  const total = rows.length;

  return (
    <div className="fleet">
      <div className="fleet-controls">
        <input
          className="input"
          type="search"
          value={query}
          placeholder="robot-0.."
          onChange={(event) => {
            setQuery(event.target.value);
            setStart(0);
          }}
        />
        <div className="seg">
          {(["all", "active", "attention"] as Filter[]).map((option) => (
            <button
              key={option}
              type="button"
              className={filter === option ? "seg-item seg-item--on" : "seg-item"}
              onClick={() => {
                setFilter(option);
                setStart(0);
              }}
            >
              {option === "attention" ? "ATTN" : option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div
        className="fleet-scroll"
        onScroll={(event) => {
          const element = event.currentTarget;
          const next = Math.max(
            0,
            Math.floor(element.scrollTop / ROW_HEIGHT) - Math.floor(WINDOW_SIZE / 2),
          );
          setStart(next);
        }}
      >
        <ul className="fleet-list" style={{ height: total * ROW_HEIGHT }}>
          {visible.map((robot, index) => (
            <FleetRow
              key={robot.robot_id}
              robot={robot}
              offset={start + index}
              selected={robot.robot_id === selectedRobotId}
              onSelect={onSelect}
            />
          ))}
        </ul>
        {total === 0 ? <p className="muted fleet-empty">no units match</p> : null}
      </div>
      <p className="fleet-count">
        {total} of {telemetry?.robots.length ?? 0} units
        {total > WINDOW_SIZE ? ` (windowed ${WINDOW_SIZE})` : ""}
      </p>
    </div>
  );
}

function FleetRow({
  robot,
  offset,
  selected,
  onSelect,
}: {
  robot: RobotTelemetry;
  offset: number;
  selected: boolean;
  onSelect: (robotId: string) => void;
}) {
  return (
    <li
      className={selected ? "fleet-row fleet-row--on" : "fleet-row"}
      style={{ top: offset * ROW_HEIGHT, height: ROW_HEIGHT }}
      onClick={() => onSelect(robot.robot_id)}
    >
      <span className="fleet-swatch" style={{ background: actionColor(robot.action) }} />
      <span className="fleet-id">{shortRobotId(robot.robot_id)}</span>
      <span className="fleet-cell">
        {robot.cell_x},{robot.cell_y}
      </span>
      <span className="fleet-action" style={{ color: actionColor(robot.action) }}>
        {robot.action}
      </span>
      <span className="fleet-status" style={{ color: statusColor(robot.status) }}>
        {robot.status}
      </span>
      <span
        className="fleet-battery"
        style={{ color: batteryColor(robot.battery_percent, 25, 10) }}
      >
        {formatPercent(robot.battery_percent, 0)}
      </span>
    </li>
  );
}
