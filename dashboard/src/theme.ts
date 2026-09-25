/**
 * Theme selection for the operations console.
 *
 * The dashboard is an instrument that gets used in a dim control room and in a
 * bright meeting room, so both themes are first-class. The choice is persisted
 * and, critically, read from the OS preference on a first visit rather than
 * defaulting to one side.
 */

export type ThemeName = "dark" | "light";

export const THEME_STORAGE_KEY = "watcher.theme";

export const THEMES: readonly ThemeName[] = ["dark", "light"] as const;

function isThemeName(value: string | null): value is ThemeName {
  return value === "dark" || value === "light";
}

function preferredTheme(): ThemeName {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Resolve the initial theme: stored choice first, then the OS preference. */
export function readInitialTheme(): ThemeName {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (isThemeName(stored)) return stored;
  } catch {
    // A blocked storage API must not stop the dashboard from rendering.
  }
  return preferredTheme();
}

export function applyTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Persisting is a convenience, not a requirement.
  }
}

export function nextTheme(current: ThemeName): ThemeName {
  return current === "dark" ? "light" : "dark";
}

/** Follow the OS preference until the operator picks a theme explicitly. */
export function watchSystemTheme(onChange: (theme: ThemeName) => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
  const query = window.matchMedia("(prefers-color-scheme: light)");
  const listener = (event: MediaQueryListEvent) => onChange(event.matches ? "light" : "dark");
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
