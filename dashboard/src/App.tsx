import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  BatteryCharging,
  ChevronRight,
  CircleDot,
  Gauge,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  Send,
  ShieldAlert,
  Signal,
  SlidersHorizontal,
  SquareStack,
  Target,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import {
  connectToEvents,
  getEvents,
  getHealth,
  getSnapshot,
  sendCommand,
} from "./api";
import WorldCanvas from "./WorldCanvas";
import {
  applyEventToDeadlockCycles,
  buildCreateTaskCommand,
  createDebouncedSnapshotRefresher,
  getActiveDeadlockCycles,
  getRuntimeSpeedMultiplier,
  mergeEvents,
  type CreateTaskInput,
  type DeadlockCycle,
} from "./state";
import type { ControlCommand, DomainEvent, Robot, SimulationSnapshot } from "./types";

type ConnectionState = "connecting" | "live" | "stale" | "offline";

function formatTime(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)} s`;
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(0)}%`;
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusTone(status: string): string {
  if (["failed", "critical", "offline", "lost"].includes(status)) return "critical";
  if (["blocked", "degraded", "warning", "recovery", "open"].includes(status)) return "warning";
  if (["active", "online", "completed", "resolved", "healthy"].includes(status)) return "healthy";
  return "neutral";
}

function eventLabel(event: DomainEvent): string {
  const labels: Record<DomainEvent["event_type"], string> = {
    TASK_CREATED: "Task entered queue",
    NEGOTIATION_STARTED: "Negotiation opened",
    BID_SUBMITTED: "Bid submitted",
    TASK_ASSIGNED: "Task assigned",
    TASK_REASSIGNED: "Task reassigned",
    ROUTE_REQUESTED: "Route requested",
    ROUTE_PLANNED: "Route planned",
    CONFLICT_DETECTED: "Conflict detected",
    DEADLOCK_DETECTED: "Deadlock detected",
    ROUTE_REPLANNED: "Route replanned",
    BATTERY_LOW: "Battery margin low",
    ROBOT_FAILED: "Robot failed",
    COMMUNICATION_LOST: "Communication lost",
    RECOVERY_STARTED: "Recovery started",
    TASK_COMPLETED: "Task completed",
  };
  return labels[event.event_type];
}

function App() {
  const [snapshot, setSnapshot] = useState<SimulationSnapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [commandMessage, setCommandMessage] = useState<string | null>(null);
  const [commandState, setCommandState] = useState<"idle" | "sending" | "success" | "error">("idle");
  const [isCommandBusy, setIsCommandBusy] = useState(false);
  const [speedMultiplier, setSpeedMultiplier] = useState(1);
  const [activeDeadlocks, setActiveDeadlocks] = useState<DeadlockCycle[]>([]);
  const [taskDraft, setTaskDraft] = useState<CreateTaskInput>({
    taskId: "",
    targetX: 0,
    targetY: 0,
    priority: 3,
    capability: "transport",
    estimatedDurationS: 60,
  });
  const [taskFormError, setTaskFormError] = useState<string | null>(null);
  const speedInitializedRef = useRef(false);
  const eventQueueRef = useRef<DomainEvent[]>([]);
  const eventFrameRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    let refreshInFlight = false;
    let refreshQueued = false;
    let hasSnapshot = false;

    const refreshSnapshot = async () => {
      if (disposed) return;
      if (refreshInFlight) {
        refreshQueued = true;
        return;
      }
      refreshInFlight = true;
      try {
        const nextSnapshot = await getSnapshot();
        if (disposed) return;
        hasSnapshot = true;
        setSnapshot(nextSnapshot);
        setLastSync(new Date());
        setError(null);
        setConnection("live");
      } catch (refreshError) {
        if (disposed) return;
        setConnection(hasSnapshot ? "stale" : "offline");
        setError(refreshError instanceof Error ? refreshError.message : "The runtime snapshot refresh failed.");
      } finally {
        refreshInFlight = false;
        if (refreshQueued && !disposed) {
          refreshQueued = false;
          void refreshSnapshot();
        }
      }
    };

    const refresher = createDebouncedSnapshotRefresher(refreshSnapshot, 150);
    const flushEventQueue = () => {
      const batch = eventQueueRef.current.splice(0);
      eventFrameRef.current = undefined;
      if (batch.length === 0) return;
      setEvents((current) => mergeEvents(current, batch));
      setActiveDeadlocks((current) => batch.reduce(applyEventToDeadlockCycles, current));
    };
    const enqueueEvent = (event: DomainEvent) => {
      eventQueueRef.current.push(event);
      if (eventFrameRef.current === undefined) eventFrameRef.current = window.requestAnimationFrame(flushEventQueue);
      setConnection("live");
      setLastSync(new Date());
      refresher.schedule();
    };

    const loadDashboard = async () => {
      if (disposed) return;
      setConnection(hasSnapshot ? "stale" : "connecting");
      try {
        await getHealth();
        const nextSnapshot = await getSnapshot();
        if (disposed) return;
        const hadSnapshot = hasSnapshot;
        hasSnapshot = true;
        setSnapshot(nextSnapshot);
        setLastSync(new Date());
        setError(null);
        setConnection("live");

        try {
          const missedEvents = await getEvents(nextSnapshot.last_event_sequence);
          if (!disposed && missedEvents.length > 0) {
            setEvents((current) => mergeEvents(current, missedEvents));
            if (!hadSnapshot) setActiveDeadlocks(getActiveDeadlockCycles(missedEvents));
          }
        } catch (eventsError) {
          if (!disposed) {
            setConnection("stale");
            setError(eventsError instanceof Error ? eventsError.message : "The event history could not be loaded.");
          }
        }

        socket = connectToEvents(
          nextSnapshot.last_event_sequence,
          enqueueEvent,
          () => {
            if (!disposed) setConnection("live");
          },
          () => {
            if (disposed) return;
            setConnection("stale");
            reconnectTimer = window.setTimeout(loadDashboard, 4000);
          },
          () => {
            if (disposed) return;
            setConnection("stale");
            setError("The event stream reported an invalid frame. Waiting for the next snapshot.");
          },
        );
      } catch (loadError) {
        if (disposed) return;
        setConnection(hasSnapshot ? "stale" : "offline");
        setError(loadError instanceof Error ? loadError.message : "The runtime API is unavailable.");
        reconnectTimer = window.setTimeout(loadDashboard, 5000);
      }
    };

    void loadDashboard();
    return () => {
      disposed = true;
      refresher.cancel();
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (eventFrameRef.current !== undefined) window.cancelAnimationFrame(eventFrameRef.current);
      eventFrameRef.current = undefined;
      eventQueueRef.current = [];
      socket?.close();
    };
  }, []);

  useEffect(() => {
    if (!snapshot || speedInitializedRef.current) return;
    setSpeedMultiplier(getRuntimeSpeedMultiplier(snapshot));
    speedInitializedRef.current = true;
  }, [snapshot]);

  const selectedRobot = useMemo<Robot | null>(
    () => snapshot?.robots.find((robot) => robot.robot_id === selectedRobotId) ?? null,
    [selectedRobotId, snapshot],
  );
  const recentEvents = useMemo(() => [...events].reverse().slice(0, 9), [events]);
  const visibleTasks = snapshot?.tasks.slice(0, 7) ?? [];
  const visibleRobots = snapshot?.robots.slice(0, 8) ?? [];
  const isPaused = snapshot?.metrics.extra_metrics.paused === 1;

  async function issueCommand(command: ControlCommand): Promise<boolean> {
    setIsCommandBusy(true);
    setCommandState("sending");
    setCommandMessage("Sending command…");
    try {
      await sendCommand(command);
      setCommandState("success");
      setCommandMessage("Command accepted by the command adapter.");
      return true;
    } catch (commandError) {
      setCommandState("error");
      setCommandMessage(`Command failed: ${commandError instanceof Error ? commandError.message : "request failed"}`);
      return false;
    } finally {
      setIsCommandBusy(false);
    }
  }

  function commandBase() {
    return {
      command_id: crypto.randomUUID(),
      schema_version: 1 as const,
      issued_at_s: snapshot?.simulation_time_s ?? 0,
    };
  }

  function injectFailure(): void {
    if (!selectedRobot) return;
    void issueCommand({
      ...commandBase(),
      command_type: "INJECT_ROBOT_FAILURE",
      robot_id: selectedRobot.robot_id,
      failure: {
        kind: "other",
        code: "operator-injected",
        detected_at_s: snapshot?.simulation_time_s ?? 0,
        detail: "Injected from the Watcher operations dashboard.",
      },
    });
  }

  function injectCommunicationLoss(): void {
    if (!selectedRobot) return;
    void issueCommand({
      ...commandBase(),
      command_type: "INJECT_COMMUNICATION_LOSS",
      robot_id: selectedRobot.robot_id,
      timeout_s: 5,
    });
  }

  function restoreRobot(): void {
    if (!selectedRobot) return;
    void issueCommand({
      ...commandBase(),
      command_type: "RESTORE_ROBOT",
      robot_id: selectedRobot.robot_id,
    });
  }

  function updateTaskDraft<K extends keyof CreateTaskInput>(field: K, value: CreateTaskInput[K]): void {
    setTaskDraft((current) => ({ ...current, [field]: value }));
    setTaskFormError(null);
  }

  async function createTask(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    try {
      const command = buildCreateTaskCommand(taskDraft, {
        commandId: crypto.randomUUID(),
        issuedAtS: snapshot?.simulation_time_s ?? 0,
      });
      const sent = await issueCommand(command);
      if (sent) {
        setTaskDraft({ taskId: "", targetX: 0, targetY: 0, priority: 3, capability: "transport", estimatedDurationS: 60 });
        setTaskFormError(null);
      }
    } catch (taskError) {
      setTaskFormError(taskError instanceof Error ? taskError.message : "Task could not be created.");
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to dashboard content</a>

      <header className="topbar">
        <div className="brand-lockup" aria-label="Watcher fleet operations">
          <div className="brand-mark" aria-hidden="true"><CircleDot size={22} strokeWidth={1.8} /></div>
          <div>
            <p className="eyebrow">Multi-robot operations</p>
            <p className="brand-name">WATCHER</p>
          </div>
        </div>
        <div className="topbar-actions">
          <div className={`connection-state connection-${connection}`} role="status" aria-live="polite">
            <span className="connection-dot" aria-hidden="true" />
            {connection === "live" ? "Live runtime" : connection === "stale" ? "Snapshot stale" : connection === "connecting" ? "Connecting" : "Runtime offline"}
          </div>
          <span className="environment-tag">OPS / 01</span>
        </div>
      </header>

      <main id="main-content" className="dashboard-main">
        <section className="page-intro" aria-labelledby="page-title">
          <div>
            <p className="section-kicker">Fleet control surface / live projection</p>
            <h1 id="page-title">Operations overview</h1>
            <p className="page-description">Observe fleet health, spatial activity, and event flow from the canonical simulation projection.</p>
          </div>
          <div className="sync-readout" aria-label="Last synchronization">
            <span className="sync-label">Last sync</span>
            <strong>{lastSync ? lastSync.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}</strong>
            <span className="sync-revision">rev {snapshot?.revision ?? "—"} · seq {snapshot?.last_event_sequence ?? "—"}</span>
          </div>
        </section>

        {error && (
          <div className="notice notice-warning" role="alert">
            <AlertTriangle size={17} aria-hidden="true" />
            <div><strong>Runtime connection needs attention.</strong><span>{error} The dashboard will retry automatically.</span></div>
            <button type="button" className="icon-button" aria-label="Dismiss connection notice" onClick={() => setError(null)}><X size={16} /></button>
          </div>
        )}

        <section className="metric-grid" aria-label="Fleet metrics">
          <MetricCard icon={Activity} label="Active robots" value={formatNumber(snapshot?.metrics.active_robots)} detail={snapshot ? `${snapshot.robots.length} total in projection` : "Awaiting snapshot"} tone="cyan" />
          <MetricCard icon={Gauge} label="Avg. battery" value={formatPercent(snapshot?.metrics.average_battery_percent)} detail="Fleet energy margin" tone="green" />
          <MetricCard icon={Target} label="Pending tasks" value={formatNumber(snapshot?.metrics.pending_tasks)} detail={`${formatNumber(snapshot?.metrics.completed_tasks)} completed`} tone="amber" />
          <MetricCard icon={ShieldAlert} label="Open conflicts" value={formatNumber(snapshot?.metrics.open_conflicts)} detail={`${formatNumber(snapshot?.metrics.detected_deadlocks)} deadlocks`} tone="red" />
          <MetricCard icon={Zap} label="Event rate" value={formatNumber(snapshot?.metrics.event_throughput_per_s)} detail="events / second" tone="blue" />
        </section>

        <section className="operations-layout" aria-label="Fleet operations">
          <section className="panel map-panel" aria-labelledby="map-title">
            <div className="panel-heading">
              <div>
                <p className="section-kicker">Spatial projection</p>
                <h2 id="map-title">Fleet world</h2>
              </div>
              <div className="panel-heading-actions">
                <span className={`status-chip ${snapshot ? (snapshot.controller_available ? "chip-healthy" : "chip-critical") : "chip-neutral"}`}><Signal size={13} />{snapshot ? (snapshot.controller_available ? "Controller available" : "Controller unavailable") : "Controller state unknown"}</span>
                <span className="status-chip chip-muted"><SquareStack size={13} />{snapshot?.world.revision ?? "—"} map revision</span>
              </div>
            </div>
            <WorldCanvas snapshot={snapshot} deadlockCycles={activeDeadlocks} selectedRobotId={selectedRobotId} onSelectRobot={setSelectedRobotId} />
          </section>

          <aside className="panel detail-panel" aria-labelledby="detail-title">
            <div className="panel-heading">
              <div><p className="section-kicker">Selection inspector</p><h2 id="detail-title">Robot detail</h2></div>
              {selectedRobot && <span className={`status-chip chip-${statusTone(selectedRobot.status)}`}>{titleCase(selectedRobot.status)}</span>}
            </div>
            {selectedRobot ? (
              <div className="robot-detail">
                <div className="detail-identity"><span className="detail-robot-mark"><CircleDot size={24} /></span><div><h3>{selectedRobot.robot_id}</h3><span className="muted-code">{selectedRobot.current_task_id ?? "no task assigned"}</span></div></div>
                <div className="detail-grid">
                  <DetailItem label="Battery" value={formatPercent(selectedRobot.battery_percent)} tone={selectedRobot.battery_percent < 20 ? "warning" : undefined} />
                  <DetailItem label="Workload" value={String(selectedRobot.workload)} />
                  <DetailItem label="Position" value={`${selectedRobot.position.x.toFixed(1)}, ${selectedRobot.position.y.toFixed(1)}`} />
                  <DetailItem label="Comms" value={titleCase(selectedRobot.communication_state)} tone={selectedRobot.communication_state === "lost" ? "critical" : undefined} />
                </div>
                <div className="detail-section"><span className="detail-label">Capabilities</span><div className="tag-list">{selectedRobot.capabilities.length ? selectedRobot.capabilities.map((capability) => <span className="tag" key={capability}>{titleCase(capability)}</span>) : <span className="muted">None reported</span>}</div></div>
                {selectedRobot.failure && <div className="failure-note"><AlertTriangle size={15} /><span>{selectedRobot.failure.detail ?? `${titleCase(selectedRobot.failure.kind)} failure reported.`}</span></div>}
                <div className="detail-actions"><button type="button" className="button button-secondary" onClick={injectFailure} disabled={isCommandBusy}><AlertTriangle size={14} />Inject failure</button><button type="button" className="button button-secondary" onClick={injectCommunicationLoss} disabled={isCommandBusy}><WifiOff size={14} />Drop comms</button><button type="button" className="button button-primary" onClick={restoreRobot} disabled={isCommandBusy}><RefreshCcw size={14} />Restore</button></div>
              </div>
            ) : (
              <div className="empty-inspector"><SlidersHorizontal size={22} /><strong>Select a robot</strong><span>Choose a marker on the map or a row in the roster to inspect telemetry.</span></div>
            )}
          </aside>
        </section>

        <section className="lower-grid">
          <section className="panel roster-panel" aria-labelledby="roster-title">
            <div className="panel-heading"><div><p className="section-kicker">Robot registry</p><h2 id="roster-title">Fleet roster</h2></div><span className="panel-count">{snapshot?.robots.length ?? "—"} units</span></div>
            {visibleRobots.length ? <div className="roster-list">{visibleRobots.map((robot) => <button type="button" className={`roster-row ${robot.robot_id === selectedRobotId ? "roster-row-selected" : ""}`} key={robot.robot_id} onClick={() => setSelectedRobotId(robot.robot_id)} aria-pressed={robot.robot_id === selectedRobotId}><span className={`status-orb orb-${statusTone(robot.status)}`} /><span className="roster-id">{robot.robot_id}</span><span className="roster-status">{titleCase(robot.status)}</span><span className="roster-battery"><BatteryCharging size={13} />{formatPercent(robot.battery_percent)}</span><ChevronRight size={14} /></button>)}</div> : <EmptyPanel label="No robot projection available" />}
            {snapshot && snapshot.robots.length > visibleRobots.length && <p className="list-footnote">Showing {visibleRobots.length} of {snapshot.robots.length} units. Use the map to inspect the full fleet.</p>}
          </section>

          <section className="panel tasks-panel" aria-labelledby="tasks-title">
            <div className="panel-heading"><div><p className="section-kicker">Work queue</p><h2 id="tasks-title">Task activity</h2></div><span className="panel-count">{snapshot?.tasks.length ?? "—"} tracked</span></div>
            {visibleTasks.length ? <div className="task-list">{visibleTasks.map((task) => <div className="task-row" key={task.task_id}><span className="priority-badge">P{task.priority}</span><div className="task-copy"><strong>{task.task_id}</strong><span>{task.assigned_robot_id ?? "Unassigned"} · {task.estimated_duration_s.toFixed(0)}s</span></div><span className={`status-chip chip-${statusTone(task.status)}`}>{titleCase(task.status)}</span></div>)}</div> : <EmptyPanel label="No tasks in the current projection" />}
          </section>

          <section className="panel events-panel" aria-labelledby="events-title">
            <div className="panel-heading"><div><p className="section-kicker">Canonical stream</p><h2 id="events-title">Event activity</h2></div><span className={`stream-indicator stream-${connection}`}><i />{events.length} received</span></div>
            {recentEvents.length ? <div className="event-list">{recentEvents.map((event) => <div className="event-row" key={event.event_id}><span className={`event-mark event-${statusTone(event.event_type)}`}><Activity size={13} /></span><div className="event-copy"><strong>{eventLabel(event)}</strong><span>#{event.sequence} · {event.correlation_id}</span></div><time>{formatTime(event.occurred_at_s)}</time></div>)}</div> : <EmptyPanel label="Waiting for the first canonical event" />}
          </section>
        </section>

        <section className="panel create-task-panel" aria-labelledby="create-task-title">
          <div className="panel-heading"><div><p className="section-kicker">Command adapter</p><h2 id="create-task-title">Create task</h2></div><span className="control-note"><Target size={14} />Canonical CREATE_TASK command</span></div>
          <form className="task-form" onSubmit={createTask}>
            <label>Task ID<input value={taskDraft.taskId} onChange={(event) => updateTaskDraft("taskId", event.target.value)} placeholder="task-001" pattern="[a-z][a-z0-9]*(?:-[a-z0-9]+)*" required /></label>
            <label>Target X<input type="number" min="0" step="0.1" value={taskDraft.targetX} onChange={(event) => updateTaskDraft("targetX", Number(event.target.value))} required /></label>
            <label>Target Y<input type="number" min="0" step="0.1" value={taskDraft.targetY} onChange={(event) => updateTaskDraft("targetY", Number(event.target.value))} required /></label>
            <label>Priority<select value={taskDraft.priority} onChange={(event) => updateTaskDraft("priority", Number(event.target.value))}><option value="1">1 · Low</option><option value="2">2</option><option value="3">3 · Normal</option><option value="4">4</option><option value="5">5 · Critical</option></select></label>
            <label>Capability<select value={taskDraft.capability} onChange={(event) => updateTaskDraft("capability", event.target.value as CreateTaskInput["capability"])}><option value="transport">Transport</option><option value="pick">Pick</option><option value="tug">Tug</option><option value="inspect">Inspect</option><option value="deliver">Deliver</option></select></label>
            <label>Duration (s)<input type="number" min="0.1" step="0.1" value={taskDraft.estimatedDurationS} onChange={(event) => updateTaskDraft("estimatedDurationS", Number(event.target.value))} required /></label>
            <button type="submit" className="button button-primary create-task-submit" disabled={!snapshot || isCommandBusy}><Send size={14} />Create task</button>
            {taskFormError && <p className="form-error" role="alert">{taskFormError}</p>}
          </form>
        </section>

        <section className="panel controls-panel" aria-labelledby="controls-title">
          <div className="panel-heading"><div><p className="section-kicker">Runtime command adapter</p><h2 id="controls-title">Simulation controls</h2></div><span className="control-note"><Wifi size={14} />Controller availability is read-only; no outage command is defined</span></div>
          <div className="controls-row">
            <div className="control-group"><span className="control-label">Transport</span><div className="button-row"><button type="button" className="button button-secondary" onClick={() => void issueCommand({ ...commandBase(), command_type: isPaused ? "RESUME_SIMULATION" : "PAUSE_SIMULATION" })} disabled={!snapshot || isCommandBusy}>{isPaused ? <Play size={15} /> : <Pause size={15} />}{isPaused ? "Resume" : "Pause"}</button><button type="button" className="button button-secondary" onClick={() => void issueCommand({ ...commandBase(), command_type: "RESET_SIMULATION", seed: 42 })} disabled={!snapshot || isCommandBusy}><RotateCcw size={15} />Reset</button></div></div>
            <label className="speed-control"><span className="control-label">Speed</span><select value={String(speedMultiplier)} onChange={(event) => { const nextSpeed = Number(event.target.value); setSpeedMultiplier(nextSpeed); void issueCommand({ ...commandBase(), command_type: "SET_SIMULATION_SPEED", multiplier: nextSpeed }); }} disabled={!snapshot || isCommandBusy}><option value="0.5">0.5×</option><option value="1">1.0×</option><option value="2">2.0×</option><option value="4">4.0×</option></select></label>
            <div className={`control-message control-message-${commandState}`} role="status" aria-live="polite">{commandMessage ?? "Ready for operator commands."}</div>
            <button type="button" className="button button-primary control-send" onClick={() => { setCommandState("idle"); setCommandMessage("Select a robot in the inspector to issue a targeted command."); }}><Send size={15} />Target command</button>
          </div>
        </section>
      </main>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, detail, tone }: { icon: typeof Activity; label: string; value: string; detail: string; tone: string }) {
  return <article className={`metric-card metric-${tone}`}><div className="metric-icon"><Icon size={17} /></div><div className="metric-copy"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function DetailItem({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className="detail-item"><span>{label}</span><strong className={tone ? `text-${tone}` : ""}>{value}</strong></div>;
}

function EmptyPanel({ label }: { label: string }) {
  return <div className="empty-panel"><span className="empty-line" />{label}</div>;
}

export default App;
