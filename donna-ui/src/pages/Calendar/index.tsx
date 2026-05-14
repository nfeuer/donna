import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import RefreshButton from "../../components/RefreshButton";
import { fetchCalendarWeek } from "../../api/calendar";
import type { CalendarEvent } from "../../api/calendar";
import { CalendarGrid } from "./CalendarGrid";

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + days);
  return toISODate(d);
}

function formatWeekRange(weekStart: string, weekEnd: string): string {
  const s = new Date(weekStart);
  const e = new Date(weekEnd);
  const startStr = s.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const endStr = e.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  return `${startStr} – ${endStr}`;
}

export default function CalendarPage() {
  const [weekDate, setWeekDate] = useState(() => toISODate(new Date()));
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [weekStart, setWeekStart] = useState("");
  const [weekEnd, setWeekEnd] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchCalendarWeek(weekDate);
      setEvents(resp.events ?? []);
      setWeekStart(resp.week_start ?? "");
      setWeekEnd(resp.week_end ?? "");
      setWarnings(resp.warnings ?? []);
    } catch {
      setEvents([]);
      setWarnings([]);
    } finally {
      setLoading(false);
    }
  }, [weekDate]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRefresh = useCallback(async () => {
    await doFetch();
  }, [doFetch]);

  const weekLabel = useMemo(
    () => (weekStart && weekEnd ? formatWeekRange(weekStart, weekEnd) : ""),
    [weekStart, weekEnd],
  );

  const isCurrentWeek = useMemo(() => {
    if (!weekStart) return true;
    const now = new Date();
    const ws = new Date(weekStart);
    const we = new Date(weekEnd);
    return now >= ws && now <= we;
  }, [weekStart, weekEnd]);

  const meta = warnings.includes("google_calendar_unavailable")
    ? "Google Calendar unavailable — showing Donna tasks only"
    : `${events.length} events`;

  return (
    <div>
      <PageHeader
        eyebrow="Schedule"
        title="Calendar"
        meta={meta}
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginRight: "var(--space-3)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "var(--text-label)", color: "#6a9cd4" }}>
                <span style={{ width: 8, height: 8, background: "#6a9cd4", borderRadius: 1, display: "inline-block" }} />
                Google
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "var(--text-label)", color: "var(--color-accent)" }}>
                <span style={{ width: 8, height: 8, background: "var(--color-accent)", borderRadius: 1, display: "inline-block" }} />
                Donna
              </div>
            </div>
            <Button variant="ghost" size="sm" aria-label="Previous week" onClick={() => setWeekDate(addDays(weekDate, -7))}>
              <ChevronLeft size={16} />
            </Button>
            <span style={{ fontSize: "var(--text-body)", color: "var(--color-text)", fontWeight: 500, minWidth: 160, textAlign: "center" }}>
              {weekLabel}
            </span>
            <Button variant="ghost" size="sm" aria-label="Next week" onClick={() => setWeekDate(addDays(weekDate, 7))}>
              <ChevronRight size={16} />
            </Button>
            {!isCurrentWeek && (
              <Button variant="ghost" size="sm" onClick={() => setWeekDate(toISODate(new Date()))}>
                Today
              </Button>
            )}
            <RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />
          </div>
        }
      />
      <CalendarGrid events={events} loading={loading} weekStart={weekStart || weekDate} />
    </div>
  );
}
