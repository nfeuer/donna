# LLM Gateway Queue, Priority, and Rate Limiting

**Date:** 2026-04-11
**Status:** Draft
**Scope:** Redesign of the LLM gateway (`/llm/*` routes) to add a priority queue, preemption, rate limiting, budget separation, cloud model access, alerting, and observability.

---

## Problem

The current LLM gateway forwards requests directly to Ollama with no queuing. This causes several issues:

- **No priority**: an external API caller can saturate the RTX 3090, blocking Donna's own agent work.
- **No rate limiting**: a single caller can monopolize the GPU with no throttling.
- **Budget conflation**: external calls count against Donna's $20/day pause threshold, meaning a busy external service can shut down Donna's agents.
- **No observability**: no queue depth, wait times, or interruption metrics.
- **Several bugs**: API key read at import time, `/chat` endpoint fakes multi-turn, `json_mode` ignored, health check never skips Ollama, startup scripts missing `--env-file`, middleware logs health check noise.

## Architecture

### Two-Queue System

Two queues, one worker coroutine, one popper that decides which queue to pull from.

```
                         ┌──────────────────────────┐
  Donna Orchestrator ──► │  Internal Queue           │
  (ModelRouter)          │  (asyncio.PriorityQueue)  │
                         │  priority: critical /      │
                         │    normal / background     │
                         └────────┬──────────────────┘
                                  │
                         ┌────────▼──────────────────┐
                         │  Queue Popper              │
                         │                            │
                         │  1. Internal first (always) │──► OllamaProvider
                         │  2. Check schedule drain    │    (or AnthropicProvider
                         │  3. External if clear       │     if allow_cloud)
                         │  4. Preempt if active hours │
                         └────────▲──────────────────┘
                                  │
                         ┌────────┴──────────────────┐
  External API calls ──► │  External Queue            │
  (POST /llm/*)          │  (asyncio.Queue, FIFO)     │
                         └───────────────────────────┘
```

**Internal queue** — `asyncio.PriorityQueue`. Items are `(priority, sequence_number, QueueItem)`. Sequence number breaks ties (FIFO within priority). Three priority levels:

| Level | Value | When | Examples |
|-------|-------|------|----------|
| critical | 0 | User actively waiting | `parse_task`, `challenge_task` after user input |
| normal | 1 | Scheduled or triggered work | `generate_digest`, `extract_preferences`, `dedup_check` |
| background | 2 | Speculative/optional | `generate_nudge`, `generate_reminder`, spot checks |

Priority is assigned by mapping `task_type` to a level via config (see Configuration section).

**External queue** — plain `asyncio.Queue`, FIFO. All external callers are equal. Mid-chain requests (continuation of an already-started job) go to the front of the queue.

**Worker** — single background coroutine running for the lifetime of the process. Calls `OllamaProvider.complete()` (or `AnthropicProvider` if `allow_cloud`). Resolves the caller's `asyncio.Future` on completion.

### Caller Interaction via Futures

API requests and orchestrator calls interact with the queue through `asyncio.Future`:

1. Request arrives → `QueueItem` created with a new `Future` → enqueued
2. Caller `await`s the `Future`
3. Worker pops the item, executes, resolves the `Future` with the result (or exception)
4. Caller receives the response

No timeout at the queue level. External callers set their own HTTP timeout. If the connection drops before the `Future` resolves, the worker detects the cancelled `Future` and skips execution (no wasted GPU time).

When enqueued, response headers include `X-Queue-Position` and `X-Estimated-Wait-Seconds` so the caller can decide whether to wait.

**Max queue depth**: external queue rejects new requests with 503 + `Retry-After` when depth exceeds a configurable limit (default 20).

### Chain Handling

A single external request may require multiple LLM round-trips internally (tool calls, agent loops). The caller sends one request and receives one response.

- Each `QueueItem` wraps the full job lifecycle, not individual LLM calls.
- A **chain ID** (internal, never exposed to callers) links the multi-step calls.
- Between inference steps (e.g., while doing a web lookup), the GPU is released. The next step re-enters the queue at the front of its tier.
- If preempted mid-chain, chain state (current step, intermediate results) is preserved in the `QueueItem`. When resumed, it continues from the last completed step.
- Individual inference steps log to `invocation_log` with the chain ID. Metrics and caller-facing responses count chains, not steps.

### Orchestrator Integration

Donna's own agents currently call `ModelRouter.complete()` which calls providers directly. To use the queue:

- `ModelRouter` gets a `queue` parameter (the `LLMQueueWorker` instance, set during lifespan startup).
- `ModelRouter.complete()` enqueues to the internal queue **only for Ollama (local) calls**. Claude/Anthropic calls bypass the queue entirely — they don't use the GPU and have their own rate limits.
- Priority is determined by a config mapping from `task_type` to priority level. Task types not listed in the `priority_map` default to `normal`.

## Preemption and Scheduling

### Active vs Slow Hours

Configured in `config/llm_gateway.yaml`:

```yaml
scheduling:
  active_hours: "06:00-22:00"
  schedule_drain_minutes: 2
```

The only behavioral difference: during active hours, a Donna task **preempts** (cancels) a running external request. During slow hours, it **waits** for the running external request to finish, then runs next.

In both cases, the internal queue is always checked first. Donna always has priority.

### Popper Decision Tree

On every cycle of the worker loop:

1. **Internal queue has items?**
   - YES → Is the GPU running an external request?
     - YES → Active hours? → Cancel external, save chain state, re-enqueue at front of external queue. Execute internal item.
     - YES → Slow hours? → Wait for external to finish. Then execute internal item.
     - NO → Execute internal item.
   - NO → Continue to step 2.
2. **Scheduled Donna task due within `drain_minutes`?**
   - YES → Idle. Don't pop external. Wait for the scheduled task to arrive.
   - NO → Continue to step 3.
3. **External queue has items?**
   - YES → Pop and execute.
   - NO → Sleep 100ms, re-check.

### Preemption Mechanics

When an external request is cancelled mid-inference:

1. Cancel the `aiohttp` request to Ollama (Ollama stops generating).
2. Mark `QueueItem.interrupted = True`, increment `interrupt_count`.
3. Log to `invocation_log` with `interrupted=true`.
4. Re-enqueue at front of external queue with chain state preserved.
5. On resume, restart the current inference step (not the whole chain).

### Starvation Prevention

If an external request has been interrupted more than N times (configurable, default 3), it is promoted: the popper lets it finish even during active hours before serving the next internal item. Triggers a Discord alert.

### Schedule Awareness

The popper reads upcoming scheduled tasks from the orchestrator's schedule data — the same source that drives `scheduler.daily_recalc` and `scheduler.slot_assigned`. Only checks the next task within `drain_minutes`.

## Rate Limiting

Per-caller rate limiting, configured in `config/llm_gateway.yaml`:

```yaml
rate_limits:
  default:
    requests_per_minute: 10
    requests_per_hour: 100
  callers:
    immich-tagger:
      requests_per_minute: 20
      requests_per_hour: 300
    home-assistant:
      requests_per_minute: 5
```

**Implementation**: in-memory sliding window counters per caller. On startup, counters are rebuilt from `invocation_log` — query the last hour of `external_llm_call` rows grouped by caller.

When a caller exceeds their limit:
- Rejected immediately with **429 Too Many Requests** + `Retry-After` header.
- Not enqueued — doesn't waste a queue slot.
- Counted for alerting.

## Budget Separation

### Donna's Budget (Unchanged)

`BudgetGuard.check_pre_call()` continues to enforce the $20/day pause threshold. Modified to exclude `task_type="external_llm_call"` from its cost query via `CostTracker.get_daily_cost(exclude_task_types=["external_llm_call"])`.

### External Budget (New)

External calls have their own budget:

```yaml
budget:
  daily_external_usd: 5.00
  alert_pct: 80
```

When external daily spend hits the limit, new external requests are rejected with 429. Donna's own work is unaffected.

### Cloud Model Access

External callers can opt into Claude as a fallback:

```json
{
  "prompt": "Analyze this image metadata",
  "allow_cloud": false,
  "caller": "immich-tagger"
}
```

- `allow_cloud: false` (default) — Ollama only. If Ollama is down, 503.
- `allow_cloud: true` — tries Ollama first. If unavailable or model not local, falls back to Claude via `AnthropicProvider`. The gateway performs its own budget check against the external cloud budget (separate from `BudgetGuard` which only covers Donna's internal spend). Goes through the external budget, not Donna's internal budget.

Cloud safeguards:

```yaml
cloud:
  max_per_request_usd: 0.50
  daily_cloud_external_usd: 2.00
```

Requests that would exceed `max_per_request_usd` (estimated from token count) are rejected before calling Claude.

## Alerting

Alerts via existing `NotificationService.dispatch()` to `#donna-debug` Discord channel:

| Trigger | Message |
|---------|---------|
| Caller rate-limited 3x in 5 minutes | "immich-tagger is being rate-limited — 34 req/min (limit: 20)" |
| External queue depth exceeds threshold | "LLM gateway backlog: 14 external requests queued" |
| External request interrupted 3+ times (starvation promotion) | "External request from immich-tagger interrupted 3x — promoting" |
| External queue full, rejecting requests | "LLM gateway full — rejecting requests (queue: 20/20)" |
| External budget at alert threshold | "External LLM spend at 80% of daily limit ($4.00/$5.00)" |

All alerts are debounced — same alert type for same caller fires at most once per 10 minutes.

```yaml
alerts:
  queue_depth_warning: 10
  rate_limit_alert_threshold: 3
  debounce_minutes: 10
```

## Observability

### Queue Status Endpoint

`GET /llm/queue/status`:

```json
{
  "current_request": {
    "type": "external",
    "caller": "immich-tagger",
    "model": "qwen2.5:32b-instruct-q6_K",
    "started_at": "2026-04-11T14:32:01Z",
    "chain_step": 2,
    "chain_total": null
  },
  "internal_queue": {
    "pending": 1,
    "by_priority": {"critical": 0, "normal": 1, "background": 0}
  },
  "external_queue": {
    "pending": 3,
    "oldest_enqueued_at": "2026-04-11T14:31:45Z"
  },
  "scheduled_upcoming": [
    {"task_type": "generate_digest", "scheduled_at": "2026-04-11T15:00:00Z"}
  ],
  "stats_24h": {
    "internal_completed": 47,
    "external_completed": 122,
    "external_interrupted": 3,
    "external_rejected_rate_limit": 8,
    "external_rejected_queue_full": 0,
    "avg_queue_wait_ms": {"internal": 210, "external": 4500}
  },
  "rate_limits": {
    "immich-tagger": {"minute": "12/20", "hour": "87/300"},
    "home-assistant": {"minute": "2/5", "hour": "14/100"}
  },
  "mode": "active"
}
```

Live queue depth and current request come from in-memory state (resets on restart — the queue is empty then anyway). 24-hour stats come from `invocation_log` queries (survive restarts).

### Structured Log Events

| event_type | When |
|------------|------|
| `llm_gateway.enqueued` | Request enters a queue — includes queue, priority, caller |
| `llm_gateway.dequeued` | Worker pops a request — includes wait_ms |
| `llm_gateway.interrupted` | External request preempted — includes caller, chain_step |
| `llm_gateway.completed` | Request finished — includes latency, tokens, chain_length |
| `llm_gateway.rejected` | Request refused — includes reason (rate_limit, queue_full, budget) |
| `llm_gateway.drain_started` | External queue paused for upcoming scheduled task |

These flow through structlog → Promtail → Loki → Grafana and appear in the dashboard log viewer under the `llm_gateway` event type category.

### Invocation Log Fields

New fields added to `invocation_log` entries for gateway calls:

- `queue_wait_ms` — time the request waited in queue before execution started
- `interrupted` — boolean, true if this inference step was preempted
- `chain_id` — links multi-step calls within a single logical request
- `caller` — the external service identifier (already partially captured in `model_alias`)

## Configuration

All gateway config lives in `config/llm_gateway.yaml` (separate from `donna_models.yaml`). Editable via the dashboard config editor. All changes are **hot-reloaded** — when the dashboard saves the file, the config endpoint calls `app.state.llm_queue.reload_config()`. Rate limit counters and queue contents are preserved; only thresholds change.

Full config structure:

```yaml
# LLM Gateway Configuration

enabled: true
api_key: "${DONNA_LLM_API_KEY}"    # resolved from env var

scheduling:
  active_hours: "06:00-22:00"
  schedule_drain_minutes: 2

queue:
  max_external_depth: 20
  max_interrupt_count: 3           # starvation prevention threshold

priority_map:
  # task_type → priority level
  parse_task: critical
  challenge_task: critical
  generate_digest: normal
  extract_preferences: normal
  dedup_check: normal
  prep_research: normal
  task_decompose: normal
  generate_nudge: background
  generate_reminder: background
  generate_weekly_digest: normal

rate_limits:
  default:
    requests_per_minute: 10
    requests_per_hour: 100
  callers:
    immich-tagger:
      requests_per_minute: 20
      requests_per_hour: 300

budget:
  daily_external_usd: 5.00
  alert_pct: 80

cloud:
  max_per_request_usd: 0.50
  daily_cloud_external_usd: 2.00

alerts:
  queue_depth_warning: 10
  rate_limit_alert_threshold: 3
  debounce_minutes: 10

ollama_health_check: true
```

## Bug Fixes (Bundled)

These are straightforward fixes from the original code review, included in this implementation:

1. **API key at import time** — moved to gateway config, loaded in lifespan, hot-reloadable.
2. **`/llm/chat` endpoint removed** — faked multi-turn by flattening messages. Removed until real chat support is needed (requires extending `OllamaProvider`).
3. **`json_mode` wired through** — `OllamaProvider.complete()` gets optional `json_mode` parameter. When `False`, omits `"format": "json"` from payload.
4. **`_check_ollama` skip logic** — uses `ollama_health_check` config flag instead of checking if URL is empty (default URL is non-empty).
5. **`donna-up.sh` / `donna-down.sh`** — add `--env-file "$DOCKER_DIR/.env"` to all `docker compose` commands.
6. **Health check log noise** — `RequestLoggingMiddleware` excludes `/health` and `/admin/health` paths.

## Database Migration

An Alembic migration adds the following nullable columns to `invocation_log`:

- `queue_wait_ms INTEGER` — time spent waiting in queue before execution
- `interrupted BOOLEAN DEFAULT 0` — whether this step was preempted
- `chain_id TEXT` — links multi-step calls within a logical request
- `caller TEXT` — external service identifier

These are nullable so existing rows are unaffected. The migration is backwards-compatible.

## New Files

| File | Purpose |
|------|---------|
| `src/donna/llm/queue.py` | `LLMQueueWorker` — the two queues, popper, worker loop |
| `src/donna/llm/rate_limiter.py` | `RateLimiter` — per-caller sliding window counters |
| `src/donna/llm/types.py` | `QueueItem`, `ChainState`, priority enum |
| `config/llm_gateway.yaml` | Gateway configuration (split from `donna_models.yaml`) |
| `tests/unit/test_llm_queue.py` | Queue ordering, preemption, chain handling |
| `tests/unit/test_llm_rate_limiter.py` | Rate limit enforcement and rebuild-from-log |

## Modified Files

| File | Change |
|------|--------|
| `src/donna/api/routes/llm.py` | Rewrite — enqueue instead of direct Ollama call, remove `/chat`, add `/queue/status`, wire `json_mode` and `allow_cloud` |
| `src/donna/api/__init__.py` | Lifespan creates `LLMQueueWorker`, stores in `app.state`; load gateway config; middleware excludes health paths |
| `src/donna/models/providers/ollama.py` | Add `json_mode` parameter to `complete()` |
| `src/donna/models/router.py` | Add queue parameter, enqueue to internal queue for local model calls |
| `src/donna/cost/tracker.py` | Add `exclude_task_types` parameter to `get_daily_cost()` |
| `src/donna/cost/budget.py` | Exclude `external_llm_call` from pause threshold check |
| `src/donna/api/routes/admin_config.py` | Post-save hook for `llm_gateway.yaml` hot-reload |
| `src/donna/api/routes/admin_health.py` | Use config flag for Ollama health check |
| `src/donna/api/routes/admin_logs.py` | Add new `llm_gateway` event types |
| `config/donna_models.yaml` | Remove `llm_gateway` section (moved to own file) |
| `docker/.env.example` | Already has `DONNA_LLM_API_KEY` |
| `scripts/donna-up.sh` | Add `--env-file` to all compose commands |
| `scripts/donna-down.sh` | Add `--env-file` to all compose commands |
| `donna-ui/nginx.conf` | No change needed (proxy already configured) |
