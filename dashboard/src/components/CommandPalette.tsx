/**
 * Command palette.
 *
 * At 500 robots the roster is the only sane way to find a specific unit, but
 * scrolling a virtualized table to hunt an identifier is a poor use of an
 * operator's time. The palette is the keyboard path to any robot or task, and
 * it is fully operable without a pointer.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { formatPercent, titleCase } from "../format";
import type { StatusTone } from "../selectors";
import { robotTone, taskTone } from "../selectors";
import type { Robot, Task } from "../types";

export interface PaletteTarget {
  kind: "robot" | "task";
  id: string;
  label: string;
  detail: string;
  tone: StatusTone;
  robotId: string | null;
}

export interface CommandPaletteProps {
  open: boolean;
  robots: Robot[];
  tasks: Task[];
  onClose: () => void;
  onSelectRobot: (robotId: string) => void;
  onSelectTask: (taskId: string) => void;
}

const MAX_RESULTS = 40;

export function CommandPalette({
  open,
  robots,
  tasks,
  onClose,
  onSelectRobot,
  onSelectTask,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const results = useMemo<PaletteTarget[]>(() => {
    const needle = query.trim().toLowerCase();
    const targets: PaletteTarget[] = [];

    for (const robot of robots) {
      if (needle !== "" && !robot.robot_id.includes(needle) && !robot.current_task_id?.includes(needle)) {
        continue;
      }
      targets.push({
        kind: "robot",
        id: robot.robot_id,
        label: robot.robot_id,
        detail: `${titleCase(robot.status)} · ${formatPercent(robot.battery_percent)} · ${
          robot.current_task_id ?? "no task"
        }`,
        tone: robotTone(robot),
        robotId: robot.robot_id,
      });
    }

    for (const task of tasks) {
      if (needle === "" && targets.length >= MAX_RESULTS) break;
      if (
        needle !== "" &&
        !task.task_id.includes(needle) &&
        !(task.assigned_robot_id?.includes(needle) ?? false)
      ) {
        continue;
      }
      targets.push({
        kind: "task",
        id: task.task_id,
        label: task.task_id,
        detail: `${titleCase(task.status)} · P${task.priority} · ${
          task.assigned_robot_id ?? "unassigned"
        }`,
        tone: taskTone(task),
        robotId: task.assigned_robot_id,
      });
    }

    return targets.slice(0, MAX_RESULTS);
  }, [query, robots, tasks]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setCursor(0);
    // Focus after paint so the dialog is announced before the field takes input.
    const handle = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(handle);
  }, [open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const active = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  if (!open) return null;

  const commit = (target: PaletteTarget | undefined) => {
    if (!target) return;
    if (target.kind === "task") {
      onSelectTask(target.id);
      if (target.robotId) onSelectRobot(target.robotId);
    } else {
      onSelectRobot(target.id);
    }
    onClose();
  };

  return (
    <div className="palette" role="presentation" onMouseDown={onClose}>
      <div
        className="palette__dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Find a robot or task"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette__input"
          type="text"
          value={query}
          placeholder="Find a robot or task by identifier"
          aria-label="Find a robot or task by identifier"
          aria-controls="palette-results"
          aria-activedescendant={results[cursor] ? `palette-option-${cursor}` : undefined}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onClose();
              return;
            }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((current) => Math.min(results.length - 1, current + 1));
              return;
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((current) => Math.max(0, current - 1));
              return;
            }
            if (event.key === "Enter") {
              event.preventDefault();
              commit(results[cursor]);
            }
          }}
        />

        {results.length === 0 ? (
          <p className="palette__empty">Nothing matches that identifier.</p>
        ) : (
          <ul className="palette__results" id="palette-results" role="listbox" ref={listRef}>
            {results.map((target, index) => (
              <li
                key={`${target.kind}-${target.id}`}
                id={`palette-option-${index}`}
                role="option"
                aria-selected={index === cursor}
                data-active={index === cursor}
                className={`palette__row${index === cursor ? " palette__row--active" : ""}`}
                onMouseEnter={() => setCursor(index)}
                onClick={() => commit(target)}
              >
                <span className={`palette__kind palette__kind--${target.tone}`}>{target.kind}</span>
                <span className="mono palette__label">{target.label}</span>
                <span className="palette__detail">{target.detail}</span>
              </li>
            ))}
          </ul>
        )}

        <p className="palette__footer">
          <kbd className="kbd">↑</kbd>
          <kbd className="kbd">↓</kbd> to move · <kbd className="kbd">Enter</kbd> to select ·{" "}
          <kbd className="kbd">Esc</kbd> to close
        </p>
      </div>
    </div>
  );
}
