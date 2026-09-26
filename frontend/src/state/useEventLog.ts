/**
 * Event log state: incremental reads, a real cursor, and filterable rows.
 *
 * The log is built from canonical events only. A new scenario rebuilds the
 * stream from sequence 0, so the cursor is reset rather than carried over, and
 * rows are de-duplicated by sequence so a repeated poll can never double-print a
 * line.
 *
 * Payloads are nested exactly as the contracts define them: a bid arrives as
 * `{"bid": {...}}`, an assignment as `{"assignment": {...}}`, a recovery as
 * `{"action": {...}}`, and a conflict as `{"conflict": {...}}`. Nothing here
 * re-shapes a payload, it only reads fields out of the canonical ones.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import type { BackendEvent } from "../api/types";
import { getEvents } from "../api/backend";

export const MAX_EVENT_ROWS = 400;

export interface EventRow {
  sequence: number;
  eventType: string;
  producer: string;
  occurredAtS: number;
  correlationId: string;
  payload: Record<string, unknown>;
}

export interface Bid {
  robotId: string;
  totalCost: number;
  distanceCost: number;
  batteryCost: number;
  workloadCost: number;
  completionS: number;
  validUntilS: number;
  createdAtS: number;
  sequence: number;
}

export interface NegotiationRecord {
  taskId: string;
  bids: Bid[];
  assignedRobotId: string | null;
  status: string;
  updatedAtS: number;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** The `task_id` a payload refers to, looking inside the nested objects. */
export function taskIdOf(payload: Record<string, unknown>): string | null {
  return (
    asString(payload.task_id) ??
    asString(asRecord(payload.bid).task_id) ??
    asString(asRecord(payload.assignment).task_id) ??
    asString(asRecord(payload.task).task_id) ??
    asString(asRecord(payload.route).task_id) ??
    asString(asRecord(payload.report).task_id)
  );
}

/** The first robot a payload refers to, looking inside the nested objects. */
export function robotIdOf(payload: Record<string, unknown>): string | null {
  return robotIdsOf(payload)[0] ?? null;
}

/** Every robot id a payload mentions, for filtering and cross-links. */
export function robotIdsOf(payload: Record<string, unknown>): string[] {
  const ids = new Set<string>();
  const add = (value: unknown): void => {
    if (typeof value === "string") ids.add(value);
  };
  add(payload.robot_id);
  add(asRecord(payload.bid).robot_id);
  add(asRecord(payload.assignment).robot_id);
  add(asRecord(payload.failure).code === undefined ? undefined : payload.robot_id);
  for (const key of ["robot_ids", "target_robot_ids", "cycle_robot_ids"]) {
    for (const source of [payload, asRecord(payload.conflict), asRecord(payload.report), asRecord(payload.action)]) {
      const value = source[key];
      if (Array.isArray(value)) for (const item of value) add(item);
    }
  }
  return [...ids];
}

/** The real bid carried by a `BID_SUBMITTED` event. */
export function bidOf(payload: Record<string, unknown>): Bid | null {
  const bid = asRecord(payload.bid);
  const robotId = asString(bid.robot_id);
  if (robotId === null) return null;
  return {
    robotId,
    totalCost: asNumber(bid.total_cost),
    distanceCost: asNumber(bid.distance_cost),
    batteryCost: asNumber(bid.battery_cost),
    workloadCost: asNumber(bid.workload_cost),
    completionS: asNumber(bid.estimated_completion_time_s),
    validUntilS: asNumber(bid.valid_until_s),
    createdAtS: asNumber(bid.created_at_s),
    sequence: 0,
  };
}

export interface EventLogState {
  rows: EventRow[];
  cursor: number;
  negotiations: NegotiationRecord[];
  paused: boolean;
  setPaused: (paused: boolean) => void;
  reset: (fromSequence: number) => void;
  poll: () => Promise<void>;
  error: string | null;
  clearError: () => void;
}

export function useEventLog(): EventLogState {
  const [rows, setRows] = useState<EventRow[]>([]);
  const [negotiations, setNegotiations] = useState<NegotiationRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const cursorRef = useRef(0);

  const reset = useCallback((fromSequence: number) => {
    // A scenario load replaces the event stream; keeping the old cursor would
    // skip the new run's events entirely.
    cursorRef.current = Math.max(0, fromSequence);
    setRows([]);
    setNegotiations([]);
  }, []);

  const poll = useCallback(async () => {
    try {
      const events = await getEvents(cursorRef.current);
      const fresh = events.filter((event: BackendEvent) => event.sequence > cursorRef.current);
      if (fresh.length === 0) return;
      cursorRef.current = fresh[fresh.length - 1]!.sequence;
      const next: EventRow[] = fresh.map((event) => ({
        sequence: event.sequence,
        eventType: event.event_type,
        producer: event.producer,
        occurredAtS: event.occurred_at_s,
        correlationId: event.correlation_id,
        payload: event.payload,
      }));
      setRows((current) => [...current, ...next].slice(-MAX_EVENT_ROWS));
      setNegotiations((current) => mergeNegotiations(current, next));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  return useMemo(
    () => ({
      rows,
      cursor: cursorRef.current,
      negotiations,
      paused,
      setPaused,
      reset,
      poll,
      error,
      clearError: () => setError(null),
    }),
    [rows, negotiations, paused, reset, poll, error],
  );
}

/**
 * Fold bid and assignment events into per-task negotiation records.
 *
 * The winner is whatever the backend actually assigned; a task that was
 * reassigned keeps the record and reports the reassignment.
 */
export function mergeNegotiations(
  current: NegotiationRecord[],
  events: EventRow[],
): NegotiationRecord[] {
  const byTask = new Map<string, NegotiationRecord>(
    current.map((record) => [record.taskId, { ...record, bids: [...record.bids] }]),
  );

  for (const event of events) {
    const taskId = taskIdOf(event.payload);
    if (taskId === null) continue;
    const existing = byTask.get(taskId);

    if (event.eventType === "BID_SUBMITTED") {
      const bid = bidOf(event.payload);
      if (bid === null) continue;
      bid.sequence = event.sequence;
      if (existing) {
        if (existing.bids.some((row) => row.sequence === event.sequence)) continue;
        existing.bids.push(bid);
        existing.updatedAtS = event.occurredAtS;
        if (existing.status === "collecting bids") existing.status = "negotiating";
      } else {
        byTask.set(taskId, {
          taskId,
          bids: [bid],
          assignedRobotId: null,
          status: "negotiating",
          updatedAtS: event.occurredAtS,
        });
      }
      continue;
    }

    if (event.eventType === "TASK_ASSIGNED") {
      const assigned = asString(asRecord(event.payload.assignment).robot_id);
      const record = existing ?? {
        taskId,
        bids: [],
        assignedRobotId: null,
        status: "negotiating",
        updatedAtS: event.occurredAtS,
      };
      record.assignedRobotId = assigned;
      record.status = "assigned";
      record.updatedAtS = event.occurredAtS;
      byTask.set(taskId, record);
      continue;
    }

    if (event.eventType === "TASK_REASSIGNED") {
      if (existing) {
        existing.assignedRobotId = asString(event.payload.new_robot_id);
        existing.status = "reassigned";
        existing.updatedAtS = event.occurredAtS;
      }
    }
  }

  return [...byTask.values()]
    .sort((left, right) => right.updatedAtS - left.updatedAtS)
    .slice(0, 12);
}
