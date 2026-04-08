import { useCallback, useEffect, useState } from "react";
import type { Theme } from "../theme";

const STORAGE_KEY = "donna-theme";
const DEFAULT_THEME: Theme = "gold";

function readStoredTheme(): Theme {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "coral" ? "coral" : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function applyTheme(theme: Theme): void {
  // "gold" is the default (no attribute needed) — only set when coral
  if (theme === "coral") {
    document.documentElement.setAttribute("data-theme", "coral");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

/**
 * Reads and writes the active accent theme.
 * Persisted to localStorage. Mirrored to [data-theme] on <html>.
 * All CSS uses var(--color-accent) so the flip is instant.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof window === "undefined") return DEFAULT_THEME;
    const stored = readStoredTheme();
    applyTheme(stored);
    return stored;
  });

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore — private browsing, quota exceeded, etc.
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "gold" ? "coral" : "gold");
  }, [theme, setTheme]);

  // Global shortcut: ⌘. (Mac) or Ctrl+. (everywhere) flips the theme.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ".") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggle]);

  return { theme, setTheme, toggle };
}
