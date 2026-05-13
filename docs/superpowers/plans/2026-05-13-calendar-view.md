# Calendar View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a time-slotted week view calendar page to the Donna dashboard that merges Google Calendar events with Donna-scheduled tasks into a single read-only view.

**Architecture:** New FastAPI endpoint (`GET /calendar/week`) fetches events from `GoogleCalendarClient` (all configured calendars) and scheduled tasks from the task DB, normalizes both into a unified event schema, and returns a merged response. React frontend renders a CSS Grid week view with events absolutely positioned at their actual hours, color-coded by source (blue=Google, gold=Donna).

**Tech Stack:** FastAPI, GoogleCalendarClient, aiosqlite, React 18, TypeScript, CSS Modules, CSS Grid

**Design spec:** `docs/superpowers/specs/2026-05-13-calendar-view-design.md`

---

## File Map

**Backend (create):**
- `src/donna/api/routes/calendar_week.py` — merged calendar+tasks endpoint
- `tests/api/test_calendar_week.py` — unit tests

**Backend (modify):**
- `src/donna/api/__init__.py` — register router, wire calendar client + config to `app.state`

**Frontend (create):**
- `donna-ui/src/api/calendar.ts` — types and `fetchCalendarWeek()`
- `donna-ui/src/pages/Calendar/index.tsx` — page component
- `donna-ui/src/pages/Calendar/CalendarGrid.tsx` — week grid renderer
- `donna-ui/src/pages/Calendar/CalendarGrid.module.css` — grid styles

**Frontend (modify):**
- `donna-ui/src/layout/Sidebar.tsx` — add Calendar nav entry
- `donna-ui/src/App.tsx` — add `/calendar` route

---

## Task 1: Backend — Calendar week route + tests

**Files:**
- Create: `src/donna/api/routes/calendar_week.py`
- Create: `tests/api/test_calendar_week.py`

### Step-by-step

- [ ] **Step 1: Write failing test for `_week_bounds` helper**

```python
# tests/api/test_calendar_week.py
from __future__ import annotations

import pytest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from donna.api.routes.calendar_week import _week_bounds


class TestWeekBounds:
    def test_midweek_date_returns_monday_to_sunday(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 13), tz)  # Wednesday
        assert start == datetime(2026, 5, 11, 0, 0, 0, tzinfo=tz)  # Monday
        assert end == datetime(2026, 5, 17, 23, 59, 59, tzinfo=tz)  # Sunday

    def test_monday_returns_same_week(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 11), tz)  # Monday
        assert start.date() == date(2026, 5, 11)
        assert end.date() == date(2026, 5, 17)

    def test_sunday_returns_same_week(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 17), tz)  # Sunday
        assert start.date() == date(2026, 5, 11)
        assert end.date() == date(2026, 5, 17)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_calendar_week.py -v`
Expected: ImportError — `_week_bounds` does not exist yet.

- [ ] **Step 3: Implement `_week_bounds` and route module skeleton**

```python
# src/donna/api/routes/calendar_week.py
from __future__ import annotations

from datetime import date as date_type, datetime, time, timedelta
from typing import Any

from fastapi import Query, Request
from zoneinfo import ZoneInfo

from donna.api.auth import CurrentUser, user_router
from donna.tasks.db_models import TaskStatus

router = user_router()


def _week_bounds(ref: date_type, tz: ZoneInfo) -> tuple[datetime, datetime]:
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(sunday, time(23, 59, 59), tzinfo=tz)
    return start, end


def _is_all_day(start: datetime, end: datetime) -> bool:
    return (
        start.hour == 0
        and start.minute == 0
        and end.hour == 0
        and end.minute == 0
        and (end - start).days >= 1
    )
```

- [ ] **Step 4: Run `_week_bounds` tests to verify they pass**

Run: `pytest tests/api/test_calendar_week.py::TestWeekBounds -v`
Expected: 3 passed.

- [ ] **Step 5: Write failing test for the route handler — merged events**

```python
# Append to tests/api/test_calendar_week.py
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass


@dataclass(frozen=True)
class FakeCalendarEvent:
    event_id: str
    calendar_id: str
    summary: str
    start: datetime
    end: datetime
    donna_managed: bool
    donna_task_id: str | None
    etag: str
    attendees: tuple = ()


@dataclass
class FakeTask:
    id: str
    title: str
    scheduled_start: str
    estimated_duration: int
    priority: str
    domain: str
    donna_managed: bool


class TestGetCalendarWeek:
    @pytest.fixture
    def tz(self) -> ZoneInfo:
        return ZoneInfo("America/New_York")

    @pytest.fixture
    def mock_request(self, tz: ZoneInfo) -> MagicMock:
        req = MagicMock()
        req.app.state.calendar_timezone = "America/New_York"
        req.app.state.calendar_ids = ["personal"]
        req.app.state.calendar_client = AsyncMock()
        req.app.state.db = AsyncMock()
        return req

    @pytest.mark.asyncio
    async def test_merges_google_and_donna_events(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        gcal_event = FakeCalendarEvent(
            event_id="abc",
            calendar_id="personal",
            summary="Team standup",
            start=datetime(2026, 5, 13, 9, 0, tzinfo=tz),
            end=datetime(2026, 5, 13, 9, 30, tzinfo=tz),
            donna_managed=False,
            donna_task_id=None,
            etag="e1",
        )
        mock_request.app.state.calendar_client.list_events.return_value = [gcal_event]

        donna_task = FakeTask(
            id="42",
            title="Review proposals",
            scheduled_start="2026-05-13T10:30:00-04:00",
            estimated_duration=3600,
            priority="high",
            domain="work",
            donna_managed=True,
        )
        mock_request.app.state.db.list_tasks.return_value = [donna_task]

        from donna.api.routes.calendar_week import get_calendar_week

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-13",
        )

        assert result["count"] == 2
        assert result["events"][0]["source"] == "google"
        assert result["events"][0]["title"] == "Team standup"
        assert result["events"][1]["source"] == "donna"
        assert result["events"][1]["title"] == "Review proposals"
        assert result["events"][1]["priority"] == "high"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/api/test_calendar_week.py::TestGetCalendarWeek::test_merges_google_and_donna_events -v`
Expected: AttributeError — `get_calendar_week` not defined yet.

- [ ] **Step 7: Implement the route handler**

Append to `src/donna/api/routes/calendar_week.py`:

```python
@router.get("/week")
async def get_calendar_week(
    request: Request,
    user_id: CurrentUser,
    ref_date: str | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    tz_name = getattr(request.app.state, "calendar_timezone", "UTC")
    tz = ZoneInfo(tz_name)

    if ref_date:
        ref = date_type.fromisoformat(ref_date)
    else:
        ref = datetime.now(tz=tz).date()

    week_start, week_end = _week_bounds(ref, tz)

    events: list[dict[str, Any]] = []
    warnings: list[str] = []

    cal_client = getattr(request.app.state, "calendar_client", None)
    if cal_client is not None:
        calendar_ids: list[str] = getattr(request.app.state, "calendar_ids", [])
        for cal_id in calendar_ids:
            try:
                gcal_events = await cal_client.list_events(cal_id, week_start, week_end)
                for ev in gcal_events:
                    events.append({
                        "id": f"gcal_{ev.event_id}",
                        "title": ev.summary,
                        "start": ev.start.isoformat(),
                        "end": ev.end.isoformat(),
                        "source": "google",
                        "calendar_id": cal_id,
                        "all_day": _is_all_day(ev.start, ev.end),
                    })
            except Exception:
                warnings.append(f"calendar_fetch_failed:{cal_id}")
    else:
        warnings.append("google_calendar_unavailable")

    db = request.app.state.db
    all_scheduled = await db.list_tasks(user_id=user_id, status=TaskStatus.SCHEDULED)
    for task in all_scheduled:
        if not task.scheduled_start:
            continue
        try:
            start = datetime.fromisoformat(task.scheduled_start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if week_start <= start <= week_end:
                duration = task.estimated_duration or 3600
                end_dt = start + timedelta(seconds=duration)
                events.append({
                    "id": f"donna_{task.id}",
                    "title": task.title,
                    "start": start.isoformat(),
                    "end": end_dt.isoformat(),
                    "source": "donna",
                    "priority": task.priority,
                    "domain": task.domain,
                    "all_day": False,
                })
        except ValueError:
            continue

    events.sort(key=lambda e: e["start"])

    result: dict[str, Any] = {
        "user_id": user_id,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "events": events,
        "count": len(events),
    }
    if warnings:
        result["warnings"] = warnings
    return result
```

- [ ] **Step 8: Run merged events test to verify it passes**

Run: `pytest tests/api/test_calendar_week.py::TestGetCalendarWeek::test_merges_google_and_donna_events -v`
Expected: PASS.

- [ ] **Step 9: Write failing test for graceful degradation (no calendar client)**

```python
# Append to TestGetCalendarWeek in tests/api/test_calendar_week.py
    @pytest.mark.asyncio
    async def test_returns_donna_only_when_calendar_unavailable(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        mock_request.app.state.calendar_client = None

        donna_task = FakeTask(
            id="42",
            title="Review proposals",
            scheduled_start="2026-05-13T10:30:00-04:00",
            estimated_duration=3600,
            priority="high",
            domain="work",
            donna_managed=True,
        )
        mock_request.app.state.db.list_tasks.return_value = [donna_task]

        from donna.api.routes.calendar_week import get_calendar_week

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-13",
        )

        assert result["count"] == 1
        assert result["events"][0]["source"] == "donna"
        assert "google_calendar_unavailable" in result["warnings"]
```

- [ ] **Step 10: Run degradation test to verify it passes**

Run: `pytest tests/api/test_calendar_week.py::TestGetCalendarWeek::test_returns_donna_only_when_calendar_unavailable -v`
Expected: PASS (the implementation already handles this case via `getattr` with `None` default).

- [ ] **Step 11: Write test for Donna task end time computation from duration**

```python
# Append to TestGetCalendarWeek in tests/api/test_calendar_week.py
    @pytest.mark.asyncio
    async def test_donna_end_computed_from_duration(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        mock_request.app.state.calendar_client = None

        donna_task = FakeTask(
            id="99",
            title="Quick task",
            scheduled_start="2026-05-14T14:00:00-04:00",
            estimated_duration=1800,  # 30 minutes
            priority="medium",
            domain="personal",
            donna_managed=True,
        )
        mock_request.app.state.db.list_tasks.return_value = [donna_task]

        from donna.api.routes.calendar_week import get_calendar_week

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-14",
        )

        event = result["events"][0]
        assert event["start"] == "2026-05-14T14:00:00-04:00"
        assert event["end"] == "2026-05-14T14:30:00-04:00"
```

- [ ] **Step 12: Run all tests in the file**

Run: `pytest tests/api/test_calendar_week.py -v`
Expected: All pass.

- [ ] **Step 13: Commit**

```bash
git add src/donna/api/routes/calendar_week.py tests/api/test_calendar_week.py
git commit -m "feat(api): add /calendar/week endpoint merging Google Calendar + Donna tasks"
```

---

## Task 2: Backend — Wire calendar client into FastAPI app.state + register route

**Files:**
- Modify: `src/donna/api/__init__.py`

**Context:** The `GoogleCalendarClient` is currently built in `src/donna/cli_wiring.py` via `_try_build_calendar_client()` and passed to background services, but not available to the FastAPI API. The calendar config lives in `config/calendar.yaml`. We need to make both available on `app.state` so the calendar week route can access them.

### Step-by-step

- [ ] **Step 1: Read the current FastAPI app initialization**

Read `src/donna/api/__init__.py` to understand the lifespan function and how `app.state` is populated. Also read `src/donna/cli_wiring.py` to find `_try_build_calendar_client()` and understand how the calendar client is constructed.

- [ ] **Step 2: Add calendar client + config to app.state in the lifespan**

In the FastAPI lifespan function in `src/donna/api/__init__.py`, add after the existing `app.state.db` setup:

```python
# Calendar client (non-fatal — None if credentials missing)
from donna.integrations.calendar import GoogleCalendarClient
import yaml

cal_config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "calendar.yaml"
cal_timezone = "UTC"
cal_ids: list[str] = []
cal_client = None

if cal_config_path.exists():
    with open(cal_config_path) as f:
        cal_config = yaml.safe_load(f)
    cal_timezone = cal_config.get("timezone", "UTC")
    calendars = cal_config.get("calendars", {})
    cal_ids = [
        c["calendar_id"] for c in calendars.values()
        if c.get("calendar_id")
    ]
    try:
        cal_client = GoogleCalendarClient(cal_config)
        await cal_client.authenticate()
    except Exception:
        cal_client = None

app.state.calendar_client = cal_client
app.state.calendar_timezone = cal_timezone
app.state.calendar_ids = cal_ids
```

Adapt this to match the exact lifespan pattern in the file (it may use `async with` or `@asynccontextmanager`). If `_try_build_calendar_client()` in `cli_wiring.py` is reusable, import and call it instead of duplicating the construction logic.

- [ ] **Step 3: Register the calendar_week router**

In `src/donna/api/__init__.py`, add alongside the existing router includes:

```python
from donna.api.routes import calendar_week

app.include_router(calendar_week.router, prefix="/calendar", tags=["calendar"])
```

- [ ] **Step 4: Verify the route is reachable**

Start the API server and test:

```bash
curl -s "http://localhost:8200/calendar/week?date=2026-05-13" | python -m json.tool
```

Expected: JSON response with `week_start`, `week_end`, `events` array (may be empty if no tasks are scheduled), `count`. If Google Calendar is not authenticated, should include `warnings: ["google_calendar_unavailable"]`.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/__init__.py
git commit -m "feat(api): wire calendar client into FastAPI app.state and register /calendar router"
```

---

## Task 3: Frontend — API client types and fetch function

**Files:**
- Create: `donna-ui/src/api/calendar.ts`

### Step-by-step

- [ ] **Step 1: Create the API client file**

```typescript
// donna-ui/src/api/calendar.ts
import client from "./client";

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  source: "google" | "donna";
  calendar_id?: string;
  priority?: string;
  domain?: string;
  all_day: boolean;
}

export interface CalendarWeekResponse {
  user_id: string;
  week_start: string;
  week_end: string;
  events: CalendarEvent[];
  count: number;
  warnings?: string[];
}

export async function fetchCalendarWeek(
  date?: string,
): Promise<CalendarWeekResponse> {
  const params: Record<string, string> = {};
  if (date) params.date = date;
  const { data } = await client.get<CalendarWeekResponse>("/calendar/week", {
    params,
  });
  return data;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/api/calendar.ts
git commit -m "feat(ui): add calendar API client types and fetch function"
```

---

## Task 4: Frontend — CalendarGrid component + CSS

**Files:**
- Create: `donna-ui/src/pages/Calendar/CalendarGrid.tsx`
- Create: `donna-ui/src/pages/Calendar/CalendarGrid.module.css`

**Context:** This is the core visual component — a time-slotted week grid with events positioned at their actual hours. Structure: CSS Grid with a time axis column (44px) and 7 day columns (1fr). Each day column is `position: relative` with events `position: absolute` inside. Hour lines use a repeating gradient.

### Step-by-step

- [ ] **Step 1: Create the CSS module**

```css
/* donna-ui/src/pages/Calendar/CalendarGrid.module.css */
.grid {
  display: grid;
  grid-template-columns: 44px repeat(7, 1fr);
  gap: 0;
}

.dayHeaders {
  display: grid;
  grid-template-columns: 44px repeat(7, 1fr);
  gap: 0;
  margin-bottom: 0;
}

.dayHeaderSpacer {
  /* empty cell above time axis */
}

.dayHeader {
  text-align: center;
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
}

.dayHeader[data-today="true"] {
  border-bottom-color: var(--color-accent-border);
}

.dayHeader[data-weekend="true"] {
  opacity: 0.4;
}

.dayLabel {
  font-size: var(--text-eyebrow);
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 1px;
}

.dayHeader[data-today="true"] .dayLabel {
  color: var(--color-accent);
  font-weight: 600;
}

.dayNumber {
  font-size: var(--text-body);
  color: var(--color-text-muted);
  margin-top: 2px;
}

.dayHeader[data-today="true"] .dayNumber {
  color: var(--color-bg);
  font-weight: 600;
  background: var(--color-accent);
  width: 26px;
  height: 26px;
  line-height: 26px;
  border-radius: 13px;
  margin: 2px auto 0;
}

/* All-day events banner row */
.allDayRow {
  display: grid;
  grid-template-columns: 44px repeat(7, 1fr);
  gap: 0;
  min-height: 0;
}

.allDayRow:not(:empty) {
  border-bottom: 1px solid var(--color-border);
}

.allDaySpacer {
  /* empty cell */
}

.allDayCell {
  padding: 2px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* Time axis */
.timeAxis {
  position: relative;
}

.hourLabel {
  position: absolute;
  right: var(--space-2);
  font-size: var(--text-eyebrow);
  color: var(--color-text-muted);
  transform: translateY(-5px);
  line-height: 1;
}

/* Day columns */
.dayColumn {
  position: relative;
  border-right: 1px solid var(--color-border-subtle);
  background-image: repeating-linear-gradient(
    to bottom,
    var(--color-border) 0,
    var(--color-border) 1px,
    transparent 1px,
    transparent 48px
  );
}

.dayColumn[data-today="true"] {
  background-color: var(--color-accent-soft);
  background-image: repeating-linear-gradient(
    to bottom,
    var(--color-accent-border) 0,
    var(--color-accent-border) 1px,
    transparent 1px,
    transparent 48px
  );
}

.dayColumn[data-weekend="true"] {
  opacity: 0.4;
}

.dayColumn:last-child {
  border-right: none;
}

/* Events */
.event {
  position: absolute;
  left: 2px;
  right: 2px;
  border-radius: 0 var(--radius-card) var(--radius-card) 0;
  padding: 2px 4px;
  overflow: hidden;
  min-height: 20px;
  cursor: default;
  z-index: 1;
}

.eventGoogle {
  background: rgba(106, 156, 212, 0.12);
  border-left: 2px solid #6a9cd4;
}

.eventDonna {
  background: rgba(212, 169, 67, 0.08);
  border-left: 2px solid var(--color-accent);
}

.eventTime {
  font-size: 8px;
  color: var(--color-text-muted);
  display: block;
  line-height: 1.2;
}

.eventTitle {
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  line-height: 1.3;
}

.eventGoogle .eventTitle {
  color: #6a9cd4;
}

.eventDonna .eventTitle {
  color: var(--color-accent);
}

/* Loading skeleton */
.skeleton {
  animation: pulse 1.5s ease-in-out infinite;
  background: var(--color-surface);
  border-radius: var(--radius-card);
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}

.skeletonEvent {
  position: absolute;
  left: 4px;
  right: 4px;
  border-radius: var(--radius-card);
}
```

- [ ] **Step 2: Create the CalendarGrid component**

```tsx
// donna-ui/src/pages/Calendar/CalendarGrid.tsx
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
      {/* Day headers */}
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

      {/* All-day events row */}
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

      {/* Time grid */}
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
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Calendar/CalendarGrid.tsx donna-ui/src/pages/Calendar/CalendarGrid.module.css
git commit -m "feat(ui): add CalendarGrid component with time-slotted week view"
```

---

## Task 5: Frontend — Calendar page with data fetching and navigation

**Files:**
- Create: `donna-ui/src/pages/Calendar/index.tsx`

**Context:** Follows the same data fetching pattern as `pages/Dashboard/index.tsx`: state for data + loading, `useCallback` fetch function, `useEffect` trigger, auto-refresh via `RefreshButton`. Week navigation is ±7 days via prev/next buttons and a "Today" reset.

### Step-by-step

- [ ] **Step 1: Create the Calendar page component**

```tsx
// donna-ui/src/pages/Calendar/index.tsx
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
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const startStr = s.toLocaleDateString("en-US", opts);
  const endFull: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" };
  const endStr = e.toLocaleDateString("en-US", endFull);
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
      setEvents(resp.events);
      setWeekStart(resp.week_start);
      setWeekEnd(resp.week_end);
      setWarnings(resp.warnings ?? []);
    } catch {
      setEvents([]);
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
            <Button variant="ghost" size="sm" onClick={() => setWeekDate(addDays(weekDate, -7))}>
              <ChevronLeft size={16} />
            </Button>
            <span style={{ fontSize: "var(--text-body)", color: "var(--color-text)", fontWeight: 500, minWidth: 160, textAlign: "center" }}>
              {weekLabel}
            </span>
            <Button variant="ghost" size="sm" onClick={() => setWeekDate(addDays(weekDate, 7))}>
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
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: No errors. If `Button` props don't match (check `variant` and `size` values), adjust to match the actual Button primitive props.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Calendar/index.tsx
git commit -m "feat(ui): add Calendar page with data fetching and week navigation"
```

---

## Task 6: Frontend — Sidebar nav entry + routing

**Files:**
- Modify: `donna-ui/src/layout/Sidebar.tsx`
- Modify: `donna-ui/src/App.tsx`

### Step-by-step

- [ ] **Step 1: Add Calendar to sidebar navigation**

In `donna-ui/src/layout/Sidebar.tsx`:

1. Add `CalendarDays` to the lucide-react import.
2. Add to `NAV_ITEMS` array after the Tasks entry:

```tsx
{ path: "/calendar", label: "Calendar", icon: <CalendarDays size={18} /> },
```

- [ ] **Step 2: Add route in App.tsx**

In `donna-ui/src/App.tsx`:

1. Import the CalendarPage component:
```tsx
import CalendarPage from "./pages/Calendar";
```

2. Add the route inside the `<Route element={<AppShell />}>` block, near the other routes:
```tsx
<Route path="/calendar" element={<ErrorBoundary><CalendarPage /></ErrorBoundary>} />
```

- [ ] **Step 3: Verify TypeScript compiles and Vite builds**

Run: `cd donna-ui && npx tsc --noEmit && npx vite build`
Expected: No errors.

- [ ] **Step 4: Manual smoke test**

Start the dev server (`cd donna-ui && npm run dev`) and the backend API. Navigate to `/calendar` in the browser:

1. Verify "Calendar" appears in the sidebar with the CalendarDays icon
2. Verify the page loads with the week grid showing hour rows from 8 AM to 6 PM
3. Verify prev/next arrows change the week
4. Verify "Today" button appears when viewing a non-current week and snaps back
5. Verify Google Calendar events appear in blue and Donna tasks in gold
6. Verify today's column has a gold-tinted background
7. Verify events are positioned at their correct hours and sized by duration
8. If Google Calendar is not connected, verify the "Google Calendar unavailable" warning appears

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/layout/Sidebar.tsx donna-ui/src/App.tsx
git commit -m "feat(ui): add Calendar to sidebar navigation and routing"
```
