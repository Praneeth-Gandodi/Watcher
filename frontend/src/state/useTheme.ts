/**
 * Theme selection.
 *
 * The active theme is written to `localStorage` so a reload keeps it, and
 * applied before the first paint by `main.tsx` so the console never flashes the
 * wrong colours on the way in.
 */

import { useCallback, useEffect, useState } from "react";
import { applyTheme, DEFAULT_THEME_ID, THEMES, themeById } from "../styles/palette";

const STORAGE_KEY = "watcher.theme";

function readStoredTheme(): string {
  if (typeof window === "undefined") return DEFAULT_THEME_ID;
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_THEME_ID;
  } catch {
    return DEFAULT_THEME_ID;
  }
}

/**
 * Apply the stored theme before React mounts.
 *
 * Called from `main.tsx` so the very first painted frame already uses the right
 * colours, rather than flashing the default and then switching.
 */
export function applyStoredTheme(): void {
  applyTheme(readStoredTheme());
}

export interface ThemeState {
  id: string;
  themes: typeof THEMES;
  select: (id: string) => void;
}

export function useTheme(): ThemeState {
  const [id, setId] = useState<string>(readStoredTheme);

  useEffect(() => {
    applyTheme(id);
  }, [id]);

  const select = useCallback((next: string) => {
    const theme = themeById(next);
    setId(theme.id);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme.id);
    } catch {
      // A browser with storage disabled still themes correctly for this session.
    }
  }, []);

  return { id, themes: THEMES, select };
}
