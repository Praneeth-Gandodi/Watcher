import { useMemo, useState } from "react";

import {
  formatCount,
  formatDecimal,
  formatMilliseconds,
  formatPercent,
  formatSeconds,
  titleCase,
} from "../format";
import {
  bidsFromEvents,
  eventTone,
  performanceFacts,
  recoveriesFromEvents,
  taskTone,
} from "../selectors";
import type { DeadlockCycle, PerformanceFacts } from "../selectors";
import type { Conflict, DomainEvent, SimulationSnapshot, Task } from "../types";
import { EmptyState, Metric, StatusPill } from "./Primitives";
import { Roster } from "./Roster";

type DockTab = "roster" | "tasks" | "events" | "bids" | "safety" | "performance";

const TABS: Array<{ key: DockTab; label: string }> = [
  { key: "roster", label: "Roster" },
  { key: "tasks", label: "Tasks" },
  { key: "events", label: "Events" },
  { key: "bids", label: "Bids" },
  { key: "safety", label: "Safety" },
  { key: "performance", label: "Performance" },
];

export interface DockProps {
  snapshot: SimulationSnapshot | null;
  events: DomainEvent[];
  deadlocks: DeadlockCycle[];
  selectedRobotId: string | null;
  onSelectRobot: (robotId: string) => void;
}

export function Dock({ snapshot, events, deadlocks, selectedRobotId, onSelectRobot }: DockProps) {
  const [tab, setTab] = useState<DockTab>("roster");
  const facts = useMemo(() => performanceFacts(snapshot), [snapshot]);

  const counts: Record<DockTab, string | null> = {
    roster: snapshot ? String(snapshot.robots.length) : null,
    tasks: snapshot ? String(snapshot.tasks.length) : null,
    events: String(events.length),
    bids: null,
    safety: snapshot ? String(snapshot.conflicts.length + deadlocks.length) : null,
    performance: null,
  };

  return (
    <section className="dock" aria-label="Fleet detail panels">
      <div className="dock__tabs" role="tablist" aria-label="Detail panels">
        {TABS.map((option) => (
          <button
            key={option.key}
            type="button"
            role="tab"
            id={`tab-${option.key}`}
            aria-selected={tab === option.key}
            aria-controls={`panel-${option.key}`}
            className={`dock__tab${tab === option.key ? " dock__tab--active" : ""}`}
            onClick={() => setTab(option.key)}
          >
            {option.label}
            {counts[option.key] ? <span className="dock__count">{counts[option.key]}</span> : null}
          </button>
        ))}
      </div>

      <div
        className="dock__panel"
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
      >
        {tab === "roster" ? (
          <Roster
            robots={snapshot?.robots ?? []}
            selectedRobotId={selectedRobotId}
            onSelect={onSelectRobot}
          />
        ) : null}
        {tab === "tasks" ? <TaskTable tasks={snapshot?.tasks ?? []} /> : null}
        {tab === "events" ? <EventStream events={events} /> : null}
        {tab === "bids" ? <BidStream events={events} /> : null}
        {tab === "safety" ? (
          <SafetyPanel
            conflicts={snapshot?.conflicts ?? []}
            deadlocks={deadlocks}
            events={events}
          />
        ) : null}
        {tab === "performance" ? <PerformancePanel facts={facts} snapshot={snapshot} /> : null}
      </div>
    </section>
  );
}

function TaskTable({ tasks }: { tasks: Task[] }) {
  const [text, setText] = useState("");
  const visible = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return tasks
      .filter(
        (task) =>
          needle === "" ||
          task.task_id.includes(needle) ||
          (task.assigned_robot_id?.includes(needle) ?? false),
      )
      .sort((left, right) => left.task_id.localeCompare(right.task_id));
  }, [tasks, text]);

  return (
    <div className="table">
      <div className="table__controls">
        <label className="search">
          <span className="visually-hidden">Filter tasks</span>
          <input
            type="search"
            value={text}
            placeholder="Filter by task or robot"
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <span className="muted">
          {visible.length} of {tasks.length} tasks
        </span>
      </div>
      {visible.length === 0 ? (
        <EmptyState title="No tasks tracked" detail="The runtime has not published any work yet." />
      ) : (
        <div className="table__scroll">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Status</th>
                <th scope="col">Priority</th>
                <th scope="col">Robot</th>
                <th scope="col">Target</th>
                <th scope="col">Estimate</th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, 300).map((task) => (
                <tr key={task.task_id}>
                  <td className="mono">{task.task_id}</td>
                  <td>
                    <StatusPill tone={taskTone(task)}>{titleCase(task.status)}</StatusPill>
                  </td>
                  <td className="mono">P{task.priority}</td>
                  <td className="mono">{task.assigned_robot_id ?? "—"}</td>
                  <td className="mono">
                    {task.target.x.toFixed(0)}, {task.target.y.toFixed(0)}
                  </td>
                  <td className="mono">{formatSeconds(task.estimated_duration_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length > 300 ? (
            <p className="list-footnote">
              Showing the first 300 of {visible.length} tasks. Narrow the filter to see the rest.
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}

const EVENT_LABELS: Record<DomainEvent["event_type"], string> = {
  TASK_CREATED: "Task queued",
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

function EventStream({ events }: { events: DomainEvent[] }) {
  const recent = useMemo(() => [...events].reverse().slice(0, 200), [events]);
  if (recent.length === 0) {
    return (
      <EmptyState
        title="No events yet"
        detail="Canonical events appear here as the runtime publishes them."
      />
    );
  }
  return (
    <ul className="feed">
      {recent.map((event) => (
        <li key={event.event_id} className="feed__row">
          <StatusPill tone={eventTone(event)}>{EVENT_LABELS[event.event_type]}</StatusPill>
          <span className="mono feed__meta">
            #{event.sequence} · {event.correlation_id}
          </span>
          <span className="feed__producer">{event.producer}</span>
          <span className="mono feed__time">{formatSeconds(event.occurred_at_s)}</span>
        </li>
      ))}
    </ul>
  );
}

function BidStream({ events }: { events: DomainEvent[] }) {
  const bids = useMemo(() => bidsFromEvents(events), [events]);
  if (bids.length === 0) {
    return (
      <EmptyState
        title="No bids in the retained window"
        detail="Bids are published by the negotiation layer; this is a view over that event stream."
      />
    );
  }
  return (
    <div className="table__scroll">
      <table className="grid">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Robot</th>
            <th scope="col">Total</th>
            <th scope="col">Distance</th>
            <th scope="col">Battery</th>
            <th scope="col">Workload</th>
            <th scope="col">Est. finish</th>
          </tr>
        </thead>
        <tbody>
          {bids.map((bid) => (
            <tr key={`${bid.bidId}-${bid.sequence}`}>
              <td className="mono">{bid.taskId}</td>
              <td className="mono">{bid.robotId}</td>
              <td className="mono">{formatDecimal(bid.totalCost)}</td>
              <td className="mono">{formatDecimal(bid.distanceCost)}</td>
              <td className="mono">{formatDecimal(bid.batteryCost)}</td>
              <td className="mono">{formatDecimal(bid.workloadCost)}</td>
              <td className="mono">{formatSeconds(bid.estimatedCompletionTimeS)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SafetyPanel({
  conflicts,
  deadlocks,
  events,
}: {
  conflicts: Conflict[];
  deadlocks: DeadlockCycle[];
  events: DomainEvent[];
}) {
  const recoveries = useMemo(() => recoveriesFromEvents(events, 40), [events]);

  if (conflicts.length === 0 && deadlocks.length === 0 && recoveries.length === 0) {
    return (
      <EmptyState
        title="No active safety events"
        detail="Conflicts, deadlock cycles, and recovery actions appear here when they occur."
      />
    );
  }

  return (
    <div className="safety">
      {deadlocks.length > 0 ? (
        <div className="safety__group">
          <p className="kicker">Deadlock cycles</p>
          <ul className="feed">
            {deadlocks.map((cycle) => (
              <li key={cycle.deadlockId} className="feed__row">
                <StatusPill tone="crit">Deadlock</StatusPill>
                <span className="mono feed__meta">{cycle.robotIds.join(" → ")}</span>
                {cycle.confidence !== null ? (
                  <span className="muted">confidence {formatPercent(cycle.confidence * 100)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {conflicts.length > 0 ? (
        <div className="safety__group">
          <p className="kicker">Open conflicts</p>
          <ul className="feed">
            {conflicts.map((conflict) => (
              <li key={conflict.conflict_id} className="feed__row">
                <StatusPill tone={conflict.severity === "critical" ? "crit" : "warn"}>
                  {titleCase(conflict.kind)}
                </StatusPill>
                <span className="mono feed__meta">{conflict.robot_ids.join(", ")}</span>
                <span className="feed__producer">
                  {conflict.position.x.toFixed(0)}, {conflict.position.y.toFixed(0)} m
                </span>
                <span className="mono feed__time">{titleCase(conflict.status)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {recoveries.length > 0 ? (
        <div className="safety__group">
          <p className="kicker">Recovery actions</p>
          <ul className="feed">
            {recoveries.map((record) => (
              <li key={`${record.actionId}-${record.sequence}`} className="feed__row feed__row--stack">
                <span className="feed__row-main">
                  <StatusPill tone="info">{titleCase(record.actionType)}</StatusPill>
                  <span className="mono feed__meta">{record.targetRobotIds.join(", ")}</span>
                  <span className="mono feed__time">{formatSeconds(record.occurredAtS)}</span>
                </span>
                <span className="feed__reason">{record.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function PerformancePanel({
  facts,
  snapshot,
}: {
  facts: PerformanceFacts;
  snapshot: SimulationSnapshot | null;
}) {
  if (!snapshot) {
    return <EmptyState title="No telemetry yet" detail="Performance figures appear with the first snapshot." />;
  }
  const metrics = snapshot.metrics;

  return (
    <div className="performance">
      <div className="performance__metrics">
        <Metric
          label="Route efficiency"
          value={facts.routeEfficiency === null ? "—" : `${facts.routeEfficiency.toFixed(2)}×`}
          detail="travelled distance over ideal distance"
          tone="info"
        />
        <Metric
          label="Tick cost"
          value={formatMilliseconds(facts.tickLatencyMs)}
          detail={`${metrics.extra_metrics.fleet_size ?? "—"} robots simulated`}
          tone="ok"
        />
        <Metric
          label="Planner latency"
          value={formatMilliseconds(facts.plannerLatencyMs)}
          detail="per route plan"
          tone="ok"
        />
        <Metric
          label="Allocation latency"
          value={formatMilliseconds(facts.allocationLatencyMs)}
          detail="per negotiation round"
          tone="ok"
        />
        <Metric
          label="Coordinator outages"
          value={facts.controllerOutages === null ? "—" : formatCount(facts.controllerOutages)}
          detail={
            facts.controllerOutageSeconds === null
              ? "no outage data"
              : `${formatSeconds(facts.controllerOutageSeconds)} without coordination`
          }
          tone={facts.controllerOutages ? "warn" : "ok"}
        />
        <Metric
          label="Reassignments"
          value={formatCount(metrics.task_reassignments)}
          detail="work migrated between robots"
          tone="info"
        />
        <Metric
          label="Deadlocks resolved"
          value={facts.deadlocksResolved === null ? "—" : formatCount(facts.deadlocksResolved)}
          detail="cycles broken by recovery"
          tone={facts.deadlocksResolved ? "warn" : "ok"}
        />
        <Metric
          label="Returns to charger"
          value={facts.returnsToCharger === null ? "—" : formatCount(facts.returnsToCharger)}
          detail={`${formatCount(facts.batteryLowEvents)} low-battery events`}
          tone="info"
        />
      </div>

      {facts.stageCosts.length > 0 ? (
        <div className="performance__stages">
          <p className="kicker">Where the tick goes</p>
          <ul className="stages">
            {facts.stageCosts.map((stage) => {
              const total = facts.stageCosts.reduce((sum, item) => sum + item.ms, 0) || 1;
              const share = Math.min(100, (stage.ms / total) * 100);
              return (
                <li key={stage.stage} className="stages__row">
                  <span className="stages__label">{titleCase(stage.stage)}</span>
                  <span className="stages__bar">
                    <span className="stages__fill" style={{ width: `${share}%` }} />
                  </span>
                  <span className="mono stages__value">{formatMilliseconds(stage.ms)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
