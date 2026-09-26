/**
 * Wire types for the Watcher backend.
 *
 * Every shape here mirrors the JSON the backend actually returns. Nothing is
 * invented and nothing is reshaped: the UI reads the canonical snapshot,
 * metrics, and telemetry responses as-is.
 *
 * A note on `Robot`: the canonical contract deliberately carries no physical
 * geometry. Footprint and speed arrive separately in `RobotTelemetry`, straight
 * from the backend's `RobotProfile` registry, so the rendered footprint is the
 * same footprint the collision engine uses.
 */

export type GridCellType =
  | "free"
  | "obstacle"
  | "resource"
  | "charging"
  | "workstation"
  | "deadzone";

export interface GridCell {
  cell_x: number;
  cell_y: number;
  cell_type: GridCellType;
}

export interface Position2D {
  x: number;
  y: number;
}

export interface WorldState {
  width_m: number;
  height_m: number;
  cell_size_m: number;
  columns: number;
  rows: number;
  cells: GridCell[];
  revision: number;
}

export type RobotStatus =
  | "idle"
  | "active"
  | "blocked"
  | "charging"
  | "degraded"
  | "failed"
  | "offline";

export type CommunicationState = "online" | "degraded" | "lost";

export interface FailureInfo {
  kind: string;
  code: string;
  detected_at_s: number;
  detail?: string | null;
}

export interface Robot {
  robot_id: string;
  position: Position2D;
  battery_percent: number;
  capabilities: string[];
  workload: number;
  status: RobotStatus;
  current_task_id: string | null;
  communication_state: CommunicationState;
  failure: FailureInfo | null;
  last_updated_at_s: number;
}

export interface Task {
  task_id: string;
  target: Position2D;
  priority: number;
  required_capabilities: string[];
  estimated_duration_s: number;
  status: string;
  assigned_robot_id: string | null;
  created_at_s: number;
}

export type RouteStatus =
  | "proposed"
  | "active"
  | "blocked"
  | "replanned"
  | "completed"
  | "invalid";

export interface RoutePlan {
  route_id: string;
  robot_id: string;
  task_id: string;
  waypoints: Position2D[];
  strategy: string;
  status: RouteStatus;
  version: number;
  planned_at_s: number;
}

export interface Conflict {
  conflict_id: string;
  kind: string;
  severity: string;
  robot_ids: string[];
  task_ids: string[];
  position: Position2D;
  status: string;
  detected_at_s: number;
}

export interface SnapshotResponse {
  world: WorldState;
  robots: Robot[];
  routes: RoutePlan[];
  tasks: Task[];
  simulation_time_s: number;
  revision: number;
  last_event_sequence: number;
  controller_available: boolean;
}

export interface SystemMetrics {
  active_robots: number;
  failed_robots: number;
  communication_lost_robots: number;
  pending_tasks: number;
  completed_tasks: number;
  open_conflicts: number;
  detected_deadlocks: number;
  task_reassignments: number;
  average_battery_percent: number;
  average_allocation_latency_ms: number;
  event_throughput_per_s: number;
  controller_available: boolean;
  extra_metrics: Record<string, number>;
}

/** A closed set the backend derives; the UI must not invent other values. */
export const ROBOT_ACTIONS = [
  "IDLE",
  "MOVING",
  "WAITING",
  "BLOCKED",
  "CHARGING",
  "DEGRADED",
  "FAILED",
  "OFFLINE",
  "NEGOTIATING",
  "REPLANNING",
  "TASK_COMPLETED",
] as const;

export type RobotAction = (typeof ROBOT_ACTIONS)[number];

export interface RobotTelemetry {
  robot_id: string;
  width_cells: number;
  height_cells: number;
  speed_mps: number;
  battery_percent: number;
  battery_percent_per_cell: number;
  cell_x: number;
  cell_y: number;
  position_x: number;
  position_y: number;
  status: string;
  communication_state: CommunicationState;
  action: RobotAction;
  action_reason: string;
  workload: number;
  capabilities: string[];
  failure_code: string | null;
  task_id: string | null;
  route_id: string | null;
  route_status: string | null;
  destination_x: number | null;
  destination_y: number | null;
  progress: number;
  cells_travelled: number;
  remaining_cells: number;
  remaining_time_s: number;
  conflict_with: string[];
  conflict_detected_at_s: number | null;
  waiting_for_robot_id: string | null;
  waiting_since_s: number | null;
  /** Remaining timed placements as `[x_m, y_m, t_s]`, capped server side. */
  trail: [number, number, number][];
}

export interface TelemetryResponse {
  simulation_time_s: number;
  scenario: string;
  counts_by_action: Record<string, number>;
  /** The backend's own bands: `critical`, `low`, and `normal`. */
  battery_buckets: Record<string, number>;
  open_conflict_pairs: string[][];
  deadlocked_robot_ids: string[];
  controller_available: boolean;
  revision: number;
  last_event_sequence: number;
  robots: RobotTelemetry[];
}

export interface BackendEvent {
  sequence: number;
  event_type: string;
  producer: string;
  correlation_id: string;
  occurred_at_s: number;
  payload: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface CommandResponse {
  accepted: boolean;
  command_type: string;
  produced_event_types: string[];
  last_event_sequence: number;
  revision: number;
  simulation_time_s: number;
}

export interface ScenarioInfo {
  name: string;
  layout: string;
  description: string;
  columns: number;
  rows: number;
  robot_count: number;
  task_count: number;
}

export interface ScenarioListResponse {
  scenarios: ScenarioInfo[];
  grid_presets: [number, number][];
  fleet_presets: number[];
  active: string;
}

export interface ScenarioLoadedResponse {
  scenario: string;
  robots: number;
  tasks: string[];
  columns: number;
  rows: number;
  last_event_sequence: number;
  simulation_time_s: number;
}
