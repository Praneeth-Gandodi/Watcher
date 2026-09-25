/**
 * Formatting helpers for telemetry.
 *
 * Every value the dashboard shows is either measured or explicitly absent. A
 * missing number renders as an em dash and a labelled "no data" state, never as
 * a zero and never as an invented figure.
 */

const EM_DASH = "—";

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const decimalFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return numberFormat.format(value);
}

export function formatDecimal(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return decimalFormat.format(value);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return `${Math.round(value)}%`;
}

export function formatMilliseconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  if (value >= 1000) return `${decimalFormat.format(value / 1000)} s`;
  return `${decimalFormat.format(value)} ms`;
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return `${decimalFormat.format(value)} s`;
}

export function formatClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return EM_DASH;
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function formatCoordinate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return value.toFixed(1);
}

export function formatWallClock(date: Date | null): string {
  if (!date) return EM_DASH;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** `astar-grid-yield-aware` becomes `Astar grid · yield aware`. */
export function humanizeStrategy(value: string): string {
  return titleCase(value.replaceAll("-", " "));
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? singular : plural;
}
