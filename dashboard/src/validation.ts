import type {
  CommunicationState,
  Conflict,
  DomainEvent,
  FailureKind,
  GridCell,
  GridCellType,
  Position2D,
  Robot,
  RoutePlan,
  SimulationSnapshot,
  SystemMetrics,
  Task,
} from "./types";

const gridCellTypes = new Set<GridCellType>(["free", "obstacle", "resource", "charging", "workstation", "deadzone"]);
const robotStatuses = new Set<Robot["status"]>(["idle", "active", "blocked", "charging", "degraded", "failed", "offline"]);
const communicationStates = new Set<CommunicationState>(["online", "degraded", "lost"]);
const failureKinds = new Set<FailureKind>(["actuator", "sensor", "compute", "communication", "battery", "other"]);
const conflictKinds = new Set<Conflict["kind"]>(["collision_risk", "right_of_way", "resource"]);
const conflictSeverities = new Set<Conflict["severity"]>(["info", "warning", "critical"]);
const resolutionStatuses = new Set<Conflict["status"]>(["open", "resolving", "resolved"]);
const taskStatuses = new Set<Task["status"]>(["pending", "negotiating", "assigned", "in_progress", "blocked", "recovery", "completed", "cancelled"]);
const routeStatuses = new Set<RoutePlan["status"]>(["proposed", "active", "blocked", "replanned", "completed", "invalid"]);
const eventTypes = new Set<DomainEvent["event_type"]>([
  "TASK_CREATED", "NEGOTIATION_STARTED", "BID_SUBMITTED", "TASK_ASSIGNED", "TASK_REASSIGNED",
  "ROUTE_REQUESTED", "ROUTE_PLANNED", "CONFLICT_DETECTED", "DEADLOCK_DETECTED", "ROUTE_REPLANNED",
  "BATTERY_LOW", "ROBOT_FAILED", "COMMUNICATION_LOST", "RECOVERY_STARTED", "TASK_COMPLETED",
]);

type RecordValue = Record<string, unknown>;

function asRecord(value: unknown, label: string): RecordValue {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as RecordValue;
}

function asArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${label} must be a non-empty string`);
  return value;
}

function asFiniteNumber(value: unknown, label: string, minimum = Number.NEGATIVE_INFINITY): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function asNonNegativeInteger(value: unknown, label: string, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) throw new Error(`${label} must be an integer >= ${minimum}`);
  return value as number;
}

function asStringArray(value: unknown, label: string): string[] {
  return asArray(value, label).map((item, index) => asString(item, `${label}[${index}]`));
}

function asPosition(value: unknown, label: string): Position2D {
  const record = asRecord(value, label);
  return { x: asFiniteNumber(record.x, `${label}.x`, 0), y: asFiniteNumber(record.y, `${label}.y`, 0) };
}

function asGridCell(value: unknown, label: string): GridCell {
  const record = asRecord(value, label);
  const cellType = asString(record.cell_type, `${label}.cell_type`) as GridCellType;
  if (!gridCellTypes.has(cellType)) throw new Error(`${label}.cell_type is not a canonical grid cell type`);
  return {
    cell_x: asNonNegativeInteger(record.cell_x, `${label}.cell_x`),
    cell_y: asNonNegativeInteger(record.cell_y, `${label}.cell_y`),
    cell_type: cellType,
  };
}

function asRobot(value: unknown, label: string): Robot {
  const record = asRecord(value, label);
  const status = asString(record.status, `${label}.status`) as Robot["status"];
  const communicationState = asString(record.communication_state, `${label}.communication_state`) as CommunicationState;
  if (!robotStatuses.has(status)) throw new Error(`${label}.status is not a canonical robot status`);
  if (!communicationStates.has(communicationState)) throw new Error(`${label}.communication_state is not a canonical communication state`);
  const failure = record.failure === null ? null : record.failure === undefined ? null : asRecord(record.failure, `${label}.failure`);
  const batteryPercent = asFiniteNumber(record.battery_percent, `${label}.battery_percent`, 0);
  if (batteryPercent > 100) throw new Error(`${label}.battery_percent must be <= 100`);
  return {
    robot_id: asString(record.robot_id, `${label}.robot_id`),
    position: asPosition(record.position, `${label}.position`),
    battery_percent: batteryPercent,
    capabilities: asStringArray(record.capabilities ?? [], `${label}.capabilities`),
    workload: asNonNegativeInteger(record.workload, `${label}.workload`),
    status,
    current_task_id: record.current_task_id === null || record.current_task_id === undefined ? null : asString(record.current_task_id, `${label}.current_task_id`),
    communication_state: communicationState,
    failure: failure === null ? null : (() => {
      const kind = asString(failure.kind, `${label}.failure.kind`) as FailureKind;
      if (!failureKinds.has(kind)) throw new Error(`${label}.failure.kind is not a canonical failure kind`);
      return {
        kind,
        code: asString(failure.code, `${label}.failure.code`),
        detected_at_s: asFiniteNumber(failure.detected_at_s, `${label}.failure.detected_at_s`, 0),
        detail: failure.detail === null || failure.detail === undefined ? null : asString(failure.detail, `${label}.failure.detail`),
      };
    })(),
    last_updated_at_s: asFiniteNumber(record.last_updated_at_s, `${label}.last_updated_at_s`, 0),
  };
}

function asTask(value: unknown, label: string): Task {
  const record = asRecord(value, label);
  const status = asString(record.status, `${label}.status`) as Task["status"];
  if (!taskStatuses.has(status)) throw new Error(`${label}.status is not a canonical task status`);
  const priority = asNonNegativeInteger(record.priority, `${label}.priority`, 1);
  if (priority > 5) throw new Error(`${label}.priority must be <= 5`);
  return {
    task_id: asString(record.task_id, `${label}.task_id`),
    target: asPosition(record.target, `${label}.target`),
    priority,
    required_capabilities: asStringArray(record.required_capabilities ?? [], `${label}.required_capabilities`),
    estimated_duration_s: asFiniteNumber(record.estimated_duration_s, `${label}.estimated_duration_s`, Number.EPSILON),
    status,
    assigned_robot_id: record.assigned_robot_id === null || record.assigned_robot_id === undefined ? null : asString(record.assigned_robot_id, `${label}.assigned_robot_id`),
    created_at_s: asFiniteNumber(record.created_at_s, `${label}.created_at_s`, 0),
  };
}

function asRoute(value: unknown, label: string): RoutePlan {
  const record = asRecord(value, label);
  const status = asString(record.status, `${label}.status`) as RoutePlan["status"];
  if (!routeStatuses.has(status)) throw new Error(`${label}.status is not a canonical route status`);
  const waypoints = asArray(record.waypoints, `${label}.waypoints`).map((waypoint, index) => asPosition(waypoint, `${label}.waypoints[${index}]`));
  if (waypoints.length < 2) throw new Error(`${label}.waypoints must contain at least two points`);
  return {
    route_id: asString(record.route_id, `${label}.route_id`),
    robot_id: asString(record.robot_id, `${label}.robot_id`),
    task_id: asString(record.task_id, `${label}.task_id`),
    waypoints,
    strategy: asString(record.strategy, `${label}.strategy`),
    status,
    version: asNonNegativeInteger(record.version, `${label}.version`, 1),
    planned_at_s: asFiniteNumber(record.planned_at_s, `${label}.planned_at_s`, 0),
  };
}

function asConflict(value: unknown, label: string): Conflict {
  const record = asRecord(value, label);
  const kind = asString(record.kind, `${label}.kind`) as Conflict["kind"];
  const severity = asString(record.severity, `${label}.severity`) as Conflict["severity"];
  const status = asString(record.status, `${label}.status`) as Conflict["status"];
  if (!conflictKinds.has(kind)) throw new Error(`${label}.kind is not a canonical conflict kind`);
  if (!conflictSeverities.has(severity)) throw new Error(`${label}.severity is not a canonical conflict severity`);
  if (!resolutionStatuses.has(status)) throw new Error(`${label}.status is not a canonical resolution status`);
  const robotIds = asStringArray(record.robot_ids, `${label}.robot_ids`);
  if (robotIds.length < 2) throw new Error(`${label}.robot_ids must contain at least two robots`);
  return {
    conflict_id: asString(record.conflict_id, `${label}.conflict_id`),
    kind,
    severity,
    robot_ids: robotIds,
    task_ids: asStringArray(record.task_ids ?? [], `${label}.task_ids`),
    position: asPosition(record.position, `${label}.position`),
    status,
    detected_at_s: asFiniteNumber(record.detected_at_s, `${label}.detected_at_s`, 0),
  };
}

function asMetrics(value: unknown, label: string): SystemMetrics {
  const record = asRecord(value, label);
  const numericKeys = [
    "active_robots", "failed_robots", "communication_lost_robots", "pending_tasks", "completed_tasks",
    "open_conflicts", "detected_deadlocks", "task_reassignments", "average_battery_percent",
    "average_allocation_latency_ms", "event_throughput_per_s",
  ] as const;
  const metrics = Object.fromEntries(numericKeys.map((key) => [key, asFiniteNumber(record[key], `${label}.${key}`, 0)])) as unknown as SystemMetrics;
  for (const key of numericKeys.slice(0, 8)) {
    if (!Number.isInteger(metrics[key])) throw new Error(`${label}.${key} must be an integer`);
  }
  if (metrics.average_battery_percent > 100) throw new Error(`${label}.average_battery_percent must be <= 100`);
  const extraRecord = asRecord(record.extra_metrics ?? {}, `${label}.extra_metrics`);
  const extraMetrics = Object.fromEntries(Object.entries(extraRecord).map(([key, item]) => [key, asFiniteNumber(item, `${label}.extra_metrics.${key}`)]));
  return { ...metrics, controller_available: typeof record.controller_available === "boolean" ? record.controller_available : (() => { throw new Error(`${label}.controller_available must be a boolean`); })(), extra_metrics: extraMetrics };
}

export function parseSnapshot(value: unknown): SimulationSnapshot {
  const record = asRecord(value, "snapshot");
  const worldRecord = asRecord(record.world, "snapshot.world");
  const world = {
    width_m: asFiniteNumber(worldRecord.width_m, "snapshot.world.width_m", Number.EPSILON),
    height_m: asFiniteNumber(worldRecord.height_m, "snapshot.world.height_m", Number.EPSILON),
    cell_size_m: asFiniteNumber(worldRecord.cell_size_m, "snapshot.world.cell_size_m", Number.EPSILON),
    columns: asNonNegativeInteger(worldRecord.columns, "snapshot.world.columns", 1),
    rows: asNonNegativeInteger(worldRecord.rows, "snapshot.world.rows", 1),
    cells: asArray(worldRecord.cells ?? [], "snapshot.world.cells").map((cell, index) => asGridCell(cell, `snapshot.world.cells[${index}]`)),
    revision: asNonNegativeInteger(worldRecord.revision, "snapshot.world.revision"),
  };
  for (const cell of world.cells) {
    if (cell.cell_x >= world.columns || cell.cell_y >= world.rows) throw new Error("snapshot.world cell coordinates must be inside world bounds");
  }
  const revision = asNonNegativeInteger(record.revision, "snapshot.revision");
  const lastEventSequence = asNonNegativeInteger(record.last_event_sequence, "snapshot.last_event_sequence");
  if (lastEventSequence < revision) throw new Error("snapshot.last_event_sequence cannot be lower than revision");
  return {
    simulation_time_s: asFiniteNumber(record.simulation_time_s, "snapshot.simulation_time_s", 0),
    revision,
    last_event_sequence: lastEventSequence,
    controller_available: typeof record.controller_available === "boolean" ? record.controller_available : (() => { throw new Error("snapshot.controller_available must be a boolean"); })(),
    world,
    robots: asArray(record.robots ?? [], "snapshot.robots").map((robot, index) => asRobot(robot, `snapshot.robots[${index}]`)),
    tasks: asArray(record.tasks ?? [], "snapshot.tasks").map((task, index) => asTask(task, `snapshot.tasks[${index}]`)),
    routes: asArray(record.routes ?? [], "snapshot.routes").map((route, index) => asRoute(route, `snapshot.routes[${index}]`)),
    conflicts: asArray(record.conflicts ?? [], "snapshot.conflicts").map((conflict, index) => asConflict(conflict, `snapshot.conflicts[${index}]`)),
    metrics: asMetrics(record.metrics, "snapshot.metrics"),
  };
}

export function parseEvent(value: unknown): DomainEvent {
  const record = asRecord(value, "event");
  const eventType = asString(record.event_type, "event.event_type") as DomainEvent["event_type"];
  if (!eventTypes.has(eventType)) throw new Error(`event.event_type ${eventType} is not a canonical event type`);
  return {
    event_id: asString(record.event_id, "event.event_id"),
    sequence: asNonNegativeInteger(record.sequence, "event.sequence", 1),
    schema_version: record.schema_version === 1 ? 1 : (() => { throw new Error("event.schema_version must be 1"); })(),
    event_type: eventType,
    producer: asString(record.producer, "event.producer"),
    correlation_id: asString(record.correlation_id, "event.correlation_id"),
    occurred_at_s: asFiniteNumber(record.occurred_at_s, "event.occurred_at_s", 0),
    payload: asRecord(record.payload, "event.payload"),
  };
}

export function parseEvents(value: unknown): DomainEvent[] {
  return asArray(value, "events").map((event) => parseEvent(event));
}

export function parseHealth(value: unknown): { status: string; service: string; version: string } {
  const record = asRecord(value, "health");
  return { status: asString(record.status, "health.status"), service: asString(record.service, "health.service"), version: asString(record.version, "health.version") };
}
