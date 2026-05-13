# Calendar View — Dashboard Week View

**Date:** 2026-05-13
**Spec ref:** spec_v3.md §4.4 (Calendar Integration), §6.1 (REST API), §7.1 (Dashboard)

## Summary

A dedicated calendar page in the Donna dashboard that renders a time-slotted week view merging Google Calendar events and Donna-scheduled tasks. Read-only, designed for testing and verifying that Donna's scheduling is placing tasks correctly around existing calendar commitments.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Data source | Both Google Calendar + Donna tasks merged | Full picture for verifying scheduling correctness |
| Layout | Time-slotted week grid (Google Calendar style) | Events positioned at actual hours, sized by duration — immediately shows gaps, conflicts, and placement accuracy |
| Placement | Separate `/calendar` page | Week grid needs full page width; keeps KPI dashboard focused |
| Interaction | Read-only, no click behavior | Testing/visibility tool, not a task management surface |
| Event detail | Title + start time | Minimum useful info per event bar; priority badges would clutter at week scale |
| Architecture | Single merged backend endpoint | Frontend makes one call; merge logic stays server-side close to data sources |

## API

### `GET /calendar/week`

New route registered in the FastAPI app alongside existing schedule routes.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `date` | ISO date string | today | Reference date; endpoint returns the Mon–Sun week containing this date |

**Response:**

```json
{
  "user_id": "nick",
  "week_start": "2026-05-12T00:00:00-04:00",
  "week_end": "2026-05-18T23:59:59-04:00",
  "events": [
    {
      "id": "gcal_abc123",
      "title": "Team standup",
      "start": "2026-05-12T09:00:00-04:00",
      "end": "2026-05-12T09:30:00-04:00",
      "source": "google",
      "calendar_id": "personal",
      "all_day": false
    },
    {
      "id": "donna_42",
      "title": "Review vendor proposals",
      "start": "2026-05-12T10:30:00-04:00",
      "end": "2026-05-12T11:30:00-04:00",
      "source": "donna",
      "priority": 2,
      "domain": "work",
      "all_day": false
    }
  ],
  "count": 14
}
```

**Behavior:**

- Computes Monday 00:00 and Sunday 23:59:59 for the week containing `date`, in the user's configured timezone (from `config/calendar.yaml`)
- Calls `GoogleCalendarClient.list_events(time_min, time_max)` for all configured calendars
- Queries Donna tasks with `scheduled_start` in the same window
- Normalizes both into the unified event schema above
- Google events get `source: "google"` and carry `calendar_id`
- Donna tasks get `source: "donna"` and carry `priority` and `domain`; `end` is computed as `scheduled_start + estimated_duration` since the task DB stores duration, not end time
- All times serialized in the user's timezone
- All-day events included with `all_day: true`

**Error handling:**

- If Google Calendar client is unavailable (no token, auth expired), return Donna tasks only with a `warnings: ["google_calendar_unavailable"]` field
- If no tasks are scheduled, return empty `events: []`

### Route registration

Add to `src/donna/api/__init__.py` alongside the existing schedule router include.

## Frontend

### File structure

```
donna-ui/src/
  pages/Calendar/
    index.tsx               — page component, data fetching, week navigation
    CalendarGrid.tsx        — time-slotted week grid renderer
    CalendarGrid.module.css — grid layout and event styling
  api/
    calendar.ts             — fetchCalendarWeek() API function
```

### Navigation

Add to `NAV_ITEMS` in `Sidebar.tsx`:

```tsx
{ path: "/calendar", label: "Calendar", icon: <CalendarDays /> }
```

Add route in `App.tsx`:

```tsx
<Route path="/calendar" element={<CalendarPage />} />
```

### Page component (`index.tsx`)

**State:**
- `weekDate: string` — ISO date string for the current reference date (defaults to today)
- `events: CalendarEvent[]` — fetched event list
- `loading: boolean`
- `warnings: string[]` — any warnings from the API (e.g., calendar unavailable)

**Behavior:**
- Fetches `fetchCalendarWeek(weekDate)` on mount and when `weekDate` changes
- Prev/next buttons shift `weekDate` by ±7 days
- "Today" button resets `weekDate` to current date
- Auto-refreshes on 30s interval (matches dashboard pattern)
- Computes `weekStart` / `weekEnd` labels from the response for the header display

**Layout:**
- `PageHeader` with eyebrow "Schedule", title "Calendar"
- Actions area: legend (blue=Google, gold=Donna) + week navigation (◀ date range ▶ Today)
- Body: `<CalendarGrid events={events} loading={loading} weekStart={weekStart} />`

### Grid component (`CalendarGrid.tsx`)

**Props:**
- `events: CalendarEvent[]`
- `loading: boolean`
- `weekStart: string` — ISO date for Monday of the displayed week

**Layout:**
- CSS Grid: `grid-template-columns: 44px repeat(7, 1fr)`
- Time axis column on the left showing hour labels
- Hour rows at 48px height each
- Visible range: 8 AM – 6 PM (10 hour rows, 480px total grid height) — derived from `calendar.yaml` work/personal time windows
- Grid scrolls vertically within the page if needed

**Event positioning:**
- Each day column is a single `position: relative` container spanning the full grid height (all hour rows)
- Events are `position: absolute` within their day column
- `top = (eventStartMinutes - gridStartMinutes) * (48 / 60)` px
- `height = durationMinutes * (48 / 60)` px
- Minimum height: 20px (for very short events, so title remains readable)

**Visual treatment:**
- Google events: `background: rgba(106, 156, 212, 0.12)`, left border `2px solid #6a9cd4`, text color `#6a9cd4`
- Donna tasks: `background: rgba(212, 169, 67, 0.08)`, left border `2px solid #d4a943`, text color `#d4a943`
- Colors defined as CSS custom properties for theme compatibility
- Today column: full-height subtle accent wash via `var(--color-accent-soft)`
- Today date number: filled accent circle (gold background, dark text)
- Weekend columns: `opacity: 0.4`
- Event bar content: start time on first line (8px, muted), title on second line (10px, source color), both with `text-overflow: ellipsis`

**All-day events:**
- Rendered as a horizontal banner row between day headers and the time grid
- Full width of day column, no time positioning
- Same color-coding by source

**Loading state:**
- Skeleton pulse animation on grid cells (same pattern as ChartCard)

### API client (`api/calendar.ts`)

```typescript
export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  source: "google" | "donna";
  calendar_id?: string;
  priority?: number;
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

export async function fetchCalendarWeek(date?: string): Promise<CalendarWeekResponse>
```

Follows existing pattern: axios client, destructure `data`, optional params as `Record<string, string>`.

## What this does NOT include

- Click/hover interaction on events (read-only)
- Drag-and-drop rescheduling
- Event creation from the calendar UI
- Day view or month view (week only)
- Conflict highlighting or overlap detection in the UI (the backend handles conflicts; this just shows what's scheduled)

## Testing

- Backend: pytest for the `/calendar/week` endpoint — mock `GoogleCalendarClient` and task DB, verify merge logic, timezone handling, week boundary computation
- Frontend: verify grid renders with mock data, events position correctly, week navigation works, loading/empty/error states display properly
- Manual: start Donna with calendar connected, open `/calendar`, verify real Google Calendar events appear alongside Donna-scheduled tasks at the correct times
