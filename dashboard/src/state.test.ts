import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyEventToDeadlockCycles,
  buildCreateTaskCommand,
  createDebouncedSnapshotRefresher,
  getActiveDeadlockCycles,
  getRuntimeSpeedMultiplier,
  mergeEvents,
} from "./state";
import type { DomainEvent, SimulationSnapshot } from "./types";

function event(sequence: number, eventType: DomainEvent["event_type"], payload: Record<string, unknown>): DomainEvent {
  return {
    event_id: `event-${sequence}`,
    sequence,
    schema_version: 1,
    event_type: eventType,
    producer: "runtime",
    correlation_id: "test-correlation",
    occurred_at_s: sequence,
    payload,
  };
}

const deadlock = event(1, "DEADLOCK_DETECTED", {
  report: {
    deadlock_id: "deadlock-001",
    cycle_robot_ids: ["robot-001", "robot-002"],
    blocked_task_ids: ["task-001"],
  },
});

describe("live dashboard state", () => {
  it("keeps an active deadlock until a related recovery or task resolution", () => {
    const detected = getActiveDeadlockCycles([deadlock]);
    expect(detected).toHaveLength(1);
    expect(applyEventToDeadlockCycles(detected, event(2, "RECOVERY_STARTED", {
      action: { target_robot_ids: ["robot-002"], affected_task_ids: [] },
    }))).toHaveLength(0);
    expect(applyEventToDeadlockCycles(detected, event(3, "TASK_COMPLETED", { task_id: "task-001", robot_id: "robot-001" }))).toHaveLength(0);
    expect(applyEventToDeadlockCycles(detected, event(4, "RECOVERY_STARTED", {
      action: { target_robot_ids: ["robot-009"], affected_task_ids: [] },
    }))).toHaveLength(1);
  });

  it("deduplicates and orders the event log", () => {
    const older = event(1, "TASK_CREATED", {});
    const newer = event(2, "TASK_COMPLETED", {});
    const merged = mergeEvents([newer, older], [newer]);
    expect(merged.map((item) => item.sequence)).toEqual([1, 2]);
  });

  it("debounces event bursts into one snapshot refresh", () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    const refresher = createDebouncedSnapshotRefresher(refresh, 100);
    refresher.schedule();
    refresher.schedule();
    vi.advanceTimersByTime(99);
    expect(refresh).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    refresher.cancel();
    vi.useRealTimers();
  });

  it("builds a canonical CREATE_TASK payload", () => {
    const command = buildCreateTaskCommand({
      taskId: "task-042",
      targetX: 12,
      targetY: 8,
      priority: 4,
      capability: "transport",
      estimatedDurationS: 45,
    }, { commandId: "command-042", issuedAtS: 7 });
    expect(command).toEqual({
      command_id: "command-042",
      schema_version: 1,
      command_type: "CREATE_TASK",
      issued_at_s: 7,
      task: {
        task_id: "task-042",
        target: { x: 12, y: 8 },
        priority: 4,
        required_capabilities: ["transport"],
        estimated_duration_s: 45,
        status: "pending",
        assigned_robot_id: null,
        created_at_s: 7,
      },
    });
  });

  it("reads the optional runtime speed metric without inventing a default value", () => {
    const snapshot = { metrics: { extra_metrics: { simulation_speed_multiplier: 2 } } } as unknown as SimulationSnapshot;
    expect(getRuntimeSpeedMultiplier(snapshot)).toBe(2);
    expect(getRuntimeSpeedMultiplier({ metrics: { extra_metrics: {} } } as unknown as SimulationSnapshot)).toBe(1);
    expect(getRuntimeSpeedMultiplier(null)).toBe(1);
  });
});

afterEach(() => {
  vi.useRealTimers();
});
