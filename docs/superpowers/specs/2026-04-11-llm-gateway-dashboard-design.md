# LLM Gateway Dashboard Design

## Goal

Add LLM gateway observability to the Donna management UI: a summary card on the main dashboard and a dedicated `/llm-gateway` page with live queue status, historical analytics, per-caller breakdown, and inline config editing.

## Architecture

Two new frontend pages consume a mix of existing and new backend endpoints. Live data flows via SSE (Server-Sent Events) for instant updates without polling. Historical data follows the existing dashboard endpoint pattern (fetch on mount, refresh button). All UI components reuse the existing Donna primitives and chart library.

## Backend Changes

### 1. New SSE endpoint: `GET /llm/queue/stream`

Server-Sent Events stream that pushes queue state on every change.

**Content type:** `text/event-stream`

**Emits on:** enqueue, dequeue, complete, preempt, rate limit rejection

**Heartbeat:** empty comment line every 15s to keep connection alive

**Payload shape** (same as `/llm/queue/status` with additions):

```json
{
  "current_request": {
    "sequence": 41,
    "type": "internal" | "external",
    "caller": "receipt-scanner" | null,
    "model": "qwen2.5:32b",
    "started_at": "2026-04-11T14:32:00Z",
    "task_type": "parse_task" | null,
    "prompt_preview": "Parse the following receipt..."
  },
  "internal_queue": {
    "pending": 0,
    "next_items": [
      {
        "sequence": 43,
        "model": "qwen2.5:32b",
        "task_type": "parse_task",
        "enqueued_at": "2026-04-11T14:32:05Z",
        "prompt_preview": "First 100 chars of prompt..."
      }
    ]
  },
  "external_queue": {
    "pending": 2,
    "next_items": [
      {
        "sequence": 42,
        "caller": "home-inventory",
        "model": "qwen2.5:32b",
        "enqueued_at": "2026-04-11T14:32:10Z",
        "prompt_preview": "First 100 chars of prompt..."
      }
    ]
  },
  "stats_24h": {
    "internal_completed": 34,
    "external_completed": 13,
    "external_interrupted": 3
  },
  "rate_limits": {
    "receipt-scanner": {
      "minute_count": 4,
      "minute_limit": 10,
      "hour_count": 18,
      "hour_limit": 60
    }
  },
  "mode": "active"
}
```

**`next_items`:** Up to 2 items per queue. `prompt_preview` is the first 100 characters of the prompt. The current request also includes `prompt_preview`.

**Full prompt expansion:** When the user clicks to expand, the frontend calls `GET /llm/queue/item/{sequence}?include_prompt=true` to fetch the full prompt text. This avoids sending large prompts on every SSE event.

**Implementation:** FastAPI `StreamingResponse` with `text/event-stream` media type. The queue worker notifies an `asyncio.Event` on state changes; the SSE generator awaits that event and emits the current status.

### 2. New endpoint: `GET /llm/queue/item/{sequence}`

Returns full details for a single queued or in-progress item.

**Query params:**
- `include_prompt` (bool, default false) — include full prompt text

**Response:**
```json
{
  "sequence": 42,
  "type": "external",
  "caller": "receipt-scanner",
  "model": "qwen2.5:32b",
  "enqueued_at": "2026-04-11T14:32:10Z",
  "prompt": "Full prompt text here...",
  "max_tokens": 1024,
  "json_mode": true
}
```

Returns 404 if the item is no longer in the queue (already completed/cancelled).

### 3. Extend `GET /llm/queue/status`

Add `next_items` arrays (up to 2 per queue) with the same shape as the SSE payload. This supports the dashboard summary card which polls on a 30s interval rather than using SSE.

Add `prompt_preview` (first 100 chars) to `current_request`.

### 4. New endpoint: `GET /admin/dashboard/llm-gateway`

Historical aggregation from `invocation_log`, following the same pattern as the 5 existing dashboard endpoints.

**Query params:**
- `days` (int, 1-365, default 30)

**Response:**
```json
{
  "summary": {
    "total_calls": 312,
    "internal_calls": 245,
    "external_calls": 67,
    "total_interrupted": 8,
    "avg_latency_ms": 2100,
    "unique_callers": 3
  },
  "time_series": [
    {
      "date": "2026-04-05",
      "internal": 38,
      "external": 12,
      "interrupted": 1
    }
  ],
  "by_caller": [
    {
      "caller": "receipt-scanner",
      "call_count": 89,
      "avg_latency_ms": 2340,
      "total_tokens_in": 98000,
      "total_tokens_out": 44000,
      "interrupted_count": 3,
      "rejected_count": 0
    }
  ],
  "days": 7
}
```

**Filtering logic:**
- Internal calls: `caller IS NULL AND task_type != 'external_llm_call'`
- External calls: `caller IS NOT NULL OR task_type = 'external_llm_call'`
- Interrupted: `interrupted = 1`
- Rejected: counted from structured log events where `event_type = 'llm_gateway.rejected'` in the Loki log store. Since rejections don't reach `invocation_log` (the request is denied before execution), the `rejected_count` in `by_caller` is populated by querying the application logs. If log querying is unavailable, this field returns 0 and a future iteration can add a `gateway_rejections` table.

### 5. SSE notification mechanism in `LLMQueueWorker`

Add an `asyncio.Condition` (`_state_changed`) to the queue worker. Notify all waiters on every state mutation:
- `enqueue_internal()` / `enqueue_external()` — after putting item
- `process_one()` — after completing/failing an item
- `preempt_external()` — after cancelling

Each SSE connection awaits the condition independently. On notification, it reads the current status via `get_status()` (extended) and emits it. Using `asyncio.Condition` (not `Event`) ensures multiple concurrent SSE consumers each get notified without race conditions.

## Frontend Changes

### 1. Dashboard Summary Card: `LLMQueueCard`

**Location:** `donna-ui/src/pages/Dashboard/LLMQueueCard.tsx`

**Position in grid:** Full-width second row (below CostAnalyticsCard, above the 2x2 grid of other cards).

**Data source:** Polls `/llm/queue/status` every 30s (same interval as the existing dashboard auto-refresh). Participates in the dashboard's `fetchAll()` + manual refresh cycle.

**Layout (using `ChartCard`):**
- **Eyebrow:** "LLM Gateway · Live"
- **Headline metric:** Mode indicator — green dot + "Active" or amber dot + "Slow"
- **Stat strip (5 stats):** Internal Queue | External Queue | Completed (24h) | Interrupted (24h) | Active Callers
- **Bottom section (children slot):**
  - Left half: Current request details (type, caller, model) — shows "Idle" when no request running
  - Right half: Per-caller rate limit usage bars (caller name, N/M rpm, progress bar)
- **Footer link:** "View full LLM Gateway →" routes to `/llm-gateway`

**Interaction:** No expandable prompts on the dashboard card — that's for the dedicated page.

### 2. API client: `donna-ui/src/api/llmGateway.ts`

New API module with:
- `fetchLLMQueueStatus(): Promise<LLMQueueStatusData>` — calls `/llm/queue/status`
- `fetchLLMGatewayAnalytics(days: number): Promise<LLMGatewayData>` — calls `/admin/dashboard/llm-gateway`
- `fetchQueueItemPrompt(sequence: number): Promise<string>` — calls `/llm/queue/item/{sequence}?include_prompt=true`
- `createQueueSSE(): EventSource` — creates SSE connection to `/llm/queue/stream`
- TypeScript interfaces for all response shapes

### 3. Dedicated Page: `donna-ui/src/pages/LLMGateway/index.tsx`

**Sidebar entry:** "LLM Gateway" under a new "Infrastructure" group (or after "Preferences" if no grouping).

**Route:** `/llm-gateway`

**Page header:** Eyebrow "Infrastructure", title "LLM Gateway", with range selector (7/14/30/90d), health pill, and refresh button (refreshes historical data only — live section uses SSE).

**Row 1 — Live Status Strip (3 `Card` components, no full-page refresh):**

Updated in real-time via SSE. React state updates in-place — no page reload.

- **Card 1 — Queue Status:** Mode indicator (Active/Slow with colored Pill), queue depths (Internal N, External N, Priority N)
- **Card 2 — Current Request:** Type, caller, model, elapsed time. Click to expand shows full prompt (fetched via `/llm/queue/item/{sequence}?include_prompt=true`). Shows "Idle" when empty.
- **Card 3 — 24h Stats:** 2x2 grid of Stat components: Internal completed, External completed, Interrupted (warning color), Rejected (error color)

**Queue Preview (below live strip):**

Shows next 2 items from each queue (from SSE `next_items`). Each item is a compact row: caller/task_type, model, relative time ("12s ago"). Click to expand shows full prompt. When queues are empty, shows a muted "No pending requests" message.

**Row 2 — Historical Chart (`ChartCard`, full width):**

Stacked `BarChart` with three series: Internal (accent), External (accentSoft), Interrupted (warning). X-axis: dates. Data from `/admin/dashboard/llm-gateway`. Respects range selector.

Stat strip on the ChartCard: Total Calls | Internal | External | Interrupted | Avg Latency | Unique Callers

**Row 3 — Detail Split (2/3 + 1/3):**

Left (2/3): Per-caller `DataTable` with columns: Caller, Calls, Avg Latency, Tokens, Interrupted, Rate Limit. Historical data from the aggregation endpoint. Rate Limit column shows live usage bar from SSE data (minute count / minute limit). Sortable columns.

Right (1/3): Quick Config `Card`. Shows editable fields: Default RPM, Default RPH, Max Queue Depth, Active Hours (display only). Save button writes to `llm_gateway.yaml` via `PUT /admin/config/llm_gateway.yaml` and triggers hot-reload. Shows success/error toast on save.

### 4. Routing and Sidebar

- Add route `/llm-gateway` → `LLMGateway` page in `App.tsx`
- Add sidebar entry in `Sidebar.tsx`

### 5. Dashboard Integration

- Add `LLMQueueCard` to dashboard grid in `Dashboard/index.tsx`
- Add `fetchLLMQueueStatus` to the dashboard's `fetchAll()` parallel fetch
- Add `LLMQueueStatusData` to `DashboardData` interface
- Update CSS animation delays for the new 6th child (250ms)

### 6. SSE Hook: `useLLMQueueStream`

Custom React hook that manages the `EventSource` lifecycle:
- Opens SSE connection on mount
- Parses incoming JSON events into typed state
- Auto-reconnects on connection loss (EventSource built-in + exponential backoff)
- Closes connection on unmount
- Returns `{ data: LLMQueueStatusData | null, connected: boolean }`

Used by the dedicated page. The dashboard card uses polling instead (simpler, less resource usage for a card that's one of many).

## Component Inventory

| Component | File | New/Existing |
|-----------|------|-------------|
| `LLMQueueCard` | `pages/Dashboard/LLMQueueCard.tsx` | New |
| `LLMGateway` page | `pages/LLMGateway/index.tsx` | New |
| `LLMGateway` CSS | `pages/LLMGateway/LLMGateway.module.css` | New |
| `useLLMQueueStream` hook | `hooks/useLLMQueueStream.ts` | New |
| API client | `api/llmGateway.ts` | New |
| `ChartCard` | `charts/ChartCard.tsx` | Existing |
| `Card` | `primitives/Card.tsx` | Existing |
| `DataTable` | `primitives/DataTable.tsx` | Existing |
| `Pill` | `primitives/Pill.tsx` | Existing |
| `Stat` | `primitives/Stat.tsx` | Existing |
| `BarChart` | `charts/BarChart.tsx` | Existing |
| `PageHeader` | `primitives/PageHeader.tsx` | Existing |
| `Segmented` | `primitives/Segmented.tsx` | Existing |
| `Skeleton` | `primitives/Skeleton.tsx` | Existing |
| `Tooltip` | `primitives/Tooltip.tsx` | Existing |

## Testing

**Backend:**
- Unit test for `/admin/dashboard/llm-gateway` endpoint (mock DB, verify SQL filtering and response shape)
- Unit test for SSE endpoint (verify event format, heartbeat, connection lifecycle)
- Unit test for `/llm/queue/item/{sequence}` (found, not found, with/without prompt)
- Unit test for extended `get_status()` with `next_items`
- Unit test for `_state_changed` event firing on queue mutations

**Frontend:**
- Test `LLMQueueCard` renders stats, mode indicator, rate limit bars, and "View full" link
- Test `LLMGateway` page renders all three rows with mock data
- Test `useLLMQueueStream` hook connects, parses events, and reconnects
- Test prompt expansion click fetches full prompt
- Test quick config save calls PUT endpoint and shows toast

## Out of Scope

- WebSocket infrastructure (SSE is sufficient for server→client push)
- Grafana dashboard integration
- Per-caller config editing from the dedicated page (use the Configs page for per-caller overrides)
- Authentication on the SSE endpoint (admin UI is local-only, no auth required)
