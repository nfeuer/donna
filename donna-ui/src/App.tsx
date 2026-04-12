import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { AppShell } from "./layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";
import LLMGatewayPage from "./pages/LLMGateway";
import DevPrimitivesPage from "./pages/DevPrimitives";
import { useTheme } from "./hooks/useTheme";
import { Skeleton } from "./primitives";

// Lazy-load editor pages — Monaco Editor (~220 kB gzip) is only needed here
const ConfigsPage = lazy(() => import("./pages/Configs"));
const PromptsPage = lazy(() => import("./pages/Prompts"));

const editorFallback = (
  <div style={{ padding: "40px 48px", display: "flex", flexDirection: "column", gap: 14 }}>
    <Skeleton width={200} height={28} />
    <Skeleton width="100%" height={400} />
  </div>
);

export default function App() {
  // Activates theme + persists to localStorage + registers ⌘. shortcut
  useTheme();

  return (
    <RadixTooltip.Provider delayDuration={400} skipDelayDuration={100}>
      <Routes>
        {/* Primitives gallery — available as internal reference */}
        <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
        <Route element={<AppShell />}>
          <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
          <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
          <Route path="/configs" element={<ErrorBoundary><Suspense fallback={editorFallback}><ConfigsPage /></Suspense></ErrorBoundary>} />
          <Route path="/configs/:file" element={<ErrorBoundary><Suspense fallback={editorFallback}><ConfigsPage /></Suspense></ErrorBoundary>} />
          <Route path="/prompts" element={<ErrorBoundary><Suspense fallback={editorFallback}><PromptsPage /></Suspense></ErrorBoundary>} />
          <Route path="/prompts/:file" element={<ErrorBoundary><Suspense fallback={editorFallback}><PromptsPage /></Suspense></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
          <Route path="/agents/:name" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
          <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/tasks/:id" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
          <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
          <Route path="/llm-gateway" element={<ErrorBoundary><LLMGatewayPage /></ErrorBoundary>} />
        </Route>
      </Routes>
    </RadixTooltip.Provider>
  );
}
