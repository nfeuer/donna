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
