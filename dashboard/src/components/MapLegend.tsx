/**
 * The map key.
 *
 * The map encodes a lot at once: five robot shapes, three marker overlays,
 * four route colours and six kinds of static floor cell. Without a key a
 * first-time viewer cannot decode any of it, which makes the strongest evidence
 * on the page unreadable.
 *
 * Two rules shape this component:
 *
 * 1. The swatches are the same geometry and the same tokens the canvas uses,
 *    so the key cannot drift from the thing it explains. Shapes are drawn as
 *    inline SVG rather than as characters, because a glyph is not a guarantee.
 * 2. Every entry is named in words as well as coloured, so the key still works
 *    in greyscale and for a viewer who cannot separate the status hues — the
 *    same constraint the map itself is built under.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { SHAPE_BY_STATUS } from "./FleetMap";
import type { Robot } from "../types";

type Shape = (typeof SHAPE_BY_STATUS)[Robot["status"]];

interface MarkerProps {
  shape: Shape;
  /** Draws the marker as an outline rather than a filled body. */
  outline?: boolean;
  className?: string;
}

function Marker({ shape, outline = false, className }: MarkerProps) {
  const common = {
    width: 12,
    height: 12,
    viewBox: "-7 -7 14 14",
    "aria-hidden": true as const,
    focusable: "false" as const,
    className,
  };
  const fill = outline ? "none" : "currentColor";

  switch (shape) {
    case "square":
      return (
        <svg {...common}>
          <rect x="-4.5" y="-4.5" width="9" height="9" fill={fill} stroke="currentColor" strokeWidth="1" />
        </svg>
      );
    case "triangle":
      return (
        <svg {...common}>
          <path d="M0 -5 L5 4 L-5 4 Z" fill={fill} stroke="currentColor" strokeWidth="1" strokeLinejoin="round" />
        </svg>
      );
    case "diamond":
      return (
        <svg {...common}>
          <path d="M0 -5 L5 0 L0 5 L-5 0 Z" fill={fill} stroke="currentColor" strokeWidth="1" strokeLinejoin="round" />
        </svg>
      );
    case "cross":
      return (
        <svg {...common}>
          <path d="M-4 -4 L4 4 M4 -4 L-4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle r="4.5" fill={fill} stroke="currentColor" strokeWidth="1" />
        </svg>
      );
  }
}

const ROBOT_KEY: Array<{ status: Robot["status"]; label: string; detail: string }> = [
  { status: "active", label: "Active", detail: "carrying out a task" },
  { status: "idle", label: "Idle", detail: "free and bidding" },
  { status: "blocked", label: "Blocked", detail: "yielding right of way" },
  { status: "charging", label: "Charging", detail: "on a charging pad" },
  { status: "degraded", label: "Degraded", detail: "also marks offline robots" },
  { status: "failed", label: "Failed", detail: "out of service" },
];

const OVERLAY_KEY: Array<{ tone: string; label: string; detail: string; children: React.ReactNode }> = [
  {
    tone: "var(--crit)",
    label: "Conflict",
    detail: "in an open conflict, drawn as a ring",
    children: (
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden focusable="false">
        <circle r="5.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      </svg>
    ),
  },
  {
    tone: "var(--ink)",
    label: "Selected",
    detail: "the robot open in the inspector",
    children: (
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden focusable="false">
        <circle r="5.5" fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
    ),
  },
  {
    tone: "var(--ok)",
    label: "Low battery",
    detail: "under reserve, drawn as a ring with a centre dot",
    children: (
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden focusable="false">
        <circle r="5.5" fill="currentColor" />
        <circle r="1.8" fill="var(--map-floor)" />
      </svg>
    ),
  },
  {
    tone: "var(--crit)",
    label: "Deadlock cycle",
    detail: "a dashed loop drawn around the robots in the cycle",
    children: (
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden focusable="false">
        <circle
          r="5.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeDasharray="3 2.5"
        />
      </svg>
    ),
  },
  {
    tone: "var(--warn)",
    label: "Right-of-way",
    detail: "a robot yielding rather than an imminent collision",
    children: (
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden focusable="false">
        <path
          d="M0 -5.5 L5.5 0 L0 5.5 L-5.5 0 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];

const ROUTE_KEY: Array<{ color: string; label: string; dash?: boolean }> = [
  { color: "var(--accent)", label: "Planned" },
  { color: "var(--warn)", label: "Blocked" },
  { color: "var(--ok)", label: "Completed" },
  { color: "var(--crit)", label: "Invalid", dash: true },
];

const FLOOR_KEY: Array<{ color: string; label: string; detail: string }> = [
  { color: "var(--map-cell-obstacle)", label: "Racking", detail: "not enterable" },
  { color: "var(--map-cell-deadzone)", label: "Dead zone", detail: "treated as blocked" },
  { color: "var(--map-cell-charging)", label: "Charger", detail: "charging pad" },
  { color: "var(--map-cell-workstation)", label: "Workstation", detail: "work point" },
  { color: "var(--map-cell-resource)", label: "Resource", detail: "resource point" },
];

export interface MapLegendProps {
  /** Hidden from assistive tech as a duplicate of the canvas aria-label. */
  compact?: boolean;
}

export function MapLegend({ compact = false }: MapLegendProps) {
  const [open, setOpen] = useState(!compact);

  return (
    <div className={`legend${open ? " legend--open" : ""}`}>
      <button
        type="button"
        className="legend__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
        <span>Legend</span>
      </button>

      {open ? (
        <div className="legend__body">
          <section className="legend__group">
            <p className="kicker">Robot status, by shape</p>
            <ul className="legend__list legend__list--split">
              {ROBOT_KEY.map((entry) => (
                <li
                  key={entry.status}
                  className="legend__row"
                  title={entry.detail}
                >
                  <span className={`legend__swatch legend__swatch--${entry.status}`}>
                    <Marker shape={SHAPE_BY_STATUS[entry.status]} />
                  </span>
                  <span>{entry.label}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="legend__group">
            <p className="kicker">Overlays and markers</p>
            <ul className="legend__list legend__list--split">
              {OVERLAY_KEY.map((entry) => (
                <li key={entry.label} className="legend__row" title={entry.detail}>
                  <span className="legend__swatch" style={{ color: entry.tone }}>
                    {entry.children}
                  </span>
                  <span>{entry.label}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="legend__group">
            <p className="kicker">Routes</p>
            <ul className="legend__list legend__list--split">
              {ROUTE_KEY.map((entry) => (
                <li key={entry.label} className="legend__row">
                  <span
                    className="legend__line"
                    style={
                      {
                        background: entry.dash
                          ? `repeating-linear-gradient(90deg, ${entry.color} 0 4px, transparent 4px 7px)`
                          : entry.color,
                      } as React.CSSProperties
                    }
                  />
                  <span>{entry.label}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="legend__group">
            <p className="kicker">Floor</p>
            <ul className="legend__list legend__list--split">
              {FLOOR_KEY.map((entry) => (
                <li key={entry.label} className="legend__row" title={entry.detail}>
                  <span className="legend__block" style={{ background: entry.color }} />
                  <span>{entry.label}</span>
                </li>
              ))}
            </ul>
          </section>

          <p className="legend__foot">
            Shape and label carry status as well as colour, so the map reads in greyscale.
            Scroll to zoom, drag to pan, 0 resets.
          </p>
        </div>
      ) : null}
    </div>
  );
}
