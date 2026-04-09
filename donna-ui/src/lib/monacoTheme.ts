// donna-ui/src/lib/monacoTheme.ts
import type { Monaco } from "@monaco-editor/react";

export const DONNA_MONACO_THEME = "donna-dark";

/**
 * Reads a CSS custom property from :root and returns a 6-digit hex.
 * Monaco requires literal hex — it can't consume `var(--x)`.
 * Falls back to a safe default if resolution fails (SSR/tests).
 */
function resolveTokenHex(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return fallback;
  if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw;
  return fallback;
}

let registered = false;

/**
 * Registers "donna-dark" once per session. Idempotent.
 * Call from every Monaco `<Editor>` via the `beforeMount` prop.
 */
export function setupDonnaMonacoTheme(monaco: Monaco) {
  if (registered) return;
  const bg = resolveTokenHex("--color-inset", "#16140f");
  const surface = resolveTokenHex("--color-surface", "#1f1c18");
  const text = resolveTokenHex("--color-text", "#e8e3d8");
  const muted = resolveTokenHex("--color-text-muted", "#8a8378");
  const border = resolveTokenHex("--color-border", "#2a2724");
  const accent = resolveTokenHex("--color-accent", "#d4a943");

  monaco.editor.defineTheme(DONNA_MONACO_THEME, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "", foreground: text.slice(1) },
      { token: "comment", foreground: muted.slice(1), fontStyle: "italic" },
      { token: "string", foreground: accent.slice(1) },
      { token: "number", foreground: accent.slice(1) },
      { token: "keyword", foreground: text.slice(1), fontStyle: "bold" },
    ],
    colors: {
      "editor.background": bg,
      "editor.foreground": text,
      "editorLineNumber.foreground": muted,
      "editorLineNumber.activeForeground": text,
      "editorCursor.foreground": accent,
      "editor.selectionBackground": accent + "40",
      "editor.inactiveSelectionBackground": accent + "1f",
      "editorIndentGuide.background1": border,
      "editorIndentGuide.activeBackground1": accent + "66",
      "editorWidget.background": surface,
      "editorWidget.border": border,
    },
  });
  registered = true;
}
