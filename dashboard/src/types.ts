export type GridCellType =
  | "free"
  | "obstacle"
  | "resource"
  | "charging"
  | "workstation"
  | "deadzone";

export type RobotStatus =
  | "idle"
  | "active"
  | "blocked"
  | "charging"
  | "degraded"
  | "failed"
  | "offline";

export type CommunicationState = "online" | "degraded" | "lost";
export type TaskStatus =
  | "pending"
  | "negotiating"
  | "assigned"
  | "in_progress"
  | "blocked"
  | "recovery"
  | "completed"
  | "cancelled";
export type RouteStatus =
  | "proposed"
  | "active"
  | "blocked"
  | "replanned"
  | "completed"
  | "invalid";
export type ConflictSeverity = "info" | "warning" | "critical";
export type ResolutionStatus = "open" | "resolving" | "resolved";
export type FailureKind = "actuator" | "sensor" | "compute" | "communication" | "battery" | "other";

export interface Position2D {
  x: number;
  y: number;
}

export interface GridCell {
  cell_x: number;
  cell_y: number;
  cell_type: GridCellType;
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

export interface FailureInfo {
  kind: FailureKind;
  code: string;
  detected_at_s: number;
  detail: string | null;
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
  status: TaskStatus;
  assigned_robot_id: string | null;
  created_at_s: number;
}

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
  kind: "collision_risk" | "right_of_way" | "resource";
  severity: ConflictSeverity;
  robot_ids: string[];
  task_ids: string[];
  position: Position2D;
  status: ResolutionStatus;
  detected_at_s: number;
}

export interface DeadlockReport {
  deadlock_id: string;
  cycle_robot_ids: string[];
  blocked_task_ids: string[];
  detected_at_s: number;
  confidence: number;
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

export interface SimulationSnapshot {
  simulation_time_s: number;
  revision: number;
  last_event_sequence: number;
  controller_available: boolean;
  world: WorldState;
  robots: Robot[];
  tasks: Task[];
  routes: RoutePlan[];
  conflicts: Conflict[];
  metrics: SystemMetrics;
}

export type EventType =
  | "TASK_CREATED"
  | "NEGOTIATION_STARTED"
  | "BID_SUBMITTED"
  | "TASK_ASSIGNED"
  | "TASK_REASSIGNED"
  | "ROUTE_REQUESTED"
  | "ROUTE_PLANNED"
  | "CONFLICT_DETECTED"
  | "DEADLOCK_DETECTED"
  | "ROUTE_REPLANNED"
  | "BATTERY_LOW"
  | "ROBOT_FAILED"
  | "COMMUNICATION_LOST"
  | "RECOVERY_STARTED"
  | "TASK_COMPLETED";

export interface DomainEvent {
  event_id: string;
  sequence: number;
  schema_version: 1;
  event_type: EventType;
  producer: string;
  correlation_id: string;
  occurred_at_s: number;
  payload: Record<string, unknown>;
}

export type StreamFrame =
  | { kind: "snapshot"; snapshot: SimulationSnapshot }
  | { kind: "event"; event: DomainEvent }
  | { kind: "cursor"; lastEventSequence: number };

export type CommandType =
  | "CREATE_TASK"
  | "INJECT_ROBOT_FAILURE"
  | "INJECT_COMMUNICATION_LOSS"
  | "RESTORE_ROBOT"
  | "PAUSE_SIMULATION"
  | "RESUME_SIMULATION"
  | "RESET_SIMULATION"
  | "SET_SIMULATION_SPEED";

export interface BaseCommand {
  command_id: string;
  schema_version: 1;
  command_type: CommandType;
  issued_at_s: number;
}

export type ControlCommand = BaseCommand &
  (
    | { command_type: "PAUSE_SIMULATION" | "RESUME_SIMULATION" }
    | { command_type: "RESET_SIMULATION"; seed: number }
    | { command_type: "SET_SIMULATION_SPEED"; multiplier: number }
    | { command_type: "INJECT_ROBOT_FAILURE"; robot_id: string; failure: FailureInfo }
    | { command_type: "RESTORE_ROBOT"; robot_id: string }
    | {
        command_type: "INJECT_COMMUNICATION_LOSS";
        robot_id: string;
        timeout_s: number;
      }
    | { command_type: "CREATE_TASK"; task: Task }
  );
