import { Outlet } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

/**
 * Top-level app chrome. Renders the fixed-width Sidebar and a scrolling
 * <main> hosting the route outlet. Registers the global keyboard shortcuts
 * hook (Escape/?, g+key navigation). Mounts the keyboard shortcuts modal
 * and the Sonner toaster once for the whole app.
 *
 * Replaces the previous AntD `components/Layout.tsx`.
 */
export function AppShell() {
  useKeyboardShortcuts();

  return (
    <div className={styles.shell}>
      <Sidebar />
      <main className={styles.main}>
        <Outlet />
      </main>

      <KeyboardShortcutsModal />

      <Toaster
        position="top-right"
        theme="dark"
        toastOptions={{
          style: {
            background: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border)",
            fontFamily: "var(--font-body)",
            fontSize: "var(--text-body)",
            borderRadius: "var(--radius-control)",
          },
        }}
      />
    </div>
  );
}
