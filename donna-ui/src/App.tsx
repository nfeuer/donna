import { Routes, Route } from "react-router-dom";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { AppShell } from "./layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";
import DevPrimitivesPage from "./pages/DevPrimitives";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  // Activates theme + persists to localStorage + registers ⌘. shortcut
  useTheme();

  return (
    <RadixTooltip.Provider delayDuration={400} skipDelayDuration={100}>
      <Routes>
        {/* Dev-only primitives gallery — outside AppShell so it renders standalone */}
        {import.meta.env.DEV && (
          <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
        )}
        <Route element={<AppShell />}>
          <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
          <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
          <Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
          <Route path="/configs/:file" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
          <Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
          <Route path="/prompts/:file" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
          <Route path="/agents/:name" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
          <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/tasks/:id" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
          <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
        </Route>
      </Routes>
    </RadixTooltip.Provider>
  );
}
