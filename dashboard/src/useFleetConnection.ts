/**
 * The dashboard's single connection to the runtime.
 *
 * Everything the console renders comes from here, and this hook is the only
 * place that talks to the API. The rules it enforces are the ones that make the
 * dashboard trustworthy rather than decorative:
 *
 * - the snapshot is the source of truth; events only *explain* changes
 * - events are batched onto an animation frame, so a 500-robot tick storm
 *   cannot turn into 500 React renders
 * - a bad frame is reported and the last good state is kept
 * - a stale projection is labelled stale, and an absent one is an empty state
 *   rather than a spinner that never resolves
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { connectToEvents, getEvents, getFleetRefresh, getHealth, getSnapshot, sendCommand } from "./api";
import { mergeEvents } from "./state";
import type { ControlCommand, DomainEvent, SimulationSnapshot } from "./types";

export type ConnectionState = "connecting" | "live" | "stale" | "offline";

const MAX_EVENTS_IN_LOG = 400;
// The auction is a headline claim, so it gets its own retention budget.
//
// The general log is a 400-event ring, and at 500 robots the runtime publishes
// 200-400 events per second, so a bid is evicted roughly a second after it is
// published. Retaining bids in a second buffer that is only fed bid events
// means the window holds minutes of auction history instead of one second of
// everything, and the Bids tab stops reading as empty while the auction runs.
const MAX_BIDS_IN_LOG = 240;
const REFRESH_DEBOUNCE_MS = 200;
const INITIAL_RETRY_MS = 1000;
const MAX_RETRY_MS = 8000;

export interface Notice {
  kind: "warning" | "error" | "info";
  title: string;
  detail: string;
}

export interface FleetConnection {
  snapshot: SimulationSnapshot | null;
  events: DomainEvent[];
  /**
   * A dedicated, longer-lived window over `BID_SUBMITTED` events only.
   *
   * This is not a second source of truth: the same canonical events the general
   * log carries, retained separately so the auction stays readable at a rate
   * that would otherwise evict it within a second.
   */
  bidEvents: DomainEvent[];
  connection: ConnectionState;
  notice: Notice | null;
  lastSync: Date | null;
  commandPending: boolean;
  commandNotice: Notice | null;
  dismissNotice: () => void;
  dismissCommandNotice: () => void;
  issueCommand: (command: ControlCommand) => Promise<boolean>;
  refresh: () => void;
}

export function useFleetConnection(): FleetConnection {
  const [snapshot, setSnapshot] = useState<SimulationSnapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [bidEvents, setBidEvents] = useState<DomainEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [commandPending, setCommandPending] = useState(false);
  const [commandNotice, setCommandNotice] = useState<Notice | null>(null);

  const hasSnapshotRef = useRef(false);
  const retryRef = useRef(INITIAL_RETRY_MS);
  const eventQueueRef = useRef<DomainEvent[]>([]);
  const frameRef = useRef<number | undefined>(undefined);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const refreshInFlightRef = useRef(false);
  const disposedRef = useRef(false);

  const applySnapshot = useCallback((next: SimulationSnapshot) => {
    hasSnapshotRef.current = true;
    setSnapshot(next);
    setLastSync(new Date());
    setConnection("live");
    setNotice(null);
  }, []);

  /**
   * Fold bid events into the auction buffer.
   *
   * Called from the same two places events enter the console, so the buffer
   * never diverges from the stream: it holds the most recent
   * `MAX_BIDS_IN_LOG` bids regardless of what the general log has since
   * evicted.
   */
  const recordBids = useCallback((incoming: readonly DomainEvent[]) => {
    const bids = incoming.filter((event) => event.event_type === "BID_SUBMITTED");
    if (bids.length === 0) return;
    setBidEvents((current) => mergeEvents(current, bids, MAX_BIDS_IN_LOG));
  }, []);

  useEffect(() => {
    disposedRef.current = false;
    let socket: WebSocket | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRefresh = () => {
      if (disposedRef.current) return;
      if (refreshTimerRef.current !== undefined) clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = setTimeout(() => {
        refreshTimerRef.current = undefined;
        void pullSnapshot();
      }, REFRESH_DEBOUNCE_MS);
    };

    const flushEvents = () => {
      frameRef.current = undefined;
      const batch = eventQueueRef.current.splice(0);
      if (batch.length === 0) return;
      setEvents((current) => mergeEvents(current, batch, MAX_EVENTS_IN_LOG));
      recordBids(batch);
      scheduleRefresh();
    };

    const enqueueEvent = (event: DomainEvent) => {
      eventQueueRef.current.push(event);
      if (frameRef.current === undefined) {
        frameRef.current = window.requestAnimationFrame(flushEvents);
      }
    };

    const pullSnapshot = async () => {
      if (disposedRef.current || refreshInFlightRef.current) return;
      refreshInFlightRef.current = true;
      try {
        if (!hasSnapshotRef.current) {
          applySnapshot(await getSnapshot());
          return;
        }
        // Subsequent refreshes take the narrow endpoints. The world is static,
        // and re-sending several hundred racking cells on every poll is the
        // difference between a console that keeps up and one that does not.
        const refresh = await getFleetRefresh();
        if (disposedRef.current) return;
        setSnapshot((current) => {
          if (current === null) return current;
          return {
            ...current,
            revision: refresh.revision,
            last_event_sequence: Math.max(
              current.last_event_sequence,
              refresh.last_event_sequence,
            ),
            robots: refresh.robots,
            tasks: refresh.tasks,
            routes: refresh.routes,
            conflicts: refresh.conflicts,
            metrics: refresh.metrics,
          };
        });
        setLastSync(new Date());
        setConnection("live");
        setNotice(null);
      } catch (error) {
        setConnection(hasSnapshotRef.current ? "stale" : "offline");
        setNotice({
          kind: hasSnapshotRef.current ? "warning" : "error",
          title: hasSnapshotRef.current
            ? "Showing the last known projection"
            : "No runtime connection",
          detail: error instanceof Error ? error.message : "The runtime did not respond.",
        });
      } finally {
        refreshInFlightRef.current = false;
      }
    };

    const connect = async () => {
      if (disposedRef.current) return;
      setConnection(hasSnapshotRef.current ? "stale" : "connecting");
      try {
        await getHealth();
        const next = await getSnapshot();
        if (disposedRef.current) return;
        const isFirstLoad = !hasSnapshotRef.current;
        applySnapshot(next);
        retryRef.current = INITIAL_RETRY_MS;

        try {
          const missed = await getEvents(next.last_event_sequence);
          if (!disposedRef.current && missed.length > 0) {
            setEvents((current) => mergeEvents(current, missed, MAX_EVENTS_IN_LOG));
            recordBids(missed);
          }
        } catch {
          // Missing history is survivable: the snapshot is still authoritative,
          // so the console keeps working with a shorter event log.
          setNotice({
            kind: "info",
            title: "Event history truncated",
            detail: "Could not load past events; live events are still arriving.",
          });
        }

        socket?.close();
        socket = connectToEvents(next.last_event_sequence, {
          onFrame: (frame) => {
            if (disposedRef.current) return;
            if (frame.kind === "snapshot") {
              applySnapshot(frame.snapshot);
              return;
            }
            if (frame.kind === "event") {
              setConnection("live");
              setLastSync(new Date());
              enqueueEvent(frame.event);
              return;
            }
            setConnection("live");
            setLastSync(new Date());
          },
          onOpen: () => {
            retryRef.current = INITIAL_RETRY_MS;
            if (!disposedRef.current) setConnection("live");
          },
          onClose: () => {
            if (disposedRef.current) return;
            setConnection(hasSnapshotRef.current ? "stale" : "offline");
            reconnectTimer = setTimeout(() => void connect(), retryRef.current);
            retryRef.current = Math.min(retryRef.current * 2, MAX_RETRY_MS);
          },
          onProtocolError: (error) => {
            if (disposedRef.current) return;
            setNotice({
              kind: "warning",
              title: "Discarded an invalid stream frame",
              detail: `${error.message}. The console kept its last good state.`,
            });
          },
        });
        if (isFirstLoad) scheduleRefresh();
      } catch (error) {
        if (disposedRef.current) return;
        setConnection(hasSnapshotRef.current ? "stale" : "offline");
        setNotice({
          kind: hasSnapshotRef.current ? "warning" : "error",
          title: hasSnapshotRef.current
            ? "Showing the last known projection"
            : "No runtime connection",
          detail: error instanceof Error ? error.message : "The runtime did not respond.",
        });
        retryTimer = setTimeout(() => void connect(), retryRef.current);
        retryRef.current = Math.min(retryRef.current * 2, MAX_RETRY_MS);
      }
    };

    void connect();

    return () => {
      disposedRef.current = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (refreshTimerRef.current !== undefined) clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = undefined;
      if (frameRef.current !== undefined) window.cancelAnimationFrame(frameRef.current);
      frameRef.current = undefined;
      eventQueueRef.current = [];
      socket?.close();
    };
  }, [applySnapshot, recordBids]);

  const issueCommand = useCallback(async (command: ControlCommand) => {
    setCommandPending(true);
    setCommandNotice({
      kind: "info",
      title: `Sending ${command.command_type.toLowerCase().replaceAll("_", " ")}`,
      detail: "The runtime applies commands on its own clock.",
    });
    try {
      await sendCommand(command);
      setCommandNotice({
        kind: "info",
        title: `${command.command_type.toLowerCase().replaceAll("_", " ")} accepted`,
        detail: "Watch the event stream for the resulting state change.",
      });
      return true;
    } catch (error) {
      setCommandNotice({
        kind: "error",
        title: "Command rejected",
        detail: error instanceof Error ? error.message : "The command could not be sent.",
      });
      return false;
    } finally {
      setCommandPending(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setConnection(hasSnapshotRef.current ? "stale" : "connecting");
    void (async () => {
      try {
        // An explicit refresh always takes the full projection, so a world
        // rebuilt by a reset is picked up rather than left stale.
        applySnapshot(await getSnapshot());
      } catch {
        setConnection(hasSnapshotRef.current ? "stale" : "offline");
      }
    })();
  }, [applySnapshot]);

  const value = useMemo<FleetConnection>(
    () => ({
      snapshot,
      events,
      bidEvents,
      connection,
      notice,
      lastSync,
      commandPending,
      commandNotice,
      dismissNotice: () => setNotice(null),
      dismissCommandNotice: () => setCommandNotice(null),
      issueCommand,
      refresh,
    }),
    [snapshot, events, bidEvents, connection, notice, lastSync, commandPending, commandNotice, issueCommand, refresh],
  );

  return value;
}

