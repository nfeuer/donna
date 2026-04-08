import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../primitives/Button";
import { PageHeader } from "../../primitives/PageHeader";
import { Select, SelectItem } from "../../primitives/Select";
import { fetchLogs, type LogEntry, type LogFilters } from "../../api/logs";
import EventTypeTree from "./EventTypeTree";
import { FilterBar, type FilterPreset } from "./FilterBar";
import type { DateRangeValue } from "./DateRangePicker";
import LogTable from "./LogTable";
import TraceView from "./TraceView";
import { SavePresetDialog } from "./SavePresetDialog";
import type { LevelFilterValue } from "./levelStyles";
import styles from "./Logs.module.css";

const PRESETS_KEY = "donna-log-presets";
const PAGE_SIZE_OPTIONS = ["25", "50", "100", "250"] as const;

function loadPresets(): FilterPreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function savePresets(presets: FilterPreset[]): void {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

export default function Logs() {
  // Data state
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  // Filter state
  const [selectedEventTypes, setSelectedEventTypes] = useState<string[]>([]);
  const [level, setLevel] = useState<LevelFilterValue>("");
  const [search, setSearch] = useState("");
  const [dateRange, setDateRange] = useState<DateRangeValue>({ start: null, end: null });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Presets
  const [presets, setPresets] = useState<FilterPreset[]>(loadPresets);
  const [savePresetOpen, setSavePresetOpen] = useState(false);

  // Trace drawer
  const [traceId, setTraceId] = useState<string | null>(null);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const filters: LogFilters = {
        limit: pageSize,
        offset: (page - 1) * pageSize,
      };
      if (selectedEventTypes.length > 0) filters.event_type = selectedEventTypes.join(",");
      if (level) filters.level = level;
      if (search) filters.search = search;
      if (dateRange.start) filters.start = dateRange.start;
      if (dateRange.end) filters.end = dateRange.end;

      const resp = await fetchLogs(filters);
      setEntries(Array.isArray(resp?.entries) ? resp.entries : []);
      setTotal(typeof resp?.total === "number" ? resp.total : 0);
      setSource(resp?.source ?? "");
    } catch {
      setEntries([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [selectedEventTypes, level, search, dateRange, page, pageSize]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleLoadPreset = useCallback(
    (name: string) => {
      const preset = presets.find((p) => p.name === name);
      if (!preset) return;
      setSelectedEventTypes(preset.eventTypes);
      setLevel((preset.level as LevelFilterValue) || "");
      setSearch(preset.search);
      setPage(1);
      toast.success(`Preset "${name}" loaded`);
    },
    [presets],
  );

  const handleDeletePreset = useCallback(
    (name: string) => {
      const next = presets.filter((p) => p.name !== name);
      setPresets(next);
      savePresets(next);
      toast.success(`Preset "${name}" deleted`);
    },
    [presets],
  );

  const handleSavePreset = useCallback(
    (name: string) => {
      const newPreset: FilterPreset = {
        name,
        eventTypes: selectedEventTypes,
        level,
        search,
      };
      const next = [...presets.filter((p) => p.name !== name), newPreset];
      setPresets(next);
      savePresets(next);
      toast.success(`Preset "${name}" saved`);
    },
    [presets, selectedEventTypes, level, search],
  );

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const metaLine = useMemo(() => {
    if (total === 0) return "No events in range";
    const start = (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    return `Showing ${start}–${end} of ${total}`;
  }, [total, page, pageSize]);

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar} aria-label="Event type filter">
        <div className={styles.sidebarTitle}>Event Types</div>
        <EventTypeTree selected={selectedEventTypes} onChange={setSelectedEventTypes} />
      </aside>

      <section className={styles.main}>
        <PageHeader
          eyebrow="Observability"
          title="Logs"
          meta={metaLine}
        />

        <FilterBar
          search={search}
          onSearchChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          level={level}
          onLevelChange={(v) => {
            setLevel(v);
            setPage(1);
          }}
          dateRange={dateRange}
          onDateRangeChange={(v) => {
            setDateRange(v);
            setPage(1);
          }}
          source={source}
          presets={presets}
          onLoadPreset={handleLoadPreset}
          onDeletePreset={handleDeletePreset}
          onOpenSavePreset={() => setSavePresetOpen(true)}
          onRefresh={doFetch}
          refreshing={loading}
        />

        <LogTable
          entries={entries}
          loading={loading}
          onCorrelationClick={setTraceId}
          onTaskClick={(id) => window.open(`/tasks/${id}`, "_blank")}
        />

        <nav className={styles.pagination} aria-label="Logs pagination">
          <div className={styles.pageSizeGroup}>
            <span className={styles.pageSizeLabel}>Rows per page</span>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v));
                setPage(1);
              }}
              aria-label="Rows per page"
            >
              {PAGE_SIZE_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {opt}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div className={styles.pageControls}>
            <span className={styles.pageMeta}>
              Page {page} / {totalPages}
            </span>
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

        <TraceView correlationId={traceId} onClose={() => setTraceId(null)} />
        <SavePresetDialog
          open={savePresetOpen}
          onOpenChange={setSavePresetOpen}
          onSave={handleSavePreset}
        />
      </section>
    </div>
  );
}
