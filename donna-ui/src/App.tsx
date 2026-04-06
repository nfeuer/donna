import { Routes, Route } from "react-router-dom";
import AppLayout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/configs" element={<ConfigsPage />} />
        <Route path="/prompts" element={<PromptsPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/shadow" element={<ShadowPage />} />
        <Route path="/preferences" element={<PreferencesPage />} />
      </Route>
    </Routes>
  );
}
