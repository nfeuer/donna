import { useState, useCallback } from "react";
import { ArrowUp, ArrowDown } from "lucide-react";
import type { ClaudeCall, ClaudeCallsResponse, ClaudeCallsParams } from "../../api/claude";
import { Button } from "../../primitives/Button";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import CallDetail from "./CallDetail";
import CallCompare from "./CallCompare";
import styles from "./claude-inspector.module.css";

interface Props {
  data: ClaudeCallsResponse | null;
  loading: boolean;
  params: ClaudeCallsParams;
  onParamsChange: (params: Partial<ClaudeCallsParams>) => void;
  initialExpandId?: string | null;
}

type SortField = "timestamp" | "task_type" | "model_alias" | "tokens_in" | "tokens_out" | "cost_usd" | "quality_score" | "latency_ms";

const PAGE_SIZE = 25;

export default function CallBrowser({ data, loading, params, onParamsChange, initialExpandId }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(initialExpandId ?? null);
  const [compareIds, setCompareIds] = useState<string[]>([]);

  const handleSort = useCallback((field: SortField) => {
    const newDir = params.sort === field && params.sort_dir === "desc" ? "asc" : "desc";
    onParamsChange({ sort: field, sort_dir: newDir, offset: 0 });
  }, [params.sort, params.sort_dir, onParamsChange]);

  const handleRowClick = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const handleCompareToggle = useCallback((id: string) => {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  }, []);

  const handleClearFilters = useCallback(() => {
    onParamsChange({
      task_type: undefined,
      model: undefined,
      date_from: undefined,
      date_to: undefined,
      offset: 0,
    });
  }, [onParamsChange]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const currentPage = data ? Math.floor(data.offset / PAGE_SIZE) + 1 : 1;

  const sortIndicator = (field: SortField) => {
    if (params.sort !== field) return null;
    return (
      <span className={styles.sortArrow}>
        {params.sort_dir === "asc" ? <ArrowUp size={10} /> : <ArrowDown size={10} />}
      </span>
    );
  };

  const compareCallA = compareIds.length === 2
    ? data?.calls.find((c) => c.id === compareIds[0]) ?? null
    : null;
  const compareCallB = compareIds.length === 2
    ? data?.calls.find((c) => c.id === compareIds[1]) ?? null
    : null;

  return (
    <div>
      {/* Filter Bar */}
      <div className={styles.filterBar}>
        <input
          type="text"
          className={styles.filterInput}
          placeholder="task_type"
          value={params.task_type ?? ""}
          onChange={(e) => onParamsChange({ task_type: e.target.value || undefined, offset: 0 })}
        />
        <input
          type="text"
          className={styles.filterInput}
          placeholder="model"
          value={params.model ?? ""}
          onChange={(e) => onParamsChange({ model: e.target.value || undefined, offset: 0 })}
        />
        <input
          type="date"
          className={styles.filterInput}
          value={params.date_from ?? ""}
          onChange={(e) => onParamsChange({ date_from: e.target.value || undefined, offset: 0 })}
        />
        <input
          type="date"
          className={styles.filterInput}
          value={params.date_to ?? ""}
          onChange={(e) => onParamsChange({ date_to: e.target.value || undefined, offset: 0 })}
        />
        <button type="button" className={styles.clearBtn} onClick={handleClearFilters}>
          Clear filters
        </button>
      </div>

      {/* Compare Bar */}
      {compareIds.length > 0 && (
        <div className={styles.compareBar}>
          <span>{compareIds.length}/2 selected for comparison</span>
          {compareIds.length === 2 && (
            <Button variant="ghost" size="sm" onClick={() => setCompareIds([])}>
              Clear
            </Button>
          )}
        </div>
      )}

      {/* Compare View */}
      {compareCallA && compareCallB && (
        <CallCompare callA={compareCallA} callB={compareCallB} />
      )}

      {/* Table */}
      {loading && !data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} width="100%" height={28} />
          ))}
        </div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th style={{ width: 32 }}></th>
                <th onClick={() => handleSort("timestamp")}>
                  Timestamp{sortIndicator("timestamp")}
                </th>
                <th onClick={() => handleSort("task_type")}>
                  Task Type{sortIndicator("task_type")}
                </th>
                <th onClick={() => handleSort("model_alias")}>
                  Model{sortIndicator("model_alias")}
                </th>
                <th onClick={() => handleSort("tokens_in")}>
                  Tokens In{sortIndicator("tokens_in")}
                </th>
                <th onClick={() => handleSort("tokens_out")}>
                  Tokens Out{sortIndicator("tokens_out")}
                </th>
                <th onClick={() => handleSort("cost_usd")}>
                  Cost{sortIndicator("cost_usd")}
                </th>
                <th onClick={() => handleSort("quality_score")}>
                  Quality{sortIndicator("quality_score")}
                </th>
                <th onClick={() => handleSort("latency_ms")}>
                  Latency{sortIndicator("latency_ms")}
                </th>
              </tr>
            </thead>
            <tbody>
              {data?.calls.map((call) => (
                <CallRow
                  key={call.id}
                  call={call}
                  expanded={expandedId === call.id}
                  compared={compareIds.includes(call.id)}
                  onRowClick={handleRowClick}
                  onCompareToggle={handleCompareToggle}
                />
              ))}
              {data?.calls.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ textAlign: "center", padding: "var(--space-5)", color: "var(--color-text-muted)" }}>
                    No calls matching filters
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {data && data.total > PAGE_SIZE && (
        <div className={styles.pagination}>
          <span className={styles.paginationInfo}>
            Page {currentPage} of {totalPages} ({data.total} total)
          </span>
          <div className={styles.paginationBtns}>
            <Button
              variant="ghost"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => onParamsChange({ offset: (currentPage - 2) * PAGE_SIZE })}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => onParamsChange({ offset: currentPage * PAGE_SIZE })}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Row sub-component ── */

interface CallRowProps {
  call: ClaudeCall;
  expanded: boolean;
  compared: boolean;
  onRowClick: (id: string) => void;
  onCompareToggle: (id: string) => void;
}

function CallRow({ call, expanded, compared, onRowClick, onCompareToggle }: CallRowProps) {
  const ts = new Date(call.timestamp);
  const timeStr = ts.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <>
      <tr
        className={cn(styles.row, expanded && styles.rowSelected)}
        onClick={() => onRowClick(call.id)}
      >
        <td onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={compared}
            onChange={() => onCompareToggle(call.id)}
            title="Select for comparison"
          />
        </td>
        <td>{timeStr}</td>
        <td className={styles.mono}>{call.task_type}</td>
        <td className={styles.mono}>{call.model_alias}</td>
        <td>{call.tokens_in.toLocaleString()}</td>
        <td>{call.tokens_out.toLocaleString()}</td>
        <td className={styles.costCell}>${call.cost_usd.toFixed(4)}</td>
        <td>
          {call.quality_score !== null
            ? `${(call.quality_score * 100).toFixed(0)}%`
            : "—"}
        </td>
        <td>{call.latency_ms.toLocaleString()}ms</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} style={{ padding: "var(--space-3)" }}>
            <CallDetail invocationId={call.id} hasPayload={call.has_payload} />
          </td>
        </tr>
      )}
    </>
  );
}
