import { parseEvent, parseEvents, parseHealth, parseSnapshot } from "./validation";
import type { ControlCommand, DomainEvent, SimulationSnapshot } from "./types";

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
  return requestJson(`/events?after_sequence=${afterSequence}`).then(parseEvents);
}

export function sendCommand(command: ControlCommand): Promise<unknown> {
  return requestJson("/commands", { method: "POST", body: JSON.stringify(command) });
}

export function connectToEvents(
  afterSequence: number,
  onEvent: (event: DomainEvent) => void,
  onOpen: () => void,
  onClose: () => void,
  onError: () => void,
): WebSocket {
  const configuredBase = API_BASE.startsWith("http")
    ? API_BASE
    : `${window.location.origin}${API_BASE}`;
  const url = new URL(configuredBase);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/stream`;
  url.searchParams.set("after_sequence", String(afterSequence));

  const socket = new WebSocket(url.toString());
  socket.addEventListener("open", onOpen);
  socket.addEventListener("message", (message: MessageEvent<unknown>) => {
    try {
      const rawEvent = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
      const event = parseEvent(rawEvent);
      if (event.sequence > afterSequence) onEvent(event);
    } catch {
      onError();
    }
  });
  socket.addEventListener("close", onClose);
  socket.addEventListener("error", onError);
  return socket;
}
