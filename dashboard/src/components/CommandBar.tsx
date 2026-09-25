import { Activity, Moon, RefreshCcw, Search, Sun } from "lucide-react";

import { formatClock, formatCount, formatWallClock } from "../format";
import { StatusPill } from "./Primitives";
import type { ConnectionState } from "../useFleetConnection";

const CONNECTION_COPY: Record<ConnectionState, { label: string; tone: "ok" | "warn" | "crit" | "info" }> = {
  live: { label: "Live", tone: "ok" },
  connecting: { label: "Connecting", tone: "info" },
  stale: { label: "Stale", tone: "warn" },
  offline: { label: "Offline", tone: "crit" },
};

export interface CommandBarProps {
  connection: ConnectionState;
  lastSync: Date | null;
  simulationTimeS: number | null;
  revision: number | null;
  lastEventSequence: number | null;
  fleetSize: number;
  controllerAvailable: boolean | null;
  controllerOutages: number | null;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onRefresh: () => void;
  onOpenPalette: () => void;
}

export function CommandBar({
  connection,
  lastSync,
  simulationTimeS,
  revision,
  lastEventSequence,
  fleetSize,
  controllerAvailable,
  controllerOutages,
  theme,
  onToggleTheme,
  onRefresh,
  onOpenPalette,
}: CommandBarProps) {
  const copy = CONNECTION_COPY[connection];
  const degraded =
    controllerAvailable === false ||
    (controllerOutages !== null && controllerOutages > 0 && controllerAvailable !== true);

  return (
    <header className="commandbar">
      <div className="commandbar__identity">
        <span className="mark" aria-hidden="true">
          <Activity size={18} strokeWidth={2} />
        </span>
        <div>
          <p className="kicker">Decentralized fleet coordination</p>
          <p className="wordmark">WATCHER</p>
        </div>
      </div>

      <dl className="commandbar__readouts">
        <Readout label="Fleet" value={formatCount(fleetSize)} />
        <Readout label="Sim clock" value={formatClock(simulationTimeS)} />
        <Readout label="Revision" value={revision === null ? "—" : String(revision)} />
        <Readout label="Cursor" value={lastEventSequence === null ? "—" : String(lastEventSequence)} />
        <Readout label="Synced" value={formatWallClock(lastSync)} />
      </dl>

      <div className="commandbar__actions">
        <StatusPill tone={degraded ? "warn" : copy.tone} title="Coordination service availability">
          {controllerAvailable === null
            ? copy.label
            : controllerAvailable
              ? "Coordinator up"
              : "Coordinator down"}
        </StatusPill>

        <button type="button" className="button button--ghost" onClick={onOpenPalette}>
          <Search size={15} aria-hidden="true" />
          <span>Find</span>
          <kbd className="kbd">Ctrl</kbd>
          <kbd className="kbd">K</kbd>
        </button>

        <button
          type="button"
          className="icon-button"
          onClick={onRefresh}
          aria-label="Refresh the projection now"
          title="Refresh now"
        >
          <RefreshCcw size={16} />
        </button>

        <button
          type="button"
          className="icon-button"
          onClick={onToggleTheme}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="readout">
      <dt className="readout__label">{label}</dt>
      <dd className="readout__value">{value}</dd>
    </div>
  );
}
