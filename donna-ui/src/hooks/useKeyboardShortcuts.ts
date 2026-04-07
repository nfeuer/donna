import { useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";

const NAV_MAP: Record<string, string> = {
  d: "/",
  l: "/logs",
  c: "/configs",
  p: "/prompts",
  a: "/agents",
  t: "/tasks",
  s: "/shadow",
  r: "/preferences",
};

export interface ShortcutDef {
  keys: string;
  description: string;
  category: "Navigation" | "Actions" | "Help";
}

export const SHORTCUT_DEFINITIONS: ShortcutDef[] = [
  { keys: "g d", description: "Go to Dashboard", category: "Navigation" },
  { keys: "g l", description: "Go to Logs", category: "Navigation" },
  { keys: "g c", description: "Go to Configs", category: "Navigation" },
  { keys: "g p", description: "Go to Prompts", category: "Navigation" },
  { keys: "g a", description: "Go to Agents", category: "Navigation" },
  { keys: "g t", description: "Go to Tasks", category: "Navigation" },
  { keys: "g s", description: "Go to Shadow Scoring", category: "Navigation" },
  { keys: "g r", description: "Go to Preferences", category: "Navigation" },
  { keys: "r", description: "Refresh current page", category: "Actions" },
  { keys: "Esc", description: "Close drawer / modal", category: "Actions" },
  { keys: "?", description: "Show keyboard shortcuts", category: "Help" },
];

export default function useKeyboardShortcuts() {
  const navigate = useNavigate();
  const pendingG = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handler = useCallback(
    (e: KeyboardEvent) => {
      // Skip if user is typing in an input, textarea, or contenteditable
      const tag = (e.target as HTMLElement).tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        (e.target as HTMLElement).isContentEditable
      ) {
        return;
      }

      // Escape → close drawers/modals
      if (e.key === "Escape") {
        window.dispatchEvent(new Event("close-drawer"));
        return;
      }

      // Two-key sequence: g then <key>
      if (pendingG.current) {
        pendingG.current = false;
        if (timer.current) clearTimeout(timer.current);

        const path = NAV_MAP[e.key];
        if (path) {
          e.preventDefault();
          navigate(path);
        }
        return;
      }

      if (e.key === "g") {
        pendingG.current = true;
        // Reset after 500ms if no second key
        timer.current = setTimeout(() => {
          pendingG.current = false;
        }, 500);
        return;
      }

      // ? → show shortcuts help
      if (e.key === "?") {
        window.dispatchEvent(new Event("show-shortcuts-help"));
        return;
      }

      // r → refresh (only when not part of g-r sequence)
      if (e.key === "r" && !pendingG.current) {
        window.dispatchEvent(new Event("trigger-refresh"));
        return;
      }
    },
    [navigate],
  );

  useEffect(() => {
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [handler]);
}
