import { Routes, Route } from "react-router-dom";
import AppLayout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import TaskDetail from "./pages/Tasks/TaskDetail";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
        <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
        <Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
        <Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
        <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
        <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
        <Route path="/tasks/:id" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
        <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
        <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
      </Route>
    </Routes>
  );
}
