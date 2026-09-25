import { parseStreamFrame } from "./frames";
import {
  parseCommandAccepted,
  parseEventsResponse,
  parseHealth,
  parseMetrics,
  parseSnapshot,
} from "./validation";
import type {
  ControlCommand,
  DomainEvent,
  SimulationSnapshot,
  StreamFrame,
  SystemMetrics,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText || "request failed"}`);
  }

  return response.json();
}

export function getHealth(): Promise<{ status: string; service: string; version: string }> {
  return requestJson("/health").then(parseHealth);
}

export function getSnapshot(): Promise<SimulationSnapshot> {
  return requestJson("/snapshot").then(parseSnapshot);
}

export function getEvents(afterSequence = 0): Promise<DomainEvent[]> {
  return requestJson(`/events?after_sequence=${afterSequence}`).then(
    (raw) => parseEventsResponse(raw).events,
  );
}

export function getMetrics(): Promise<SystemMetrics> {
  return requestJson("/metrics").then(parseMetrics);
}

export function sendCommand(command: ControlCommand): Promise<unknown> {
  return requestJson("/commands", { method: "POST", body: JSON.stringify(command) }).then(
    parseCommandAccepted,
  );
}

/** Build a websocket URL for the canonical stream from the API base. */
export function buildStreamUrl(afterSequence: number): string {
  const configuredBase = API_BASE.startsWith("http")
    ? API_BASE
    : `${window.location.origin}${API_BASE}`;
  const url = new URL(configuredBase);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/stream`;
  url.searchParams.set("after_sequence", String(afterSequence));
  return url.toString();
}

export interface StreamHandlers {
  onFrame: (frame: StreamFrame) => void;
  onOpen: () => void;
  onClose: () => void;
  onProtocolError: (error: Error) => void;
}

/**
 * Open the canonical event stream.
 *
 * Frames are validated before they are handed on. A frame that fails
 * validation reports a protocol error and the socket is left open, because one
 * bad frame says nothing about the next one and tearing the connection down
 * would lose the stream over a single bad payload.
 */
export function connectToEvents(afterSequence: number, handlers: StreamHandlers): WebSocket {
  const socket = new WebSocket(buildStreamUrl(afterSequence));

  socket.addEventListener("open", handlers.onOpen);
  socket.addEventListener("message", (message: MessageEvent<unknown>) => {
    try {
      const raw = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
      handlers.onFrame(parseStreamFrame(raw));
    } catch (error) {
      handlers.onProtocolError(
        error instanceof Error ? error : new Error("unparseable stream frame"),
      );
    }
  });
  socket.addEventListener("close", handlers.onClose);
  socket.addEventListener("error", () => handlers.onClose());
  return socket;
}
