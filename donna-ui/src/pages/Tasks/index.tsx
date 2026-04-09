import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import RefreshButton from "../../components/RefreshButton";
import { Button } from "../../primitives/Button";
import { PageHeader } from "../../primitives/PageHeader";
import { fetchTasks, type TaskSummary } from "../../api/tasks";
import TaskDetailDrawer from "./TaskDetailDrawer";
import TaskFilters from "./TaskFilters";
import TaskTable from "./TaskTable";
import { ALL_VALUE } from "./taskStatusStyles";
import styles from "./Tasks.module.css";

const PAGE_SIZE = 50;

/**
 * Tasks list page. Owns filter + pagination + drawer state. The
 * drawer's open/close state is mirrored to the URL via the optional
 * :id param so the existing Logs deep-link (`window.open('/tasks/:id')`)
 * still opens a pre-populated drawer.
 */
export default function TasksPage() {
  const navigate = useNavigate();
  const { id: routeId } = useParams<{ id?: string }>();

  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const [status, setStatus] = useState<string>(ALL_VALUE);
  const [domain, setDomain] = useState<string>(ALL_VALUE);
  const [priority, setPriority] = useState<string>(ALL_VALUE);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchTasks({
        status: status === ALL_VALUE ? undefined : status,
        domain: domain === ALL_VALUE ? undefined : domain,
        priority: priority === ALL_VALUE ? undefined : Number(priority),
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setTasks(Array.isArray(resp?.tasks) ? resp.tasks : []);
      setTotal(typeof resp?.total === "number" ? resp.total : 0);
    } catch {
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, domain, priority, search, page]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleReset = useCallback(() => {
    setStatus(ALL_VALUE);
    setDomain(ALL_VALUE);
    setPriority(ALL_VALUE);
    setSearch("");
    setPage(1);
  }, []);

  const handleTaskClick = useCallback(
    (id: string) => {
      navigate(`/tasks/${id}`);
    },
    [navigate],
  );

  const handleDrawerClose = useCallback(() => {
    navigate("/tasks");
  }, [navigate]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const metaLine = useMemo(() => {
    if (total === 0) return "No tasks in range";
    const start = (page - 1) * PAGE_SIZE + 1;
    const end = Math.min(page * PAGE_SIZE, total);
    return `Showing ${start}–${end} of ${total}`;
  }, [total, page]);

  return (
    <div className={styles.root} data-testid="tasks-root">
      <PageHeader
        eyebrow="Work"
        title="Tasks"
        meta={metaLine}
        actions={<RefreshButton onRefresh={doFetch} />}
      />

      <TaskFilters
        status={status}
        domain={domain}
        priority={priority}
        search={search}
        onStatusChange={(v) => {
          setStatus(v);
          setPage(1);
        }}
        onDomainChange={(v) => {
          setDomain(v);
          setPage(1);
        }}
        onPriorityChange={(v) => {
          setPriority(v);
          setPage(1);
        }}
        onSearchChange={(v) => {
          setSearch(v);
          setPage(1);
        }}
        onReset={handleReset}
      />

      <TaskTable
        tasks={tasks}
        loading={loading}
        selectedId={routeId ?? null}
        onTaskClick={handleTaskClick}
      />

      <nav className={styles.pagination} aria-label="Tasks pagination">
        <span className={styles.pageMeta}>
          Page {page} / {totalPages}
        </span>
        <div className={styles.pageControls}>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1 || loading}
          >
            Prev
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages || loading}
          >
            Next
          </Button>
        </div>
      </nav>

      <TaskDetailDrawer taskId={routeId ?? null} onClose={handleDrawerClose} />
    </div>
  );
}
