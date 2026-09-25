import type { ControlCommand, DomainEvent, SimulationSnapshot } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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

  return (await response.json()) as T;
}

export function getHealth(): Promise<{ status: string; service: string; version: string }> {
  return request("/health");
}

export function getSnapshot(): Promise<SimulationSnapshot> {
  return request("/snapshot");
}

export function getEvents(afterSequence = 0): Promise<DomainEvent[]> {
  return request(`/events?after_sequence=${afterSequence}`);
}

export function sendCommand(command: ControlCommand): Promise<unknown> {
  return request("/commands", { method: "POST", body: JSON.stringify(command) });
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
  socket.addEventListener("message", (message) => {
    try {
      const event = JSON.parse(String(message.data)) as DomainEvent;
      if (event.event_id && event.sequence > afterSequence) onEvent(event);
    } catch {
      onError();
    }
  });
  socket.addEventListener("close", onClose);
  socket.addEventListener("error", onError);
  return socket;
}
