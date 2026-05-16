import { useState, useCallback, useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import { DashboardProvider } from "../context/DashboardContext";
import QuickChatButton from "../components/QuickChatButton";
import QuickChatPanel from "../components/QuickChatPanel";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

export function AppShell() {
  useKeyboardShortcuts();
  const [quickChatOpen, setQuickChatOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    if (location.pathname === "/chat") {
      setQuickChatOpen(false);
    }
  }, [location.pathname]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "j") {
        e.preventDefault();
        if (location.pathname !== "/chat") {
          setQuickChatOpen((prev) => !prev);
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [location.pathname]);

  const handleOpenQuickChat = useCallback(() => setQuickChatOpen(true), []);
  const handleCloseQuickChat = useCallback(() => setQuickChatOpen(false), []);

  return (
    <DashboardProvider>
      <div className={styles.shell}>
        <Sidebar />
        <main className={styles.main}>
          <Outlet />
        </main>

        <QuickChatButton onClick={handleOpenQuickChat} visible={!quickChatOpen} />
        <QuickChatPanel open={quickChatOpen} onClose={handleCloseQuickChat} />

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
