import { useEffect, useMemo, useRef, useState } from "react";

import { formatPercent, titleCase } from "../format";
import { compareRobots, matchesFilter, matchesQuery, robotTone } from "../selectors";
import type { RosterFilter, RosterSort } from "../selectors";
import type { Robot } from "../types";
import { computeWindow, DEFAULT_ROW_HEIGHT } from "../virtual";
import { EmptyState, StatusPill } from "./Primitives";

const FILTERS: Array<{ key: RosterFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "attention", label: "Needs attention" },
  { key: "charging", label: "Charging" },
  { key: "low", label: "Low battery" },
];

const SORTS: Array<{ key: RosterSort; label: string }> = [
  { key: "id", label: "Identifier" },
  { key: "status", label: "Status" },
  { key: "battery", label: "Battery" },
  { key: "workload", label: "Workload" },
];

export interface RosterProps {
  robots: Robot[];
  selectedRobotId: string | null;
  onSelect: (robotId: string) => void;
}

export function Roster({ robots, selectedRobotId, onSelect }: RosterProps) {
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<RosterFilter>("all");
  const [sort, setSort] = useState<RosterSort>("id");
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(360);
  const scrollRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(
    () =>
      robots
        .filter((robot) => matchesFilter(robot, filter) && matchesQuery(robot, text))
        .sort(compareRobots(sort)),
    [robots, text, filter, sort],
  );

  const window_ = useMemo(
    () =>
      computeWindow({
        rowCount: visible.length,
        scrollTop,
        viewportHeight,
        rowHeight: DEFAULT_ROW_HEIGHT,
      }),
    [visible.length, scrollTop, viewportHeight],
  );

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setViewportHeight(entry.contentRect.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Keep the selected row in view when selection comes from the map.
  useEffect(() => {
    if (!selectedRobotId) return;
    const index = visible.findIndex((robot) => robot.robot_id === selectedRobotId);
    if (index < 0) return;
    const element = scrollRef.current;
    if (!element) return;
    const top = index * DEFAULT_ROW_HEIGHT;
    const bottom = top + DEFAULT_ROW_HEIGHT;
    if (top < element.scrollTop) element.scrollTop = top;
    else if (bottom > element.scrollTop + element.clientHeight) {
      element.scrollTop = bottom - element.clientHeight;
    }
  }, [selectedRobotId, visible]);

  const rows = visible.slice(window_.startIndex, window_.endIndex);

  return (
    <div className="roster">
      <div className="roster__controls">
        <label className="search">
          <span className="visually-hidden">Filter robots by identifier, task, or capability</span>
          <input
            type="search"
            value={text}
            placeholder="Filter by id, task, or capability"
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <div className="segmented" role="group" aria-label="Filter robots by state">
          {FILTERS.map((option) => (
            <button
              key={option.key}
              type="button"
              className="segmented__item"
              aria-pressed={filter === option.key}
              onClick={() => setFilter(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="select">
          <span className="visually-hidden">Sort robots</span>
          <select value={sort} onChange={(event) => setSort(event.target.value as RosterSort)}>
            {SORTS.map((option) => (
              <option key={option.key} value={option.key}>
                Sort: {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="roster__meta">
        <span>
          {visible.length} of {robots.length} robots
        </span>
        {visible.length > rows.length ? (
          <span className="muted">
            rendering {rows.length} rows
          </span>
        ) : null}
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title="No robots match"
          detail="Clear the filter or pick a different state to see the rest of the fleet."
        />
      ) : (
        <div
          className="roster__scroll"
          ref={scrollRef}
          onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        >
          <div style={{ height: window_.totalHeight, position: "relative" }}>
            <div
              style={{
                position: "absolute",
                top: window_.paddingTop,
                left: 0,
                right: 0,
              }}
            >
              {rows.map((robot) => (
                <RosterRow
                  key={robot.robot_id}
                  robot={robot}
                  selected={robot.robot_id === selectedRobotId}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RosterRow({
  robot,
  selected,
  onSelect,
}: {
  robot: Robot;
  selected: boolean;
  onSelect: (robotId: string) => void;
}) {
  return (
    <button
      type="button"
      className={`roster__row${selected ? " roster__row--selected" : ""}`}
      style={{ height: DEFAULT_ROW_HEIGHT }}
      onClick={() => onSelect(robot.robot_id)}
      aria-pressed={selected}
    >
      <StatusPill tone={robotTone(robot)}>{titleCase(robot.status)}</StatusPill>
      <span className="mono roster__id">{robot.robot_id}</span>
      <span className="roster__task mono">{robot.current_task_id ?? "—"}</span>
      <span className="roster__battery">{formatPercent(robot.battery_percent)}</span>
    </button>
  );
}
