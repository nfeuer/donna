import { useState, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "antd";
import RefreshButton from "../../components/RefreshButton";
import TaskFilters from "./TaskFilters";
import TaskTable from "./TaskTable";
import { fetchTasks, type TaskSummary } from "../../api/tasks";

export default function TasksPage() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // Filters
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [priority, setPriority] = useState(0);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchTasks({
        status: status || undefined,
        domain: domain || undefined,
        priority: priority || undefined,
        search: search || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setTasks(resp.tasks);
      setTotal(resp.total);
    } catch {
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, domain, priority, search, page, pageSize]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handlePageChange = (newPage: number, newSize: number) => {
    setPage(newPage);
    setPageSize(newSize);
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <TaskFilters
          status={status}
          domain={domain}
          priority={priority}
          onStatusChange={(v) => { setStatus(v); setPage(1); }}
          onDomainChange={(v) => { setDomain(v); setPage(1); }}
          onPriorityChange={(v) => { setPriority(v); setPage(1); }}
          onSearch={(v) => { setSearch(v); setPage(1); }}
        />
        <RefreshButton onRefresh={doFetch} />
      </div>
      <Card size="small">
        <TaskTable
          tasks={tasks}
          total={total}
          loading={loading}
          page={page}
          pageSize={pageSize}
          onPageChange={handlePageChange}
          onTaskClick={(id) => navigate(`/tasks/${id}`)}
        />
      </Card>
    </div>
  );
}
