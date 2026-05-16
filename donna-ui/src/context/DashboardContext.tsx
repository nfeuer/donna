import { createContext, useContext, useState, useCallback, useMemo, useEffect } from "react";
import { useLocation } from "react-router-dom";
import type { ReactNode } from "react";

export interface SelectedItem {
  type: "task" | "agent" | "skill" | "log_entry" | "vault_file" | "automation";
  id: string;
  label: string;
}

interface DashboardContextValue {
  currentPage: string;
  selectedItem: SelectedItem | null;
  setSelectedItem: (item: SelectedItem | null) => void;
}

const DashboardCtx = createContext<DashboardContextValue>({
  currentPage: "",
  selectedItem: null,
  setSelectedItem: () => {},
});

function pageFromPathname(pathname: string): string {
  const segment = pathname.split("/")[1] || "dashboard";
  return segment || "dashboard";
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const currentPage = pageFromPathname(location.pathname);
  const [selectedItem, setSelectedItemRaw] = useState<SelectedItem | null>(null);

  useEffect(() => {
    setSelectedItemRaw(null);
  }, [currentPage]);

  const setSelectedItem = useCallback((item: SelectedItem | null) => {
    setSelectedItemRaw(item);
  }, []);

  const value = useMemo(
    () => ({ currentPage, selectedItem, setSelectedItem }),
    [currentPage, selectedItem, setSelectedItem],
  );

  return <DashboardCtx.Provider value={value}>{children}</DashboardCtx.Provider>;
}

export function useDashboardContext(): DashboardContextValue {
  return useContext(DashboardCtx);
}
