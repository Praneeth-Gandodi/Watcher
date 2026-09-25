import { parseEvent, parseSnapshot } from "./validation";
import type { StreamFrame } from "./types";

/**
 * WebSocket frame protocol.
 *
 * The stream is a transport for the canonical event vocabulary, not a second
 * domain contract. Each frame is a tagged envelope around data that is already
 * defined by `backend/contracts`:
 *
 * - `snapshot` the full `SimulationSnapshot`, sent on connect and as a
 *   resynchronisation point
 * - `event`    one canonical event envelope
 * - `cursor`   the latest sequence number, so a client can notice it is behind
 *
 * Every frame's payload is validated with the same parsers the REST path uses,
 * so a malformed frame is rejected rather than rendered.
 */

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

/**
 * Parse one stream frame, throwing on anything unrecognised.
 *
 * A rejected frame is a protocol error, not an empty frame: the caller reports
 * it and keeps the last good state rather than silently dropping data.
 */
export function parseStreamFrame(raw: unknown): StreamFrame {
  const frame = asRecord(raw, "stream frame");
  const kind = frame.kind;
  const data = frame.data;

  if (kind === "snapshot") {
    return { kind, snapshot: parseSnapshot(data) };
  }
  if (kind === "event") {
    return { kind, event: parseEvent(data) };
  }
  if (kind === "cursor") {
    const payload = asRecord(data, "cursor payload");
    const sequence = payload.last_event_sequence;
    if (typeof sequence !== "number" || !Number.isFinite(sequence)) {
      throw new Error("cursor last_event_sequence must be a finite number");
    }
    return { kind, lastEventSequence: sequence };
  }
  throw new Error(`unknown stream frame kind: ${String(kind)}`);
}
