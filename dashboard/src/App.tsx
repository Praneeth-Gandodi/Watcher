/**
 * Watcher fleet operations console.
 *
 * The map is the page; everything else is a panel around it. This component
 * owns composition and keyboard shortcuts only — data comes from the
 * connection hook, and every derived figure comes from `selectors`, so the tree
 * stays cheap enough to run beside a 500-robot canvas.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TriangleAlert, X } from "lucide-react";

import FleetMap, { DEFAULT_LAYERS } from "./components/FleetMap";
import type { MapLayers } from "./components/FleetMap";
import { CommandBar } from "./components/CommandBar";
import { CommandPalette } from "./components/CommandPalette";
import { Dock } from "./components/Dock";
import { Inspector } from "./components/Inspector";
import { OperatorControls } from "./components/OperatorControls";
import { Panel } from "./components/Primitives";
import { buildCreateTaskCommand } from "./state";
import type { CreateTaskInput } from "./state";
import { activeDeadlocks, statTiles } from "./selectors";
import { applyTheme, nextTheme, readInitialTheme } from "./theme";
import type { ThemeName } from "./theme";
import { useFleetConnection } from "./useFleetConnection";
import type { Robot } from "./types";

const FAILURE_CODE = "operator-injected";

export default function App() {
  const fleet = useFleetConnection();
  const [theme, setTheme] = useState<ThemeName>(() => readInitialTheme());
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null);
  const [hoveredRobotId, setHoveredRobotId] = useState<string | null>(null);
  const [layers, setLayers] = useState<MapLayers>(DEFAULT_LAYERS);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [cameraResetToken, setCameraResetToken] = useState(0);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  // The theme attribute is written before the state update, not in an effect.
  // The map reads its canvas colours out of the document during render, so if
  // the attribute were applied afterwards the canvas would keep drawing the
  // previous theme's palette — which is exactly what an effect-based flip does.
  const toggleTheme = useCallback(() => {
    const next = nextTheme(themeRef.current);
    applyTheme(next);
    setTheme(next);
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const snapshot = fleet.snapshot;
  const deadlocks = useMemo(() => activeDeadlocks(fleet.events), [fleet.events]);
  const tiles = useMemo(() => statTiles(snapshot), [snapshot]);

  const selectedRobot = useMemo<Robot | null>(
    () => snapshot?.robots.find((robot) => robot.robot_id === selectedRobotId) ?? null,
    [selectedRobotId, snapshot],
  );

  // A robot that disappears from the projection must not stay selected and
  // leave the inspector showing telemetry that no longer exists.
  useEffect(() => {
    if (!selectedRobotId || !snapshot) return;
    if (!snapshot.robots.some((robot) => robot.robot_id === selectedRobotId)) {
      setSelectedRobotId(null);
    }
  }, [selectedRobotId, snapshot]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (event.key === "/" && !isTypingTarget(event.target)) {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const commandBase = useCallback(
    () => ({
      command_id: crypto.randomUUID(),
      schema_version: 1 as const,
      issued_at_s: snapshot?.simulation_time_s ?? 0,
    }),
    [snapshot],
  );

  const handleSelectRobot = useCallback((robotId: string | null) => {
    setSelectedRobotId(robotId);
  }, []);

  const toggleLayer = useCallback((layer: keyof MapLayers) => {
    setLayers((current) => ({ ...current, [layer]: !current[layer] }));
  }, []);

  const resetCamera = useCallback(() => {
    setCameraResetToken((token) => token + 1);
  }, []);

  return (
    <div className="shell" data-camera-reset={cameraResetToken}>
      <a className="skip-link" href="#map-region">
        Skip to the fleet map
      </a>

      <CommandBar
        connection={fleet.connection}
        lastSync={fleet.lastSync}
        simulationTimeS={snapshot?.simulation_time_s ?? null}
        revision={snapshot?.revision ?? null}
        lastEventSequence={snapshot?.last_event_sequence ?? null}
        fleetSize={snapshot?.robots.length ?? 0}
        controllerAvailable={snapshot ? snapshot.controller_available : null}
        controllerOutages={snapshot?.metrics.extra_metrics.controller_outages ?? null}
        theme={theme}
        onToggleTheme={toggleTheme}
        onRefresh={fleet.refresh}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      {fleet.notice ? (
        <div className={`notice notice--${fleet.notice.kind}`} role="status">
          <TriangleAlert size={16} aria-hidden="true" />
          <div>
            <strong>{fleet.notice.title}</strong>
            <span>{fleet.notice.detail}</span>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Dismiss notice"
            onClick={fleet.dismissNotice}
          >
            <X size={15} />
          </button>
        </div>
      ) : null}

      <main className="layout" id="map-region">
        <section className="layout__map">
          <div className="tiles" role="group" aria-label="Fleet metrics">
            {tiles.map((tile) => (
              <div key={tile.key} className={`tile tile--${tile.tone}`}>
                <span className="tile__label">{tile.label}</span>
                <span className="tile__value">{tile.value}</span>
                <span className="tile__detail">{tile.detail}</span>
              </div>
            ))}
          </div>

          <FleetMap
            key={cameraResetToken}
            snapshot={snapshot}
            deadlocks={deadlocks}
            selectedRobotId={selectedRobotId}
            hoveredRobotId={hoveredRobotId}
            layers={layers}
            theme={theme}
            stale={fleet.connection === "stale"}
            onSelectRobot={handleSelectRobot}
            onHoverRobot={setHoveredRobotId}
            onToggleLayer={toggleLayer}
            onResetCamera={resetCamera}
          />
        </section>

        <aside className="layout__rail">
          <Panel title="Inspector" kicker="Selection" id="inspector">
            <Inspector
              snapshot={snapshot}
              robot={selectedRobot}
              commandPending={fleet.commandPending}
              onInjectFailure={(robot) =>
                void fleet.issueCommand({
                  ...commandBase(),
                  command_type: "INJECT_ROBOT_FAILURE",
                  robot_id: robot.robot_id,
                  failure: {
                    kind: "other",
                    code: FAILURE_CODE,
                    detected_at_s: snapshot?.simulation_time_s ?? 0,
                    detail: "Injected from the Watcher operations console.",
                  },
                })
              }
              onInjectCommsLoss={(robot) =>
                void fleet.issueCommand({
                  ...commandBase(),
                  command_type: "INJECT_COMMUNICATION_LOSS",
                  robot_id: robot.robot_id,
                  timeout_s: 8,
                })
              }
              onRestore={(robot) =>
                void fleet.issueCommand({
                  ...commandBase(),
                  command_type: "RESTORE_ROBOT",
                  robot_id: robot.robot_id,
                })
              }
              onFocusOnMap={handleSelectRobot}
            />
          </Panel>

          <Panel title="Operator controls" kicker="Command adapter" id="controls">
            <OperatorControls
              snapshot={snapshot}
              pending={fleet.commandPending}
              onPause={() =>
                void fleet.issueCommand({ ...commandBase(), command_type: "PAUSE_SIMULATION" })
              }
              onResume={() =>
                void fleet.issueCommand({ ...commandBase(), command_type: "RESUME_SIMULATION" })
              }
              onReset={() =>
                void fleet.issueCommand({
                  ...commandBase(),
                  command_type: "RESET_SIMULATION",
                  seed: 2026,
                })
              }
              onSpeed={(multiplier) =>
                void fleet.issueCommand({
                  ...commandBase(),
                  command_type: "SET_SIMULATION_SPEED",
                  multiplier,
                })
              }
              onCreateTask={(input) => {
                try {
                  const command = buildCreateTaskCommand(input as CreateTaskInput, {
                    commandId: crypto.randomUUID(),
                    issuedAtS: snapshot?.simulation_time_s ?? 0,
                  });
                  void fleet.issueCommand(command);
                } catch (error) {
                  // The form validates first; this is the last line of defence.
                  window.alert(
                    error instanceof Error ? error.message : "The task could not be queued.",
                  );
                }
              }}
            />
            {fleet.commandNotice ? (
              <p className={`command-notice command-notice--${fleet.commandNotice.kind}`} role="status">
                <strong>{fleet.commandNotice.title}</strong>
                <span>{fleet.commandNotice.detail}</span>
              </p>
            ) : null}
          </Panel>
        </aside>
      </main>

      <Dock
        snapshot={snapshot}
        events={fleet.events}
        deadlocks={deadlocks}
        selectedRobotId={selectedRobotId}
        onSelectRobot={handleSelectRobot}
      />

      <CommandPalette
        open={paletteOpen}
        robots={snapshot?.robots ?? []}
        tasks={snapshot?.tasks ?? []}
        onClose={() => setPaletteOpen(false)}
        onSelectRobot={handleSelectRobot}
        onSelectTask={() => undefined}
      />
    </div>
  );
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}
