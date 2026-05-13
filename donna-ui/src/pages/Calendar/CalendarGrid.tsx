import { useMemo } from "react";
import type { CalendarEvent } from "../../api/calendar";
import { cn } from "../../lib/cn";
import styles from "./CalendarGrid.module.css";

interface CalendarGridProps {
  events: CalendarEvent[];
  loading: boolean;
  weekStart: string;
}

const HOUR_PX = 48;
const START_HOUR = 8;
const END_HOUR = 18;
const TOTAL_HOURS = END_HOUR - START_HOUR;
const GRID_HEIGHT = TOTAL_HOURS * HOUR_PX;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MIN_EVENT_HEIGHT = 20;

function formatHourLabel(hour: number): string {
  if (hour === 0) return "12 AM";
  if (hour < 12) return `${hour} AM`;
  if (hour === 12) return "12 PM";
  return `${hour - 12} PM`;
}

function formatEventTime(isoString: string): string {
  const d = new Date(isoString);
  const h = d.getHours();
  const m = d.getMinutes();
  const suffix = h >= 12 ? "PM" : "AM";
  const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return m === 0 ? `${display} ${suffix}` : `${display}:${String(m).padStart(2, "0")}`;
}

function getEventStyle(event: CalendarEvent): React.CSSProperties {
  const start = new Date(event.start);
  const end = new Date(event.end);
  const startMinutes = start.getHours() * 60 + start.getMinutes();
  const endMinutes = end.getHours() * 60 + end.getMinutes();
  const gridStartMinutes = START_HOUR * 60;

  const top = ((startMinutes - gridStartMinutes) / 60) * HOUR_PX;
  const rawHeight = ((endMinutes - startMinutes) / 60) * HOUR_PX;
  const height = Math.max(rawHeight, MIN_EVENT_HEIGHT);

  return { top: `${top}px`, height: `${height}px` };
}

function getDayIndex(isoString: string, weekStartDate: Date): number {
  const d = new Date(isoString);
  const diff = Math.floor(
    (d.getTime() - weekStartDate.getTime()) / (1000 * 60 * 60 * 24),
  );
  return Math.max(0, Math.min(6, diff));
}

export function CalendarGrid({ events, loading, weekStart }: CalendarGridProps) {
  const weekStartDate = useMemo(() => new Date(weekStart), [weekStart]);

  const today = useMemo(() => {
    const now = new Date();
    return now.toISOString().slice(0, 10);
  }, []);

  const dayDates = useMemo(() => {
    const dates: Date[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(weekStartDate);
      d.setDate(d.getDate() + i);
      dates.push(d);
    }
    return dates;
  }, [weekStartDate]);

  const { timedByDay, allDayByDay } = useMemo(() => {
    const timed: CalendarEvent[][] = Array.from({ length: 7 }, () => []);
    const allDay: CalendarEvent[][] = Array.from({ length: 7 }, () => []);
    for (const ev of events) {
      const idx = getDayIndex(ev.start, weekStartDate);
      if (ev.all_day) {
        allDay[idx].push(ev);
      } else {
        timed[idx].push(ev);
      }
    }
    return { timedByDay: timed, allDayByDay: allDay };
  }, [events, weekStartDate]);

  const hasAllDay = allDayByDay.some((day) => day.length > 0);

  const hourLabels = useMemo(() => {
    const labels: { hour: number; top: number }[] = [];
    for (let h = START_HOUR; h <= END_HOUR; h++) {
      labels.push({ hour: h, top: (h - START_HOUR) * HOUR_PX });
    }
    return labels;
  }, []);

  if (loading) {
    return (
      <div className={styles.grid} style={{ height: `${GRID_HEIGHT}px` }}>
        <div className={styles.timeAxis} />
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className={styles.dayColumn} style={{ height: `${GRID_HEIGHT}px` }}>
            <div className={cn(styles.skeleton, styles.skeletonEvent)} style={{ top: "48px", height: "36px" }} />
            <div className={cn(styles.skeleton, styles.skeletonEvent)} style={{ top: "144px", height: "48px" }} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div className={styles.dayHeaders}>
        <div className={styles.dayHeaderSpacer} />
        {dayDates.map((d, i) => {
          const dateStr = d.toISOString().slice(0, 10);
          const isToday = dateStr === today;
          const isWeekend = i >= 5;
          return (
            <div
              key={i}
              className={styles.dayHeader}
              data-today={isToday}
              data-weekend={isWeekend}
            >
              <div className={styles.dayLabel}>{DAY_NAMES[i]}</div>
              <div className={styles.dayNumber}>{d.getDate()}</div>
            </div>
          );
        })}
      </div>

      {hasAllDay && (
        <div className={styles.allDayRow}>
          <div className={styles.allDaySpacer} />
          {allDayByDay.map((dayEvents, i) => (
            <div key={i} className={styles.allDayCell}>
              {dayEvents.map((ev) => (
                <div
                  key={ev.id}
                  className={cn(
                    styles.event,
                    ev.source === "google" ? styles.eventGoogle : styles.eventDonna,
                  )}
                  style={{ position: "relative", minHeight: "18px" }}
                >
                  <span className={styles.eventTitle}>{ev.title}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className={styles.grid}>
        <div className={styles.timeAxis} style={{ height: `${GRID_HEIGHT}px` }}>
          {hourLabels.map(({ hour, top }) => (
            <div key={hour} className={styles.hourLabel} style={{ top: `${top}px` }}>
              {formatHourLabel(hour)}
            </div>
          ))}
        </div>
        {dayDates.map((d, i) => {
          const dateStr = d.toISOString().slice(0, 10);
          const isToday = dateStr === today;
          const isWeekend = i >= 5;
          return (
            <div
              key={i}
              className={styles.dayColumn}
              data-today={isToday}
              data-weekend={isWeekend}
              style={{ height: `${GRID_HEIGHT}px` }}
            >
              {timedByDay[i].map((ev) => (
                <div
                  key={ev.id}
                  className={cn(
                    styles.event,
                    ev.source === "google" ? styles.eventGoogle : styles.eventDonna,
                  )}
                  style={getEventStyle(ev)}
                >
                  <span className={styles.eventTime}>{formatEventTime(ev.start)}</span>
                  <span className={styles.eventTitle}>{ev.title}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </>
  );
}
