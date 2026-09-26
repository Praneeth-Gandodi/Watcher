/**
 * The console shell.
 *
 * Layout is the classic industrial command console: a full-bleed map in the
 * middle, live telemetry and the robot inspector on the right, and the log and
 * negotiation views along the bottom. The map is the only thing that grows; the
 * panels scroll.
 *
 * Selection, display toggles, and task placement are local UI state. Everything
 * about the simulation itself comes from `useSimulation`, which is the only
 * thing that talks to the backend.
 */

import { useCallback, useMemo, useState } from "react";
import { ControlBar, type DisplayKey } from "./components/ControlBar";
import { EventLogPanel } from "./components/EventLogPanel";
import { FleetOverview } from "./components/FleetOverview";
import { NegotiationPanel } from "./components/NegotiationPanel";
import { RobotInspector } from "./components/RobotInspector";
import { RobotList } from "./components/RobotList";
import { ScenarioEditor } from "./components/ScenarioEditor";
import { SimulationCanvas } from "./components/SimulationCanvas";
import { StatusBar } from "./components/StatusBar";
import { TaskPanel } from "./components/TaskPanel";
import { TemplatesPanel } from "./components/TemplatesPanel";
import { useSimulation } from "./state/useSimulation";
import { useDemo } from "./state/useDemo";
import { useTheme } from "./state/useTheme";

interface DisplayState {
  showFootprints: boolean;
  showRoutes: boolean;
  showTrails: boolean;
  showLabels: boolean;
  showTaskMarkers: boolean;
  showConflictCells: boolean;
}

const DEFAULT_DISPLAY: DisplayState = {
  showFootprints: false,
  showRoutes: true,
  showTrails: false,
  showLabels: false,
  showTaskMarkers: true,
  showConflictCells: true,
};

type BottomTab = "events" | "negotiation" | "tasks" | "templates" | "scenario";

export function App() {
  const sim = useSimulation();
  const demo = useDemo();
  const theme = useTheme();
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null);
  const [display, setDisplay] = useState<DisplayState>(DEFAULT_DISPLAY);
  const [placingTask, setPlacingTask] = useState(false);
  // The cell chosen on the map, shared by the canvas and the task form so a pick
  // made on one surface is the cell the other one submits.
  const [pickedCell, setPickedCell] = useState<{ cellX: number; cellY: number } | null>(null);
  const [tab, setTab] = useState<BottomTab>("events");

  const selectedRobot = useMemo(
    () => sim.telemetry?.robots.find((robot) => robot.robot_id === selectedRobotId) ?? null,
    [sim.telemetry, selectedRobotId],
  );

  /**
   * Whether the run still has work in it.
   *
   * A finished run leaves every robot idle, which is indistinguishable from a
   * frozen console unless it is called out, so the transport bar and the status
   * strip both say so and offer a restart.
   */
  const runComplete = useMemo(() => {
    const tasks = sim.snapshot?.tasks ?? [];
    if (tasks.length === 0) return false;
    return tasks.every((task) => task.status === "completed" || task.status === "cancelled");
  }, [sim.snapshot]);

  const toggle = useCallback((key: DisplayKey, value: boolean) => {
    setDisplay((current) => ({ ...current, [key]: value }));
  }, []);

  const beginPlacing = useCallback(() => {
    setPlacingTask((current) => !current);
  }, []);

  return (
    <div className="console">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <h1 className="brand-name">WATCHER</h1>
            <p className="brand-sub">INDUSTRIAL FLEET COMMAND CONSOLE</p>
          </div>
        </div>
        <div className="masthead-meta">
          <div className="theme-switch" role="group" aria-label="Colour theme">
            {theme.themes.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                title={candidate.note}
                className={
                  candidate.id === theme.id ? "theme-chip theme-chip--on" : "theme-chip"
                }
                onClick={() => theme.select(candidate.id)}
              >
                <span
                  className="theme-swatch"
                  style={{ background: candidate.palette.safe }}
                  aria-hidden="true"
                />
                {candidate.label}
              </button>
            ))}
          </div>
          <span>BACKEND {sim.health?.version ?? "--"}</span>
          <span>SCENARIO {sim.telemetry?.scenario ?? "--"}</span>
          <span>UNITS {sim.snapshot?.robots.length ?? 0}</span>
          <span>REV {sim.snapshot?.revision ?? 0}</span>
        </div>
      </header>

      <StatusBar
        simulationTimeS={sim.snapshot?.simulation_time_s ?? 0}
        revision={sim.snapshot?.revision ?? 0}
        lastEventSequence={sim.snapshot?.last_event_sequence ?? 0}
        scenario={sim.telemetry?.scenario ?? "--"}
        metrics={sim.metrics}
        telemetry={sim.telemetry}
        error={sim.error}
        runComplete={runComplete}
        notice={sim.notice}
        onDismissError={sim.clearError}
        onDismissNotice={sim.clearNotice}
      />

      <ControlBar
        running={sim.running}
        speed={sim.speed}
        busy={sim.busy}
        connection={sim.connection}
        scenario={sim.telemetry?.scenario ?? "--"}
        placingTask={placingTask}
        runComplete={runComplete}
        {...display}
        onStart={() => void sim.startOrReopen()}
        onPause={() => void sim.pause()}
        onStep={() => void sim.step()}
        onSpeed={(multiplier) => void sim.setSpeed(multiplier)}
        onReset={() => void sim.reset()}
        onDispatch={() => void sim.dispatch()}
        onRandomize={() => void sim.randomizeAssignment(0.75)}
        onToggle={toggle}
        onPlaceTask={beginPlacing}
      />

      <main className="workspace">
        <section className="map-pane">
          <SimulationCanvas
            snapshot={sim.snapshot}
            telemetry={sim.telemetry}
            selectedRobotId={selectedRobotId}
            onSelectRobot={setSelectedRobotId}
            placingTask={placingTask}
            onPlaceTask={(cell) => {
              setPickedCell(cell);
              setPlacingTask(false);
              setTab("tasks");
            }}
            running={sim.running}
            speed={sim.speed}
            now={sim.now}
            {...display}
          />
        </section>

        <aside className="side">
          <Panel title="UNIT INSPECTOR" className="panel--inspector">
            <RobotInspector
              robot={selectedRobot}
              snapshot={sim.snapshot}
              busy={sim.busy !== null}
              onFail={(robotId) => void sim.injectFailure(robotId)}
              onLoseLink={(robotId) => void sim.injectCommunicationLoss(robotId)}
              onRestore={(robotId) => void sim.restoreRobot(robotId)}
            />
          </Panel>

          <Panel title="FLEET" className="panel--fleet">
            <RobotList
              telemetry={sim.telemetry}
              selectedRobotId={selectedRobotId}
              onSelect={setSelectedRobotId}
            />
          </Panel>

          <Panel title="FLEET TELEMETRY" className="panel--telemetry">
            <FleetOverview telemetry={sim.telemetry} />
          </Panel>
        </aside>
      </main>

      <section className="dock">
        <nav className="dock-tabs" role="tablist">
          {(
            [
              ["events", `EVENT LOG (${sim.eventLog.rows.length})`],
              ["negotiation", `NEGOTIATION (${sim.eventLog.negotiations.length})`],
              ["tasks", `TASKS (${sim.snapshot?.tasks.length ?? 0})`],
              ["templates", "TEMPLATES"],
              ["scenario", "SCENARIO"],
            ] as [BottomTab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={tab === key ? "dock-tab dock-tab--on" : "dock-tab"}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>

        <div className="dock-body">
          {tab === "events" ? (
            <EventLogPanel
              rows={sim.eventLog.rows}
              cursor={sim.eventLog.cursor}
              paused={!sim.running}
              selectedRobotId={selectedRobotId}
              onSelectRobot={setSelectedRobotId}
            />
          ) : null}
          {tab === "negotiation" ? (
            <NegotiationPanel
              negotiations={sim.eventLog.negotiations}
              onSelectRobot={setSelectedRobotId}
            />
          ) : null}
          {tab === "tasks" ? (
            <TaskPanel
              snapshot={sim.snapshot}
              placingTask={placingTask}
              busy={sim.busy !== null}
              onBeginPlacing={beginPlacing}
              onCancelPlacing={() => setPlacingTask(false)}
              cell={pickedCell}
              onCellChange={setPickedCell}
              onCreateTask={(input) => void sim.createTask(input)}
            />
          ) : null}
          {tab === "templates" ? (
            <TemplatesPanel
              scenarios={sim.scenarios}
              active={sim.telemetry?.scenario ?? "normal"}
              busy={sim.busy !== null}
              demo={demo}
              onLoad={(name) => void sim.loadScenario({ name })}
              onStartDemo={(name, stages) => {
                setSelectedRobotId(null);
                void demo.start(name, stages, {
                  pause: async () => {
                    // The console's own transport, so the poll loop stops
                    // advancing ticks too. Pausing the backend alone left the
                    // run moving between stages.
                    if (sim.running) await sim.pause();
                  },
                  resume: async () => {
                    if (!sim.running) await sim.start();
                  },
                  refresh: sim.refreshNow,
                });
              }}
            />
          ) : null}
          {tab === "scenario" ? (
            <ScenarioEditor
              scenarios={sim.scenarios}
              active={sim.telemetry?.scenario ?? "normal"}
              busy={sim.busy !== null}
              onLoad={(input) => void sim.loadScenario(input)}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}

function Panel({
  title,
  className,
  children,
}: {
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={className ? `panel ${className}` : "panel"}>
      <h2 className="panel-title">{title}</h2>
      {children}
    </section>
  );
}
