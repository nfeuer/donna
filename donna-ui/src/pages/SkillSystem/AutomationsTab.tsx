import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Loader2, Play } from "lucide-react";
import { DataTable } from "../../primitives/DataTable";
import { Button } from "../../primitives/Button";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import {
  fetchAutomationRuns,
  fetchAutomations,
  runAutomationNow,
  type Automation,
} from "../../api/skillSystem";
import { cn } from "../../lib/cn";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  onNew: () => void;
  refreshToken: number;
}

const STATUS_OPTIONS = ["active", "paused", "deleted", "all"];

function statusVariant(status: string): PillVariant {
  if (status === "active") return "success";
  if (status === "paused") return "warning";
  if (status === "deleted") return "muted";
  return "accent";
}

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 120_000;

export default function AutomationsTab({
  selectedId,
  onRowClick,
  onNew,
  refreshToken,
}: Props) {
  const [status, setStatus] = useState("active");
  const [rows, setRows] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(false);
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
  const pollTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchAutomations({ status });
      setRows(resp.automations);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  const stopPolling = useCallback((id: string) => {
    const timer = pollTimers.current.get(id);
    if (timer) {
      clearInterval(timer);
      pollTimers.current.delete(id);
    }
    setRunningIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  useEffect(() => {
    return () => {
      for (const timer of pollTimers.current.values()) clearInterval(timer);
    };
  }, []);

  const handleRunNow = useCallback(
    async (id: string) => {
      setRunningIds((prev) => new Set(prev).add(id));
      try {
        await runAutomationNow(id);
      } catch {
        stopPolling(id);
        return;
      }

      const started = Date.now();
      const timer = setInterval(async () => {
        if (Date.now() - started > POLL_TIMEOUT_MS) {
          stopPolling(id);
          return;
        }
        try {
          const { runs } = await fetchAutomationRuns(id, 1);
          const latest = runs[0];
          if (latest?.finished_at) {
            stopPolling(id);
            load();
          }
        } catch {
          /* keep polling */
        }
      }, POLL_INTERVAL_MS);
      pollTimers.current.set(id, timer);
    },
    [stopPolling, load],
  );

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<Automation>[]>(
    () => [
      {
        id: "run",
        header: "",
        size: 44,
        cell: ({ row }) => {
          const a = row.original;
          const isRunning = runningIds.has(a.id);
          return (
            <button
              className={styles.runBtn}
              disabled={a.status !== "active" || isRunning}
              title={isRunning ? "Running…" : "Run now"}
              onClick={(e) => {
                e.stopPropagation();
                handleRunNow(a.id);
              }}
            >
              {isRunning ? (
                <Loader2 size={14} className={cn(styles.spinning)} />
              ) : (
                <Play size={14} />
              )}
            </button>
          );
        },
      },
      { accessorKey: "name", header: "Name", size: 200 },
      { accessorKey: "capability_name", header: "Capability", size: 200 },
      { accessorKey: "trigger_type", header: "Trigger", size: 110 },
      {
        accessorKey: "schedule",
        header: "Schedule",
        size: 160,
        cell: ({ getValue }) => (
          <code style={{ fontSize: "var(--text-small)" }}>
            {getValue<string | null>() ?? "—"}
          </code>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 100,
        cell: ({ getValue }) => (
          <Pill variant={statusVariant(getValue<string>())}>
            {getValue<string>()}
          </Pill>
        ),
      },
      {
        accessorKey: "next_run_at",
        header: "Next run",
        size: 170,
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          return v ? v.replace("T", " ").slice(0, 19) : "—";
        },
      },
      {
        id: "counts",
        header: "Runs (fail)",
        size: 110,
        cell: ({ row }) =>
          `${row.original.run_count} (${row.original.failure_count})`,
      },
    ],
    [runningIds, handleRunNow],
  );

  return (
    <div className={styles.tabContent}>
      <div className={styles.toolbar}>
        <Select
          value={status}
          onValueChange={setStatus}
          placeholder="Status"
          aria-label="Filter by status"
        >
          {STATUS_OPTIONS.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </Select>
        <Button onClick={onNew}>+ New Automation</Button>
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {rows.length} automations
        </span>
      </div>
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(r) => r.id}
        onRowClick={(r) => onRowClick(r.id)}
        selectedRowId={selectedId}
        keyboardNav
        loading={loading}
        pageSize={25}
        emptyState="No automations match the current filter."
      />
    </div>
  );
}
