/**
 * The scenario editor.
 *
 * It only posts a scenario name plus optional size overrides, which the
 * backend turns into a fresh deterministic run. The presets, grid sizes, and
 * fleet sizes come from `GET /scenarios` rather than being duplicated here, so
 * the editor cannot drift from what the backend actually supports.
 */

import { useEffect, useState } from "react";
import type { ScenarioListResponse } from "../api/types";

export interface ScenarioEditorProps {
  scenarios: ScenarioListResponse | null;
  active: string;
  busy: boolean;
  onLoad: (input: {
    name: string;
    robotCount?: number;
    columns?: number;
    rows?: number;
  }) => void;
}

type SizeMode = "preset" | "grid" | "fleet";

export function ScenarioEditor({ scenarios, active, busy, onLoad }: ScenarioEditorProps) {
  const [name, setName] = useState(active);
  const [mode, setMode] = useState<SizeMode>("preset");
  const [grid, setGrid] = useState("");
  const [fleet, setFleet] = useState("");

  useEffect(() => {
    setName(active);
  }, [active]);

  const load = (): void => {
    if (name.trim() === "") return;
    if (mode === "grid" && grid !== "") {
      const [columns, rows] = grid.split("x").map((part) => Number(part.trim()));
      if (!columns || !rows) return;
      onLoad({ name, columns, rows });
      return;
    }
    if (mode === "fleet" && fleet !== "") {
      const robotCount = Number(fleet);
      if (!robotCount) return;
      onLoad({ name, robotCount });
      return;
    }
    onLoad({ name });
  };

  return (
    <div className="panel-body scenario">
      <div className="field">
        <label htmlFor="scenario-name">SCENARIO</label>
        <select
          id="scenario-name"
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
        >
          {(scenarios?.scenarios ?? []).map((scenario) => (
            <option key={scenario.name} value={scenario.name}>
              {scenario.name} · {scenario.layout} · {scenario.robot_count}u /{" "}
              {scenario.task_count}t · {scenario.columns}x{scenario.rows}
            </option>
          ))}
          {scenarios === null ? <option value="normal">loading…</option> : null}
        </select>
      </div>

      <p className="muted scenario-description">
        {scenarios?.scenarios.find((scenario) => scenario.name === name)?.description ??
          "loading scenario description"}
      </p>

      <div className="field">
        <span className="field-label">SIZE MODE</span>
        <div className="seg">
          {(["preset", "grid", "fleet"] as SizeMode[]).map((option) => (
            <button
              key={option}
              type="button"
              className={mode === option ? "seg-item seg-item--on" : "seg-item"}
              onClick={() => setMode(option)}
            >
              {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {mode === "grid" ? (
        <div className="field">
          <label htmlFor="scenario-grid">GRID</label>
          <select
            id="scenario-grid"
            className="input"
            value={grid}
            onChange={(event) => setGrid(event.target.value)}
          >
            <option value="">(use preset size)</option>
            {(scenarios?.grid_presets ?? []).map(([columns, rows]) => (
              <option key={`${columns}x${rows}`} value={`${columns}x${rows}`}>
                {columns}x{rows}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {mode === "fleet" ? (
        <div className="field">
          <label htmlFor="scenario-fleet">FLEET</label>
          <select
            id="scenario-fleet"
            className="input"
            value={fleet}
            onChange={(event) => setFleet(event.target.value)}
          >
            <option value="">(use preset size)</option>
            {(scenarios?.fleet_presets ?? []).map((count) => (
              <option key={count} value={count}>
                {count} robots
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <p className="muted scenario-note">
        A fleet size on its own grows the grid automatically so every unit fits;
        an explicit grid size wins over the preset.
      </p>

      <div className="button-row">
        <button
          className="button button--primary"
          type="button"
          disabled={busy || scenarios === null}
          onClick={load}
        >
          LOAD SCENARIO
        </button>
      </div>
    </div>
  );
}
