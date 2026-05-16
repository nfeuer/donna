import { Outlet } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import { DashboardProvider } from "../context/DashboardContext";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

export function AppShell() {
  useKeyboardShortcuts();

  return (
    <DashboardProvider>
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
    </DashboardProvider>
  );
}
