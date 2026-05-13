# GPU-Aware Multi-Tier Extraction Pipeline — Design Spec

> **For agentic workers:** This spec describes a multi-component system. Use `superpowers:writing-plans` to create the implementation plan, then `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute.

**Goal:** Replace the current product_watch pipeline (fetch HTML → clean → send to LLM) with a multi-tier extraction system that tries the cheapest local path first, uses smart GPU model management to minimize swap overhead, and only falls back to Claude as a last resort — with full visibility into every tier.

**Architecture:** A Playwright browser sidecar handles page rendering and content extraction. The existing LLM Gateway Queue gains GPU model awareness to coordinate swaps safely. The automation scheduler groups same-model work together. Three extraction tiers cascade: local text, local vision, Claude tool_use.

**Tech Stack:** Playwright (Chromium headless), Ollama (qwen2.5:32b-instruct-q6_K + qwen2.5-vl:7b), Claude API with tool_use, existing LLM Gateway Queue, Docker Compose.

**Spec references:** §4 (model routing), §5.4–5.5 (task types & routing), §23 (skills & capabilities)

---

## 1. Playwright Browser Sidecar

### Container

A new Docker container (`donna-browser`) running a lightweight Python HTTP service with Playwright. Defined in `docker/Dockerfile.browser`, added to `docker/donna-app.yml` so it's co-managed with the orchestrator and discoverable during rebuilds.

The container runs headless Chromium and is stateless — no sessions or cookies persisted between requests. Each request gets a fresh browser context that is closed after completion.

### API Endpoints

**`POST /extract-text`**

Navigates to the given URL, waits for page load, extracts `innerText` from a configurable CSS selector.

Request:
```json
{
  "url": "https://example.com/product",
  "selector": "main",
  "timeout_ms": 15000
}
```

Response:
```json
{
  "text": "Nike Air Max 90\n$129.99\nSizes: 8 9 10 11\nIn Stock",
  "url": "https://example.com/product",
  "selector_used": "main",
  "timestamp": "2026-05-13T03:00:12Z",
  "duration_ms": 2340
}
```

Defaults: `selector` = `"body"`, `timeout_ms` = `15000`.

**`POST /screenshot`**

Navigates to the given URL, takes a full-page screenshot, saves it to the shared volume.

Request:
```json
{
  "url": "https://example.com/product",
  "timeout_ms": 15000
}
```

Response:
```json
{
  "file_path": "/data/browser/screenshots/2026-05-13T030012_abc123.png",
  "page_title": "Nike Air Max 90 — Example Store",
  "url": "https://example.com/product",
  "timestamp": "2026-05-13T03:00:12Z",
  "duration_ms": 3100
}
```

Screenshots save to a shared Docker volume mounted at `/data/browser/screenshots/` on both the sidecar and orchestrator containers.

**`GET /gallery`**

A lightweight HTML page (vanilla HTML/CSS/JS, no framework) for browsing all extractions:

- Thumbnail grid of screenshots sorted newest-first
- Each card shows: thumbnail, URL, timestamp, whether text extraction was also done
- Click to expand: full-size screenshot on one side, extracted text on the other
- Filter bar: search by URL, date range picker

Accessible at `donna-browser:3100/gallery`. Caddy can proxy to `donna.local/browser/gallery`.

Both endpoints return structured errors on navigation failure (timeout, DNS resolution, HTTP error). On failure, the response includes `error`, `error_type`, and `duration_ms`.

### Storage Format

Each extraction saves a metadata JSON alongside any screenshot:

```
/data/browser/screenshots/
  2026-05-13T030012_abc123.png
  2026-05-13T030012_abc123.json
```

The JSON file contains:
```json
{
  "url": "https://example.com/product",
  "timestamp": "2026-05-13T03:00:12Z",
  "text": "extracted text content if text extraction was done",
  "selector": "main",
  "duration_ms": 2340,
  "automation_id": "auto_abc123",
  "screenshot_path": "2026-05-13T030012_abc123.png"
}
```

The gallery reads these JSON files to build its index — no database needed.

### Orchestrator Tools

Two new tools registered in the tool registry:

- `browser_extract_text(url, selector?, timeout_ms?)` — calls `/extract-text`, returns the response dict
- `browser_screenshot(url, timeout_ms?)` — calls `/screenshot`, returns the response dict including the file path

Both tools log through structlog: URL, latency, result size, file path for screenshots.

### Logging

The sidecar logs structured JSON to stdout (Promtail picks it up into Loki). Each request logs:

| Field | Description |
|-------|-------------|
| `url` | Requested URL |
| `action` | `extract-text` or `screenshot` |
| `duration_ms` | Total time including page render |
| `response_bytes` | Size of extracted text or screenshot |
| `status` | `success`, `error`, `timeout` |
| `error` | Error message if status != success |

---

## 2. GPU-Aware Model Management via LLM Gateway Queue

### Integration Point

All GPU model coordination lives in the existing `LLMQueueWorker` (`src/donna/llm/queue.py`), which already serializes all Ollama requests through a single worker. Since it processes one item at a time, there is no concurrent access to the GPU — no drain logic needed.

### Model State Tracking

The worker tracks the currently-loaded Ollama model by polling `GET /api/ps` after each swap. Before executing a queue item that specifies a `required_model`, the worker checks whether that model is already loaded.

### Queue Item Extension

`QueueItem` (`src/donna/llm/types.py`) gains an optional `required_model: str | None` field. When set, the worker checks the loaded model before execution and swaps if needed. When `None`, the item runs on whatever model is currently loaded (existing behavior).

### Model-Affinity Sorting

When `_pop_next()` selects the next item from items of equal priority, it prefers items whose `required_model` matches the currently-loaded model. This naturally batches same-model work together and minimizes swaps without reordering priorities.

Implementation: within a priority band, partition candidates into "matches current model" and "needs swap". Serve the matching partition first. When the matching partition is empty, pop from the swap partition (which triggers a model load).

### Home Model & Auto-Restore

Config declares a `home_model`. After the last non-home-model request completes and no more non-home requests are queued, the worker waits `restore_home_delay_s` (configurable, default 30s) then:

1. Sends a request to Ollama with the departing model using `keep_alive: "0"` to force immediate unload
2. Sends a tiny warmup prompt to the home model to trigger loading
3. Updates the tracked loaded model

The delay prevents thrashing if another non-home request arrives shortly after a batch.

### Swap Metrics

The worker tracks rolling metrics (no hard caps):

| Metric | Description |
|--------|-------------|
| `swaps_this_hour` | Rolling count of model swaps in the last 60 minutes |
| `swap_duration_ms` | Duration of the most recent swap (model load time) |
| `avg_swap_duration_ms_1h` | Average swap duration over the last hour |
| `swap_wait_ms` | How long the triggering request waited for the swap |
| `swap_overhead_pct_1h` | Percentage of worker time spent swapping vs. executing |
| `loaded_model` | Currently loaded model name |
| `is_home` | Whether the loaded model is the home model |
| `queued_by_model` | Count of pending items grouped by required_model |

### Alert Thresholds

Configurable thresholds that trigger Discord DM notifications when breached:

| Threshold | Default | Alert Message |
|-----------|---------|---------------|
| `swaps_per_hour_warning` | 4 | "GPU swapped {n} times in the last hour. Consider consolidating automation schedules." |
| `swap_wait_ms_warning` | 60000 | "Last GPU swap took {n}s. Model loading is slow — check Ollama health." |
| `swap_overhead_pct_warning` | 25 | "{n}% of queue time spent loading models. Review model affinity groupings." |

Alerts are actionable — they tell you what's happening and suggest what to do. The system keeps working; nothing is silently degraded or capped.

### Configuration

Additions to `config/llm_gateway.yaml`:

```yaml
gpu:
  home_model: qwen2.5:32b-instruct-q6_K
  swap_timeout_s: 120
  restore_home_delay_s: 30
  alerts:
    swaps_per_hour_warning: 4
    swap_wait_ms_warning: 60000
    swap_overhead_pct_warning: 25
```

### Status Endpoint Enhancement

`GET /llm/queue/status` response gains a `gpu` section with all tracked metrics. The existing SSE stream (`GET /llm/queue/stream`) emits `gpu_swap_started` and `gpu_swap_completed` events with model names and duration.

---

## 3. Scheduler Model-Affinity Grouping

### Automation Config Extension

Each automation gains an optional `gpu_model` field declaring which local model it needs:

```yaml
trigger_type: cadence
schedule: "once_daily"
gpu_model: local_vision
preferred_window: "01:00-06:00"
```

- `gpu_model` maps to a model alias in `donna_models.yaml`. The scheduler uses this to group automations. If omitted, defaults to the home model.
- `preferred_window` is an optional time window for flexible automations ("once_daily"). The scheduler places them in this window when possible. If the automation has a fixed cron schedule, the cron takes precedence.

### Batch Scheduling Logic

When the scheduler computes run times for flexible automations:

1. Group automations by `gpu_model`
2. Within each group, assign consecutive `next_run_at` times (spaced by estimated run duration + buffer)
3. Place non-home-model groups in their `preferred_window` (default: `01:00-06:00`)
4. Place home-model groups in any available slot

This results in one swap into the vision model, all vision work runs, one swap back. For 100 product watches where 90 use Tier 1 (text/32B) and 10 use Tier 2 (vision/7B), that's exactly 2 swaps per night instead of up to 20.

### Schema

The `automations` table gains two nullable columns:

- `gpu_model TEXT` — model alias
- `preferred_window TEXT` — time window string (e.g., `"01:00-06:00"`)

Alembic migration adds these columns with `NULL` defaults (no impact on existing automations).

---

## 4. Multi-Tier Product Watch Pipeline

### Tier Overview

| Tier | Method | Model | Swap? | Cost |
|------|--------|-------|-------|------|
| 1 | Playwright text extraction | 32B (home) | No | ~$0.00 |
| 2 | Playwright screenshot | 7B vision (batched) | Yes, batched | ~$0.00 |
| 3 | Claude tool_use with URL | Claude Sonnet | No | ~$0.01-0.05 |

### Tier 1: Playwright Text Extraction → Local 32B

1. `browser_extract_text` tool calls the sidecar with the product URL and a configurable CSS selector (per-product in automation inputs, default `"body"`)
2. Sidecar renders the page, extracts `innerText`, saves text + metadata to gallery volume
3. Extracted text goes to the local 32B model for structured extraction (price, sizes, stock, title)
4. If extraction succeeds and output validates against the schema, tier 1 is done

**Failure conditions that trigger Tier 2:**
- Text extraction returns empty or very short content (< 50 chars)
- LLM extraction returns malformed output (schema validation failure)
- LLM extraction returns low-confidence output (all fields null/empty)
- Context overflow (text too large for 32B context window — unlikely with innerText but possible)

### Tier 2: Playwright Screenshot → Local Vision Model

1. `browser_screenshot` tool captures the page, saves PNG to gallery volume
2. The extraction step is re-enqueued to the LLM Gateway Queue with `required_model: qwen2.5-vl:7b` and `BACKGROUND` priority
3. When the vision model is loaded (immediately during slow hours, or in the next affinity batch), the screenshot is sent for structured extraction
4. If extraction succeeds and output validates, tier 2 is done

**Failure conditions that trigger Tier 3:**
- Vision model returns malformed or empty output
- Screenshot is blank or unusable (page didn't render)
- Vision model unavailable (not pulled, Ollama error)

### Tier 3: Claude with tool_use

1. A new LLM step fires routed to Claude (`model: parser`)
2. The prompt is minimal: the product URL + output schema + a `web_fetch` tool definition
3. Claude calls `web_fetch(url)` via tool_use, receives the page content, extracts structured data
4. Claude's extraction is authoritative — if this fails, the entire run fails and alerts the user

**Prompt:**
```
Extract product information from this URL: {{ inputs.url }}

Use the web_fetch tool to retrieve the page. Return structured data matching the output schema.
```

Claude receives the `web_fetch` tool and calls it. The orchestrator executes the tool and returns the result to Claude. Claude extracts from the fetched content. Total prompt cost is minimal — just the instruction + tool definition + schema. The fetched HTML appears as a tool result.

### Tier Tracking

Each run records which tier succeeded in the step result:

```json
{
  "tier": "tier_1_text",
  "extraction": { "price_usd": 129.99, ... },
  "tier_1_attempted": true,
  "tier_2_attempted": false,
  "tier_3_attempted": false
}
```

This feeds the automation run history and tier stats API.

### Conditional Steps in Skill YAML

The skill executor gains support for `condition` fields on steps. A step with a `condition` only runs if the Jinja expression evaluates truthy. This keeps the tier cascade declarative in YAML.

**Step success semantics:** Each step result in `state` gains a `.success` boolean. A step is successful if it completed without error and its output validated against the schema (if one is specified). A step with `on_failure: continue` that fails sets `.success = false` but does not abort the skill — subsequent steps can check this in their `condition`.

**`gpu_model` on steps vs. automations:** The step-level `gpu_model` tells the executor which model to request when enqueuing the LLM call (maps to `QueueItem.required_model`). The automation-level `gpu_model` (Section 3) tells the scheduler how to group and schedule the automation for swap efficiency. They serve different layers — the step-level field controls execution, the automation-level field controls scheduling.

```yaml
steps:
  - name: extract_text
    kind: tool
    tools: [browser_extract_text]
    tool_invocations:
      - tool: browser_extract_text
        args:
          url: "{{ inputs.url }}"
          selector: "{{ inputs.selector | default('body') }}"
        store_as: page_text

  - name: try_local_extract
    kind: llm
    prompt: steps/extract_product_info.md
    output_schema: schemas/product_info.json
    gpu_model: local_parser
    on_failure: continue

  - name: screenshot_fallback
    kind: tool
    tools: [browser_screenshot]
    condition: "not state.try_local_extract.success"
    tool_invocations:
      - tool: browser_screenshot
        args:
          url: "{{ inputs.url }}"
        store_as: screenshot

  - name: try_vision_extract
    kind: llm
    prompt: steps/extract_from_screenshot.md
    output_schema: schemas/product_info.json
    gpu_model: local_vision
    condition: "not state.try_local_extract.success"
    on_failure: continue

  - name: claude_fallback
    kind: llm
    prompt: steps/extract_via_claude.md
    output_schema: schemas/product_info.json
    model: parser
    tools: [web_fetch]
    condition: "not (state.try_local_extract.success or state.try_vision_extract.success)"

  - name: format_output
    kind: llm
    prompt: steps/format_output.md
```

### Per-Product Selector Config

Each product watch automation can specify a CSS selector in its inputs:

```json
{
  "url": "https://store.example.com/product/nike-air-max-90",
  "selector": "#product-detail",
  "max_price_usd": 150,
  "required_size": "10"
}
```

If a product consistently falls to Tier 2 or 3, configuring a tighter selector can improve Tier 1 success by focusing text extraction on the product area rather than the full page body.

---

## 5. Observability & Dashboard Integration

### API Enhancements

**`GET /llm/queue/status` — GPU section:**

The existing queue status endpoint gains a `gpu` object with all swap metrics from Section 2: `loaded_model`, `is_home`, `swaps_this_hour`, `last_swap_at`, `last_swap_duration_ms`, `swap_overhead_pct_1h`, `queued_by_model`.

**`GET /llm/queue/stream` — New SSE events:**

- `gpu_swap_started` — `{from_model, to_model, triggered_by_task}`
- `gpu_swap_completed` — `{from_model, to_model, duration_ms}`

**`GET /admin/automations/{id}/tier-stats` — New endpoint:**

Returns aggregated tier success counts over a configurable window:

```json
{
  "automation_id": "auto_abc",
  "window_days": 30,
  "total_runs": 100,
  "tier_1_text": 85,
  "tier_2_vision": 10,
  "tier_3_claude": 5,
  "estimated_claude_cost_usd": 0.15
}
```

### Dashboard Additions

**Automations tab — tier pill per run:**
Each row in the automation run history shows a color-coded pill indicating which tier handled it:
- Green: Tier 1 (Local/Text)
- Yellow: Tier 2 (Local/Vision)
- Orange: Tier 3 (Claude)

Hover shows escalation reason (e.g., "text extraction returned empty content").

**Queue/GPU status card:**
A small card on the SkillSystem page showing:
- Currently loaded model name
- Home/away indicator
- Swaps this hour
- Queue depth by model
- Swap overhead percentage

Uses the existing `/llm/queue/status` endpoint, polls on the existing refresh interval.

**Browser gallery link:**
Automation detail view includes a link to the sidecar gallery filtered to that product's URL: `donna-browser:3100/gallery?url={encoded_url}`.

### Alerts

All alerts fire via existing Discord DM channel:

| Trigger | Message |
|---------|---------|
| Swap overhead threshold breached | "GPU swapped {n} times in the last hour, {pct}% overhead. Consider consolidating automation schedules." |
| Product URL falls to Tier 3 repeatedly | "{product_name} fell to Claude {n} times in a row. Check page structure or configure a CSS selector." |
| Vision batch exceeded expected duration | "Vision batch took {n}min for {count} items (expected ~{expected}min). Check Ollama performance." |
| Swap wait time threshold breached | "Last GPU swap took {n}s to load model. Check Ollama health and available VRAM." |

All metrics also logged as structured events to Loki via Promtail — queryable in Grafana.

---

## 6. New File Inventory

| File | Purpose |
|------|---------|
| `docker/Dockerfile.browser` | Playwright sidecar container image |
| `docker/donna-app.yml` (modify) | Add `donna-browser` service |
| `src/donna/skills/tools/browser_extract_text.py` | Tool: calls sidecar `/extract-text` |
| `src/donna/skills/tools/browser_screenshot.py` | Tool: calls sidecar `/screenshot` |
| `src/donna/skills/tools/__init__.py` (modify) | Register new browser tools |
| `src/donna/llm/queue.py` (modify) | GPU state tracking, model-affinity sorting, swap metrics, home model restore |
| `src/donna/llm/types.py` (modify) | `QueueItem.required_model` field, GPU config dataclass |
| `config/llm_gateway.yaml` (modify) | `gpu:` section with home model, swap timeout, alert thresholds |
| `src/donna/automations/scheduler.py` (modify) | Model-affinity grouping, preferred window scheduling |
| `src/donna/automations/models.py` (modify) | `gpu_model`, `preferred_window` fields on AutomationRow |
| `alembic/versions/xxx_add_gpu_model_fields.py` | Migration: add columns to automations table |
| `skills/product_watch/skill.yaml` (modify) | Multi-tier step pipeline with conditions |
| `skills/product_watch/steps/extract_from_screenshot.md` | Vision extraction prompt template |
| `skills/product_watch/steps/extract_via_claude.md` | Claude tool_use extraction prompt template |
| `src/donna/skills/executor.py` (modify) | Conditional step execution (`condition` field) |
| `src/donna/api/routes/automations.py` (modify) | Tier stats endpoint |
| `donna-ui/src/pages/SkillSystem/` (modify) | Tier pills, GPU status card, gallery link |
| `donna-ui/src/api/skillSystem.ts` (modify) | Tier stats API call |
