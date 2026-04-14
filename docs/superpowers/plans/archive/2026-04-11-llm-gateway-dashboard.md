# LLM Gateway Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM gateway observability to the Donna management UI — a summary card on the main dashboard and a dedicated `/llm-gateway` page with live SSE-driven queue status, historical analytics, per-caller breakdown, and inline config editing.

**Architecture:** Backend adds an SSE endpoint for real-time queue state, extends `get_status()` with queue item previews, adds a historical aggregation endpoint matching the existing dashboard pattern, and exposes a single-item detail endpoint. Frontend adds a dashboard card (polling), a dedicated page (SSE), and the supporting API client/hook.

**Tech Stack:** Python/FastAPI (SSE via `StreamingResponse`), asyncio, SQLite, React 18, TypeScript, Recharts, TanStack Table, Playwright E2E tests.

**Spec:** `docs/superpowers/specs/2026-04-11-llm-gateway-dashboard-design.md`

---

## File Structure

### Backend (new/modified)
| File | Responsibility |
|------|---------------|
| `src/donna/llm/queue.py` (modify) | Add `asyncio.Condition`, extend `get_status()` with `next_items`/`prompt_preview`, add `get_item()`, fire condition on mutations |
| `src/donna/api/routes/llm.py` (modify) | Add SSE `/queue/stream`, `/queue/item/{sequence}` endpoints |
| `src/donna/api/routes/admin_dashboard.py` (modify) | Add `/dashboard/llm-gateway` aggregation endpoint |
| `tests/unit/test_llm_queue.py` (modify) | Tests for extended `get_status()`, `get_item()`, condition notification |
| `tests/unit/test_llm_gateway.py` (modify) | Tests for SSE endpoint, item endpoint |
| `tests/unit/test_admin_dashboard_llm.py` (create) | Tests for the new aggregation endpoint |

### Frontend (new/modified)
| File | Responsibility |
|------|---------------|
| `donna-ui/src/api/llmGateway.ts` (create) | API client + TypeScript interfaces |
| `donna-ui/src/hooks/useLLMQueueStream.ts` (create) | SSE EventSource hook |
| `donna-ui/src/pages/Dashboard/LLMQueueCard.tsx` (create) | Dashboard summary card |
| `donna-ui/src/pages/Dashboard/index.tsx` (modify) | Add LLMQueueCard to grid |
| `donna-ui/src/pages/Dashboard/Dashboard.module.css` (modify) | Add 7th child animation delay |
| `donna-ui/src/pages/LLMGateway/index.tsx` (create) | Dedicated page |
| `donna-ui/src/pages/LLMGateway/LLMGateway.module.css` (create) | Page styles |
| `donna-ui/src/App.tsx` (modify) | Add `/llm-gateway` route |
| `donna-ui/src/layout/Sidebar.tsx` (modify) | Add nav entry |
| `donna-ui/tests/e2e/helpers.ts` (modify) | Add mock for `/llm/queue/status` |
| `donna-ui/tests/e2e/smoke/dashboard.spec.ts` (modify) | Update card count assertion |
| `donna-ui/tests/e2e/smoke/llm-gateway.spec.ts` (create) | Smoke test for dedicated page |

---

### Task 1: Extend `get_status()` with `next_items` and `prompt_preview`

**Files:**
- Modify: `src/donna/llm/queue.py:327-358`
- Modify: `tests/unit/test_llm_queue.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_llm_queue.py`:

```python
class TestQueueStatusExtended:
    async def test_status_includes_next_items(self) -> None:
        """get_status() returns up to 2 next_items per queue with prompt_preview."""
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        await worker.enqueue_internal(
            prompt="A" * 200, model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.NORMAL,
        )
        await worker.enqueue_external(
            prompt="B" * 200, model="m", max_tokens=100,
            json_mode=True, caller="test-caller", allow_cloud=False,
        )

        status = worker.get_status()

        # Internal next_items
        assert len(status["internal_queue"]["next_items"]) == 1
        item = status["internal_queue"]["next_items"][0]
        assert item["task_type"] == "parse_task"
        assert item["model"] == "m"
        assert len(item["prompt_preview"]) <= 100
        assert "sequence" in item
        assert "enqueued_at" in item

        # External next_items
        assert len(status["external_queue"]["next_items"]) == 1
        ext = status["external_queue"]["next_items"][0]
        assert ext["caller"] == "test-caller"
        assert len(ext["prompt_preview"]) <= 100

    async def test_status_limits_next_items_to_two(self) -> None:
        """next_items returns at most 2 items even when more are queued."""
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        for i in range(5):
            await worker.enqueue_external(
                prompt=f"prompt-{i}", model="m", max_tokens=100,
                json_mode=True, caller=f"caller-{i}", allow_cloud=False,
            )

        status = worker.get_status()
        assert len(status["external_queue"]["next_items"]) == 2
        assert status["external_queue"]["pending"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_queue.py::TestQueueStatusExtended -v`
Expected: FAIL — `next_items` key not in status dict

- [ ] **Step 3: Implement extended `get_status()`**

In `src/donna/llm/queue.py`, replace the `get_status()` method:

```python
def get_status(self) -> dict[str, Any]:
    """Return queue status for the /llm/queue/status endpoint."""
    current = None
    if self._current_task:
        ct = self._current_task
        current = {
            "sequence": ct.sequence,
            "type": "internal" if ct.is_internal else "external",
            "caller": ct.caller,
            "model": ct.model,
            "started_at": ct.enqueued_at.isoformat(),
            "task_type": ct.task_type,
            "prompt_preview": ct.prompt[:100],
        }

    internal_pending = self._internal.qsize()
    external_pending = self._external.qsize() + len(self._external_priority)

    return {
        "current_request": current,
        "internal_queue": {
            "pending": internal_pending,
            "next_items": self._peek_internal(2),
        },
        "external_queue": {
            "pending": external_pending,
            "next_items": self._peek_external(2),
        },
        "stats_24h": {
            "internal_completed": self._stats["internal_completed"],
            "external_completed": self._stats["external_completed"],
            "external_interrupted": self._stats["external_interrupted"],
        },
        "rate_limits": self._rate_limiter.get_all_usage(),
        "mode": "active" if self._config.is_active_hours() else "slow",
    }

def _peek_internal(self, n: int) -> list[dict[str, Any]]:
    """Peek at next N items in the internal PriorityQueue without removing them."""
    items: list[QueueItem] = []
    # Drain, snapshot, refill
    while not self._internal.empty() and len(items) < n:
        items.append(self._internal.get_nowait())
    result = [self._item_preview(it) for it in items]
    for it in items:
        self._internal.put_nowait(it)
    return result

def _peek_external(self, n: int) -> list[dict[str, Any]]:
    """Peek at next N items from external priority deque + external queue."""
    result: list[dict[str, Any]] = []
    # Priority deque first
    for it in list(self._external_priority)[:n]:
        result.append(self._item_preview(it))
    remaining = n - len(result)
    if remaining <= 0:
        return result
    # Regular queue — drain, snapshot, refill
    items: list[QueueItem] = []
    while not self._external.empty() and len(items) < remaining:
        items.append(self._external.get_nowait())
    result.extend(self._item_preview(it) for it in items)
    for it in items:
        self._external.put_nowait(it)
    return result

def _item_preview(self, item: QueueItem) -> dict[str, Any]:
    """Build a preview dict for a queue item."""
    return {
        "sequence": item.sequence,
        "caller": item.caller,
        "model": item.model,
        "task_type": item.task_type,
        "enqueued_at": item.enqueued_at.isoformat(),
        "prompt_preview": item.prompt[:100],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_queue.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/llm/queue.py tests/unit/test_llm_queue.py
git commit -m "feat(llm-gateway): extend get_status with next_items and prompt_preview"
```

---

### Task 2: Add `get_item()` method and `asyncio.Condition` to queue worker

**Files:**
- Modify: `src/donna/llm/queue.py`
- Modify: `tests/unit/test_llm_queue.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_llm_queue.py`:

```python
class TestQueueGetItem:
    async def test_get_item_returns_queued_item(self) -> None:
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        future = await worker.enqueue_external(
            prompt="full prompt text here", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        # The sequence number is 1 (first item)
        item = worker.get_item(1)
        assert item is not None
        assert item["prompt"] == "full prompt text here"
        assert item["caller"] == "test"
        assert item["sequence"] == 1

    async def test_get_item_returns_none_for_missing(self) -> None:
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        assert worker.get_item(999) is None


class TestStateChangedCondition:
    async def test_enqueue_notifies_condition(self) -> None:
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        notified = False

        async def waiter():
            nonlocal notified
            async with worker.state_changed:
                await worker.state_changed.wait()
                notified = True

        task = asyncio.create_task(waiter())
        # Give the waiter time to start waiting
        await asyncio.sleep(0.01)

        await worker.enqueue_external(
            prompt="p", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        await asyncio.wait_for(task, timeout=1.0)
        assert notified is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_queue.py::TestQueueGetItem tests/unit/test_llm_queue.py::TestStateChangedCondition -v`
Expected: FAIL — `get_item` and `state_changed` don't exist

- [ ] **Step 3: Implement `get_item()` and `asyncio.Condition`**

In `src/donna/llm/queue.py`, add to `__init__`:

```python
# After the existing self._internal_arrived = asyncio.Event() line:
self.state_changed = asyncio.Condition()
```

Add `get_item()` method after `get_status()`:

```python
def get_item(self, sequence: int) -> dict[str, Any] | None:
    """Return full details for a queued or in-progress item by sequence number."""
    # Check current task
    if self._current_task and self._current_task.sequence == sequence:
        return self._item_full(self._current_task)

    # Check internal queue (drain + refill)
    found = None
    items: list[QueueItem] = []
    while not self._internal.empty():
        it = self._internal.get_nowait()
        items.append(it)
        if it.sequence == sequence:
            found = it
    for it in items:
        self._internal.put_nowait(it)
    if found:
        return self._item_full(found)

    # Check external priority deque
    for it in self._external_priority:
        if it.sequence == sequence:
            return self._item_full(it)

    # Check external queue (drain + refill)
    items = []
    while not self._external.empty():
        it = self._external.get_nowait()
        items.append(it)
        if it.sequence == sequence:
            found = it
    for it in items:
        self._external.put_nowait(it)
    if found:
        return self._item_full(found)

    return None

def _item_full(self, item: QueueItem) -> dict[str, Any]:
    """Build a full detail dict for a queue item."""
    return {
        "sequence": item.sequence,
        "type": "internal" if item.is_internal else "external",
        "caller": item.caller,
        "model": item.model,
        "task_type": item.task_type,
        "enqueued_at": item.enqueued_at.isoformat(),
        "prompt": item.prompt,
        "max_tokens": item.max_tokens,
        "json_mode": item.json_mode,
    }
```

Add condition notification to `enqueue_internal()`, `enqueue_external()`, and `process_one()`. After each state mutation, add:

```python
async with self.state_changed:
    self.state_changed.notify_all()
```

Specifically:
- `enqueue_internal()` — after `self._internal_arrived.set()`
- `enqueue_external()` — after the depth alert check (end of method)
- `process_one()` — after setting future result or exception (in the `try` and `except` blocks, before `finally`)
- `preempt_external()` — after re-enqueuing the item

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_queue.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/llm/queue.py tests/unit/test_llm_queue.py
git commit -m "feat(llm-gateway): add get_item() and asyncio.Condition for SSE"
```

---

### Task 3: Add SSE and item detail endpoints

**Files:**
- Modify: `src/donna/api/routes/llm.py`
- Modify: `tests/unit/test_llm_gateway.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_llm_gateway.py`:

```python
from donna.api.routes.llm import llm_queue_item


class TestQueueItem:
    async def test_returns_item_when_found(self) -> None:
        queue = MagicMock()
        queue.get_item.return_value = {
            "sequence": 1,
            "type": "external",
            "caller": "test",
            "model": "m",
            "enqueued_at": "2026-04-11T00:00:00+00:00",
            "prompt": "full prompt",
            "max_tokens": 100,
            "json_mode": True,
        }
        request = _make_request(queue=queue)
        result = await llm_queue_item(1, request)
        assert result["prompt"] == "full prompt"
        assert result["sequence"] == 1

    async def test_returns_404_when_not_found(self) -> None:
        queue = MagicMock()
        queue.get_item.return_value = None
        request = _make_request(queue=queue)
        with pytest.raises(HTTPException) as exc_info:
            await llm_queue_item(999, request)
        assert exc_info.value.status_code == 404
```

Add the import at the top of the test file:

```python
from fastapi import HTTPException
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_gateway.py::TestQueueItem -v`
Expected: FAIL — `llm_queue_item` not importable

- [ ] **Step 3: Implement endpoints**

Add to `src/donna/api/routes/llm.py`:

```python
import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse


@router.get("/queue/item/{sequence}")
async def llm_queue_item(sequence: int, request: Request) -> dict[str, Any]:
    """Return full details for a single queued or in-progress item."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")
    item = queue.get_item(sequence)
    if item is None:
        raise HTTPException(404, "Item not found in queue")
    return item


@router.get("/queue/stream")
async def llm_queue_stream(request: Request) -> StreamingResponse:
    """SSE stream of queue state changes."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")

    async def event_generator():
        try:
            # Send initial state immediately
            status = queue.get_status()
            yield f"data: {json.dumps(status)}\n\n"

            while True:
                try:
                    async with asyncio.timeout(15):
                        async with queue.state_changed:
                            await queue.state_changed.wait()
                    status = queue.get_status()
                    yield f"data: {json.dumps(status)}\n\n"
                except TimeoutError:
                    # Heartbeat
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

Add the `json` import at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_llm_gateway.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/llm.py tests/unit/test_llm_gateway.py
git commit -m "feat(llm-gateway): add SSE stream and item detail endpoints"
```

---

### Task 4: Add `/admin/dashboard/llm-gateway` aggregation endpoint

**Files:**
- Modify: `src/donna/api/routes/admin_dashboard.py`
- Create: `tests/unit/test_admin_dashboard_llm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_admin_dashboard_llm.py`:

```python
"""Tests for the /admin/dashboard/llm-gateway endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.routes.admin_dashboard import get_llm_gateway_analytics


def _make_request(rows_daily=None, rows_caller=None) -> MagicMock:
    """Build a mock request with a mock DB connection."""
    request = MagicMock()
    conn = AsyncMock()

    # We'll set up execute to return different cursors based on call order
    cursors = []

    # Daily time-series cursor
    daily_cursor = AsyncMock()
    daily_cursor.fetchall = AsyncMock(return_value=rows_daily or [])
    cursors.append(daily_cursor)

    # Per-caller cursor
    caller_cursor = AsyncMock()
    caller_cursor.fetchall = AsyncMock(return_value=rows_caller or [])
    cursors.append(caller_cursor)

    conn.execute = AsyncMock(side_effect=cursors)
    request.app.state.db.connection = conn
    return request


class TestLLMGatewayAnalytics:
    async def test_returns_empty_when_no_data(self) -> None:
        request = _make_request()
        result = await get_llm_gateway_analytics(request, days=7)

        assert result["summary"]["total_calls"] == 0
        assert result["summary"]["unique_callers"] == 0
        assert result["time_series"] == []
        assert result["by_caller"] == []
        assert result["days"] == 7

    async def test_aggregates_daily_data(self) -> None:
        daily_rows = [
            # (date, internal, external, interrupted, avg_latency)
            ("2026-04-10", 30, 10, 2, 2100),
            ("2026-04-11", 25, 8, 1, 1900),
        ]
        caller_rows = [
            # (caller, count, avg_lat, tokens_in, tokens_out, interrupted, rejected)
            ("receipt-scanner", 12, 2340, 98000, 44000, 2, 0),
            ("home-inventory", 6, 1820, 32000, 18000, 1, 0),
        ]
        request = _make_request(rows_daily=daily_rows, rows_caller=caller_rows)
        result = await get_llm_gateway_analytics(request, days=7)

        assert result["summary"]["total_calls"] == 73  # 30+10+25+8
        assert result["summary"]["internal_calls"] == 55  # 30+25
        assert result["summary"]["external_calls"] == 18  # 10+8
        assert result["summary"]["total_interrupted"] == 3  # 2+1
        assert result["summary"]["unique_callers"] == 2
        assert len(result["time_series"]) == 2
        assert result["time_series"][0]["date"] == "2026-04-10"
        assert len(result["by_caller"]) == 2
        assert result["by_caller"][0]["caller"] == "receipt-scanner"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_admin_dashboard_llm.py -v`
Expected: FAIL — `get_llm_gateway_analytics` not importable

- [ ] **Step 3: Implement the endpoint**

Add to `src/donna/api/routes/admin_dashboard.py`:

```python
@router.get("/dashboard/llm-gateway")
async def get_llm_gateway_analytics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """LLM gateway metrics from invocation_log.

    Aggregates internal vs external calls, interruptions, and
    per-caller breakdowns using the caller and interrupted columns
    added by the gateway queue migration.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)

    # Daily time-series: internal/external/interrupted counts
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day,
               COUNT(CASE WHEN caller IS NULL AND task_type != 'external_llm_call' THEN 1 END) as internal,
               COUNT(CASE WHEN caller IS NOT NULL OR task_type = 'external_llm_call' THEN 1 END) as external,
               COUNT(CASE WHEN interrupted = 1 THEN 1 END) as interrupted,
               ROUND(AVG(latency_ms), 0) as avg_latency_ms
           FROM invocation_log
           WHERE timestamp >= ?
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    daily_rows = await cursor.fetchall()

    time_series = [
        {
            "date": row[0],
            "internal": row[1],
            "external": row[2],
            "interrupted": row[3],
            "avg_latency_ms": int(row[4] or 0),
        }
        for row in daily_rows
    ]

    total_internal = sum(r[1] for r in daily_rows)
    total_external = sum(r[2] for r in daily_rows)
    total_interrupted = sum(r[3] for r in daily_rows)
    total_calls = total_internal + total_external
    avg_latency = (
        sum(r[4] * (r[1] + r[2]) for r in daily_rows if r[4]) / total_calls
        if total_calls > 0
        else 0
    )

    # Per-caller breakdown
    cursor = await conn.execute(
        """SELECT
               COALESCE(caller, '_internal') as caller_name,
               COUNT(*) as call_count,
               ROUND(AVG(latency_ms), 0) as avg_latency_ms,
               SUM(tokens_in) as total_tokens_in,
               SUM(tokens_out) as total_tokens_out,
               COUNT(CASE WHEN interrupted = 1 THEN 1 END) as interrupted_count,
               0 as rejected_count
           FROM invocation_log
           WHERE timestamp >= ?
               AND (caller IS NOT NULL OR task_type = 'external_llm_call')
           GROUP BY caller_name
           ORDER BY call_count DESC""",
        (since,),
    )
    caller_rows = await cursor.fetchall()

    by_caller = [
        {
            "caller": row[0],
            "call_count": row[1],
            "avg_latency_ms": int(row[2] or 0),
            "total_tokens_in": row[3] or 0,
            "total_tokens_out": row[4] or 0,
            "interrupted_count": row[5],
            "rejected_count": row[6],
        }
        for row in caller_rows
    ]

    unique_callers = len([c for c in by_caller if c["caller"] != "_internal"])

    return {
        "summary": {
            "total_calls": total_calls,
            "internal_calls": total_internal,
            "external_calls": total_external,
            "total_interrupted": total_interrupted,
            "avg_latency_ms": int(avg_latency),
            "unique_callers": unique_callers,
        },
        "time_series": time_series,
        "by_caller": by_caller,
        "days": days,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/test_admin_dashboard_llm.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/admin_dashboard.py tests/unit/test_admin_dashboard_llm.py
git commit -m "feat(llm-gateway): add /admin/dashboard/llm-gateway aggregation endpoint"
```

---

### Task 5: Frontend API client and TypeScript interfaces

**Files:**
- Create: `donna-ui/src/api/llmGateway.ts`

- [ ] **Step 1: Create the API client**

Create `donna-ui/src/api/llmGateway.ts`:

```typescript
import client from "./client";

// --- Interfaces ---

export interface QueueItemPreview {
  sequence: number;
  caller: string | null;
  model: string;
  task_type: string | null;
  enqueued_at: string;
  prompt_preview: string;
}

export interface CurrentRequest {
  sequence: number;
  type: "internal" | "external";
  caller: string | null;
  model: string;
  started_at: string;
  task_type: string | null;
  prompt_preview: string;
}

export interface CallerRateLimit {
  minute_count: number;
  minute_limit: number;
  hour_count: number;
  hour_limit: number;
}

export interface LLMQueueStatusData {
  current_request: CurrentRequest | null;
  internal_queue: {
    pending: number;
    next_items: QueueItemPreview[];
  };
  external_queue: {
    pending: number;
    next_items: QueueItemPreview[];
  };
  stats_24h: {
    internal_completed: number;
    external_completed: number;
    external_interrupted: number;
  };
  rate_limits: Record<string, CallerRateLimit>;
  mode: "active" | "slow";
}

export interface LLMGatewayTimeSeriesEntry {
  date: string;
  internal: number;
  external: number;
  interrupted: number;
  avg_latency_ms: number;
}

export interface LLMGatewayCallerEntry {
  caller: string;
  call_count: number;
  avg_latency_ms: number;
  total_tokens_in: number;
  total_tokens_out: number;
  interrupted_count: number;
  rejected_count: number;
}

export interface LLMGatewayData {
  summary: {
    total_calls: number;
    internal_calls: number;
    external_calls: number;
    total_interrupted: number;
    avg_latency_ms: number;
    unique_callers: number;
  };
  time_series: LLMGatewayTimeSeriesEntry[];
  by_caller: LLMGatewayCallerEntry[];
  days: number;
}

export interface QueueItemDetail {
  sequence: number;
  type: "internal" | "external";
  caller: string | null;
  model: string;
  task_type: string | null;
  enqueued_at: string;
  prompt: string;
  max_tokens: number;
  json_mode: boolean;
}

// --- Fetch functions ---

export async function fetchLLMQueueStatus(): Promise<LLMQueueStatusData> {
  const { data } = await client.get("/llm/queue/status");
  return data;
}

export async function fetchLLMGatewayAnalytics(
  days: number,
): Promise<LLMGatewayData> {
  const { data } = await client.get("/admin/dashboard/llm-gateway", {
    params: { days },
  });
  return data;
}

export async function fetchQueueItemPrompt(
  sequence: number,
): Promise<QueueItemDetail> {
  const { data } = await client.get(`/llm/queue/item/${sequence}`);
  return data;
}

export function createQueueSSEUrl(): string {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  return `${base}/llm/queue/stream`;
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit src/api/llmGateway.ts 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/api/llmGateway.ts
git commit -m "feat(llm-gateway): add frontend API client and TypeScript interfaces"
```

---

### Task 6: SSE React hook `useLLMQueueStream`

**Files:**
- Create: `donna-ui/src/hooks/useLLMQueueStream.ts`

- [ ] **Step 1: Create the hook**

Create `donna-ui/src/hooks/useLLMQueueStream.ts`:

```typescript
import { useEffect, useRef, useState } from "react";
import {
  createQueueSSEUrl,
  type LLMQueueStatusData,
} from "../api/llmGateway";

interface UseLLMQueueStreamResult {
  data: LLMQueueStatusData | null;
  connected: boolean;
}

/**
 * SSE hook for real-time LLM queue status.
 * Opens an EventSource on mount, parses JSON events into typed state,
 * auto-reconnects with exponential backoff, and closes on unmount.
 */
export function useLLMQueueStream(): UseLLMQueueStreamResult {
  const [data, setData] = useState<LLMQueueStatusData | null>(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    function connect() {
      if (unmounted) return;

      const url = createQueueSSEUrl();
      es = new EventSource(url);

      es.onopen = () => {
        if (unmounted) return;
        setConnected(true);
        retryDelay.current = 1000; // Reset backoff on successful connect
      };

      es.onmessage = (event) => {
        if (unmounted) return;
        try {
          const parsed = JSON.parse(event.data) as LLMQueueStatusData;
          setData(parsed);
        } catch {
          // Ignore malformed events
        }
      };

      es.onerror = () => {
        if (unmounted) return;
        setConnected(false);
        es?.close();
        es = null;

        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        retryTimeout = setTimeout(() => {
          retryDelay.current = Math.min(retryDelay.current * 2, 30000);
          connect();
        }, retryDelay.current);
      };
    }

    connect();

    return () => {
      unmounted = true;
      es?.close();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, []);

  return { data, connected };
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit src/hooks/useLLMQueueStream.ts 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/hooks/useLLMQueueStream.ts
git commit -m "feat(llm-gateway): add useLLMQueueStream SSE hook"
```

---

### Task 7: Dashboard summary card `LLMQueueCard`

**Files:**
- Create: `donna-ui/src/pages/Dashboard/LLMQueueCard.tsx`
- Modify: `donna-ui/src/pages/Dashboard/index.tsx`
- Modify: `donna-ui/src/pages/Dashboard/Dashboard.module.css`
- Modify: `donna-ui/src/api/dashboard.ts`

- [ ] **Step 1: Create the card component**

Create `donna-ui/src/pages/Dashboard/LLMQueueCard.tsx`:

```tsx
import { Link } from "react-router-dom";
import { ChartCard, type ChartCardStat } from "../../charts";
import { Pill } from "../../primitives/Pill";
import type { LLMQueueStatusData } from "../../api/llmGateway";

interface Props {
  data: LLMQueueStatusData | null;
  loading: boolean;
}

function ModeIndicator({ mode }: { mode: "active" | "slow" }) {
  const color = mode === "active" ? "var(--color-success)" : "var(--color-warning)";
  const label = mode === "active" ? "Active" : "Slow";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: color,
          display: "inline-block",
        }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function RateLimitBar({ count, limit }: { count: number; limit: number }) {
  const pct = limit > 0 ? Math.min((count / limit) * 100, 100) : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
      }}
    >
      <div
        style={{
          flex: 1,
          height: 4,
          background: "var(--color-accent-soft)",
          borderRadius: 2,
          overflow: "hidden",
        }}
        role="progressbar"
        aria-valuenow={count}
        aria-valuemax={limit}
        aria-label="Rate limit usage"
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: pct > 80 ? "var(--color-warning)" : "var(--color-accent)",
            borderRadius: 2,
            transition: "width var(--duration-base) var(--ease-out)",
          }}
        />
      </div>
      <span
        style={{
          fontSize: "var(--text-label)",
          color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)",
          whiteSpace: "nowrap",
        }}
      >
        {count}/{limit}
      </span>
    </div>
  );
}

export default function LLMQueueCard({ data, loading }: Props) {
  const stats: ChartCardStat[] = [
    { label: "Internal Queue", value: data?.internal_queue.pending ?? 0 },
    { label: "External Queue", value: data?.external_queue.pending ?? 0 },
    {
      label: "Completed (24h)",
      value: data
        ? data.stats_24h.internal_completed + data.stats_24h.external_completed
        : 0,
    },
    {
      label: "Interrupted (24h)",
      value: data?.stats_24h.external_interrupted ?? 0,
    },
    {
      label: "Callers Active",
      value: data ? Object.keys(data.rate_limits).length : 0,
    },
  ];

  const rateLimitEntries = data ? Object.entries(data.rate_limits) : [];

  return (
    <ChartCard
      eyebrow="LLM Gateway · Live"
      metric={
        data ? <ModeIndicator mode={data.mode} /> : "—"
      }
      stats={stats}
      loading={loading && !data}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-4)",
        }}
      >
        {/* Current request */}
        <div>
          <div
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginBottom: "var(--space-2)",
            }}
          >
            Current Request
          </div>
          {data?.current_request ? (
            <div
              style={{
                background: "var(--color-surface)",
                borderRadius: 8,
                padding: "var(--space-3)",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-label)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>type</span>
                <span>{data.current_request.type}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>caller</span>
                <span>{data.current_request.caller ?? "—"}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>model</span>
                <span>{data.current_request.model}</span>
              </div>
            </div>
          ) : (
            <div
              style={{
                background: "var(--color-surface)",
                borderRadius: 8,
                padding: "var(--space-3)",
                fontSize: "var(--text-label)",
                color: "var(--color-text-muted)",
              }}
            >
              Idle
            </div>
          )}
        </div>

        {/* Rate limits */}
        <div>
          <div
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginBottom: "var(--space-2)",
            }}
          >
            Rate Limits
          </div>
          <div
            style={{
              background: "var(--color-surface)",
              borderRadius: 8,
              padding: "var(--space-3)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
            }}
          >
            {rateLimitEntries.length > 0 ? (
              rateLimitEntries.map(([caller, limits]) => (
                <div key={caller}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: "var(--space-1)",
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-label)",
                      }}
                    >
                      {caller}
                    </span>
                    <span
                      style={{
                        fontSize: "var(--text-label)",
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {limits.minute_count}/{limits.minute_limit} rpm
                    </span>
                  </div>
                  <RateLimitBar
                    count={limits.minute_count}
                    limit={limits.minute_limit}
                  />
                </div>
              ))
            ) : (
              <span
                style={{
                  fontSize: "var(--text-label)",
                  color: "var(--color-text-muted)",
                }}
              >
                No active callers
              </span>
            )}
          </div>
        </div>
      </div>

      {/* View full link */}
      <div style={{ marginTop: "var(--space-3)", textAlign: "right" }}>
        <Link
          to="/llm-gateway"
          style={{
            fontSize: "var(--text-label)",
            color: "var(--color-accent)",
            textDecoration: "none",
          }}
        >
          View full LLM Gateway →
        </Link>
      </div>
    </ChartCard>
  );
}
```

- [ ] **Step 2: Add `fetchLLMQueueStatus` to the dashboard data flow**

In `donna-ui/src/pages/Dashboard/index.tsx`:

Add import at top:
```tsx
import LLMQueueCard from "./LLMQueueCard";
import {
  fetchLLMQueueStatus,
  type LLMQueueStatusData,
} from "../../api/llmGateway";
```

Update `DashboardData` interface:
```tsx
export interface DashboardData {
  cost: CostAnalyticsData | null;
  parse: ParseAccuracyData | null;
  tasks: TaskThroughputData | null;
  agents: AgentPerformanceData | null;
  quality: QualityWarningsData | null;
  llmQueue: LLMQueueStatusData | null;
}
```

Update initial state:
```tsx
const [data, setData] = useState<DashboardData>({
  cost: null,
  parse: null,
  tasks: null,
  agents: null,
  quality: null,
  llmQueue: null,
});
```

Update `fetchAll` — add `fetchLLMQueueStatus` to the `Promise.all`:
```tsx
const [cost, parse, tasks, agents, quality, llmQueue] = await Promise.all([
  fetchCostAnalytics(d).catch(() => null),
  fetchParseAccuracy(d).catch(() => null),
  fetchTaskThroughput(d).catch(() => null),
  fetchAgentPerformance(d).catch(() => null),
  fetchQualityWarnings(d).catch(() => null),
  fetchLLMQueueStatus().catch(() => null),
]);

setData({ cost, parse, tasks, agents, quality, llmQueue });
```

Update the grid JSX — add after `CostAnalyticsCard` and before `ParseAccuracyCard`:
```tsx
<div className={styles.grid}>
  <div className={styles.fullWidth}>
    <CostAnalyticsCard data={data.cost} loading={loading} />
  </div>
  <div className={styles.fullWidth}>
    <LLMQueueCard data={data.llmQueue} loading={loading} />
  </div>
  <ParseAccuracyCard data={data.parse} loading={loading} />
  <TaskThroughputCard data={data.tasks} loading={loading} />
  <AgentPerformanceCard data={data.agents} loading={loading} />
  <QualityWarningsCard data={data.quality} loading={loading} />
</div>
```

- [ ] **Step 3: Update animation delays for 7 children**

In `donna-ui/src/pages/Dashboard/Dashboard.module.css`, add a 7th child delay and bump existing delays:

```css
.page[data-entered="true"] .grid > *:nth-child(1) { animation-delay: 0ms; }
.page[data-entered="true"] .grid > *:nth-child(2) { animation-delay: 50ms; }
.page[data-entered="true"] .grid > *:nth-child(3) { animation-delay: 100ms; }
.page[data-entered="true"] .grid > *:nth-child(4) { animation-delay: 150ms; }
.page[data-entered="true"] .grid > *:nth-child(5) { animation-delay: 200ms; }
.page[data-entered="true"] .grid > *:nth-child(6) { animation-delay: 250ms; }
.page[data-entered="true"] .grid > *:nth-child(7) { animation-delay: 300ms; }
```

- [ ] **Step 4: Verify it builds**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/pages/Dashboard/LLMQueueCard.tsx donna-ui/src/pages/Dashboard/index.tsx donna-ui/src/pages/Dashboard/Dashboard.module.css
git commit -m "feat(llm-gateway): add LLMQueueCard to main dashboard"
```

---

### Task 8: Dedicated LLM Gateway page

**Files:**
- Create: `donna-ui/src/pages/LLMGateway/index.tsx`
- Create: `donna-ui/src/pages/LLMGateway/LLMGateway.module.css`

- [ ] **Step 1: Create the page CSS**

Create `donna-ui/src/pages/LLMGateway/LLMGateway.module.css`:

```css
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.controls {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.liveStrip {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}

.detailSplit {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--space-4);
}

.queuePreview {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.previewItem {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border-radius: 6px;
  font-size: var(--text-label);
  cursor: pointer;
}

.previewItem:hover {
  background: var(--color-surface-raised);
}

.promptExpand {
  padding: var(--space-3);
  background: var(--color-surface);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: var(--text-label);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
}

.configField {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.configLabel {
  font-size: var(--text-label);
  color: var(--color-text-muted);
}

.configInput {
  width: 80px;
  padding: var(--space-1) var(--space-2);
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-size: var(--text-label);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 960px) {
  .liveStrip {
    grid-template-columns: 1fr;
  }
  .detailSplit {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Create the page component**

Create `donna-ui/src/pages/LLMGateway/index.tsx`:

```tsx
import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { type ColumnDef } from "@tanstack/react-table";
import RefreshButton from "../../components/RefreshButton";
import { PageHeader } from "../../primitives/PageHeader";
import { Segmented } from "../../primitives/Segmented";
import { Pill } from "../../primitives/Pill";
import { Card } from "../../primitives/Card";
import { Stat } from "../../primitives/Stat";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { Skeleton } from "../../primitives/Skeleton";
import { Tooltip } from "../../primitives/Tooltip";
import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import { fetchAdminHealth, type AdminHealthData } from "../../api/health";
import {
  fetchLLMGatewayAnalytics,
  fetchQueueItemPrompt,
  type LLMGatewayData,
  type LLMGatewayCallerEntry,
  type LLMQueueStatusData,
  type QueueItemPreview,
} from "../../api/llmGateway";
import { useLLMQueueStream } from "../../hooks/useLLMQueueStream";
import client from "../../api/client";
import styles from "./LLMGateway.module.css";

const RANGE_OPTIONS = [
  { label: "7d", value: "7" },
  { label: "14d", value: "14" },
  { label: "30d", value: "30" },
  { label: "90d", value: "90" },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function QueuePreviewItem({ item }: { item: QueueItemPreview }) {
  const [expanded, setExpanded] = useState(false);
  const [fullPrompt, setFullPrompt] = useState<string | null>(null);

  const handleClick = async () => {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (!fullPrompt) {
      try {
        const detail = await fetchQueueItemPrompt(item.sequence);
        setFullPrompt(detail.prompt);
      } catch {
        setFullPrompt("[Could not load prompt]");
      }
    }
  };

  return (
    <div>
      <div className={styles.previewItem} onClick={handleClick}>
        <span style={{ fontFamily: "var(--font-mono)" }}>
          {item.caller ?? item.task_type ?? "internal"}
        </span>
        <span style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          <span style={{ color: "var(--color-text-muted)" }}>{item.model}</span>
          <span style={{ color: "var(--color-text-muted)" }}>{timeAgo(item.enqueued_at)}</span>
        </span>
      </div>
      {expanded && (
        <div className={styles.promptExpand}>
          {fullPrompt ?? item.prompt_preview}
        </div>
      )}
    </div>
  );
}

const callerColumns: ColumnDef<LLMGatewayCallerEntry>[] = [
  {
    accessorKey: "caller",
    header: "Caller",
    cell: ({ getValue }) => (
      <span style={{ fontFamily: "var(--font-mono)" }}>{getValue<string>()}</span>
    ),
  },
  {
    accessorKey: "call_count",
    header: "Calls",
    cell: ({ getValue }) => getValue<number>().toLocaleString(),
  },
  {
    accessorKey: "avg_latency_ms",
    header: "Avg Latency",
    cell: ({ getValue }) => `${getValue<number>().toLocaleString()}ms`,
  },
  {
    accessorKey: "total_tokens_in",
    header: "Tokens In",
    cell: ({ getValue }) => {
      const v = getValue<number>();
      return v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toString();
    },
  },
  {
    accessorKey: "interrupted_count",
    header: "Interrupted",
    cell: ({ getValue }) => {
      const v = getValue<number>();
      return v > 0 ? (
        <span style={{ color: "var(--color-warning)" }}>{v}</span>
      ) : (
        "0"
      );
    },
  },
];

export default function LLMGateway() {
  const [range, setRange] = useState<RangeValue>("7");
  const days = Number(range);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const [analytics, setAnalytics] = useState<LLMGatewayData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(true);

  // Live data via SSE
  const { data: liveData, connected } = useLLMQueueStream();

  // Config state
  const [configRpm, setConfigRpm] = useState("");
  const [configRph, setConfigRph] = useState("");
  const [configDepth, setConfigDepth] = useState("");
  const [configSaving, setConfigSaving] = useState(false);

  // Expanded current request prompt
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [currentExpanded, setCurrentExpanded] = useState(false);

  const fetchAnalytics = useCallback(async (d: number) => {
    setAnalyticsLoading(true);
    try {
      const data = await fetchLLMGatewayAnalytics(d);
      setAnalytics(data);
    } catch {
      // Error toast handled by client interceptor
    } finally {
      setAnalyticsLoading(false);
    }
  }, []);

  const refreshHealth = useCallback(() => {
    fetchAdminHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Load config defaults
  useEffect(() => {
    client
      .get("/admin/configs/llm_gateway.yaml")
      .then(({ data }) => {
        // Parse YAML values for the quick config fields
        const content = data.content as string;
        const rpmMatch = content.match(/requests_per_minute:\s*(\d+)/);
        const rphMatch = content.match(/requests_per_hour:\s*(\d+)/);
        const depthMatch = content.match(/max_external_depth:\s*(\d+)/);
        if (rpmMatch) setConfigRpm(rpmMatch[1]);
        if (rphMatch) setConfigRph(rphMatch[1]);
        if (depthMatch) setConfigDepth(depthMatch[1]);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchAnalytics(days);
    refreshHealth();
  }, [days, fetchAnalytics, refreshHealth]);

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await fetchAnalytics(days);
  }, [days, fetchAnalytics, refreshHealth]);

  const handleConfigSave = async () => {
    setConfigSaving(true);
    try {
      // Read current config
      const { data: current } = await client.get("/admin/configs/llm_gateway.yaml");
      let content = current.content as string;

      // Replace values
      if (configRpm) {
        content = content.replace(
          /requests_per_minute:\s*\d+/,
          `requests_per_minute: ${configRpm}`,
        );
      }
      if (configRph) {
        content = content.replace(
          /requests_per_hour:\s*\d+/,
          `requests_per_hour: ${configRph}`,
        );
      }
      if (configDepth) {
        content = content.replace(
          /max_external_depth:\s*\d+/,
          `max_external_depth: ${configDepth}`,
        );
      }

      await client.put("/admin/configs/llm_gateway.yaml", { content });
      toast.success("Gateway config saved and reloaded");
    } catch {
      toast.error("Failed to save config");
    } finally {
      setConfigSaving(false);
    }
  };

  const handleCurrentRequestClick = async () => {
    if (!liveData?.current_request) return;
    if (currentExpanded) {
      setCurrentExpanded(false);
      return;
    }
    setCurrentExpanded(true);
    if (!currentPrompt) {
      try {
        const detail = await fetchQueueItemPrompt(liveData.current_request.sequence);
        setCurrentPrompt(detail.prompt);
      } catch {
        setCurrentPrompt("[Could not load prompt]");
      }
    }
  };

  // Reset expanded prompt when current request changes
  const prevSeq = useRef<number | null>(null);
  useEffect(() => {
    const seq = liveData?.current_request?.sequence ?? null;
    if (seq !== prevSeq.current) {
      setCurrentPrompt(null);
      setCurrentExpanded(false);
      prevSeq.current = seq;
    }
  }, [liveData?.current_request?.sequence]);

  const healthVariant =
    health?.status === "healthy" ? "success" : health ? "warning" : "muted";
  const healthLabel =
    health?.status === "healthy" ? "Healthy" : health ? "Degraded" : "—";

  const s = analytics?.summary;
  const chartStats: ChartCardStat[] = [
    { label: "Total", value: s?.total_calls.toLocaleString() ?? "—" },
    { label: "Internal", value: s?.internal_calls.toLocaleString() ?? "—" },
    { label: "External", value: s?.external_calls.toLocaleString() ?? "—" },
    { label: "Interrupted", value: s?.total_interrupted.toLocaleString() ?? "—" },
    { label: "Avg Latency", value: s ? `${s.avg_latency_ms.toLocaleString()}ms` : "—" },
    { label: "Callers", value: s?.unique_callers.toLocaleString() ?? "—" },
  ];

  const allNextItems = [
    ...(liveData?.internal_queue.next_items ?? []),
    ...(liveData?.external_queue.next_items ?? []),
  ];

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow="Infrastructure"
        title="LLM Gateway"
        actions={
          <div className={styles.controls}>
            <Segmented
              value={range}
              onValueChange={(v) => setRange(v as RangeValue)}
              options={RANGE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              aria-label="Date range"
            />
            {health && (
              <Tooltip content={`Ollama: ${health.checks?.ollama?.ok ? "OK" : "down"}`}>
                <span role="status">
                  <Pill variant={healthVariant}>{healthLabel}</Pill>
                </span>
              </Tooltip>
            )}
            <Pill variant={connected ? "success" : "muted"}>
              {connected ? "SSE" : "Disconnected"}
            </Pill>
            <RefreshButton onRefresh={handleRefresh} />
          </div>
        }
      />

      {/* Row 1: Live Status Strip */}
      <div className={styles.liveStrip}>
        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <Stat
              eyebrow="Queue Status"
              value={liveData?.mode === "active" ? "Active" : liveData?.mode === "slow" ? "Slow" : "—"}
              sub={
                liveData && (
                  <span style={{ fontSize: "var(--text-label)", color: "var(--color-text-muted)" }}>
                    Internal {liveData.internal_queue.pending} · External{" "}
                    {liveData.external_queue.pending}
                  </span>
                )
              }
              plain
            />
          </div>
        </Card>

        <Card>
          <div
            style={{ padding: "var(--space-4)", cursor: liveData?.current_request ? "pointer" : "default" }}
            onClick={handleCurrentRequestClick}
          >
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-2)",
              }}
            >
              Current Request
            </div>
            {liveData?.current_request ? (
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-label)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>type</span>
                  <span>{liveData.current_request.type}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>caller</span>
                  <span>{liveData.current_request.caller ?? "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>model</span>
                  <span>{liveData.current_request.model}</span>
                </div>
              </div>
            ) : (
              <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-label)" }}>
                Idle
              </span>
            )}
            {currentExpanded && (
              <div className={styles.promptExpand} style={{ marginTop: "var(--space-2)" }}>
                {currentPrompt ?? liveData?.current_request?.prompt_preview ?? ""}
              </div>
            )}
          </div>
        </Card>

        <Card>
          <div
            style={{
              padding: "var(--space-4)",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-3)",
            }}
          >
            <Stat
              eyebrow="Internal"
              value={liveData?.stats_24h.internal_completed ?? 0}
              plain
            />
            <Stat
              eyebrow="External"
              value={liveData?.stats_24h.external_completed ?? 0}
              plain
            />
            <Stat
              eyebrow="Interrupted"
              value={liveData?.stats_24h.external_interrupted ?? 0}
              plain
            />
            <Stat eyebrow="Rejected" value={0} plain />
          </div>
        </Card>
      </div>

      {/* Queue Preview */}
      {allNextItems.length > 0 && (
        <div className={styles.queuePreview}>
          <div
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
            }}
          >
            Next in Queue
          </div>
          {allNextItems.map((item) => (
            <QueuePreviewItem key={item.sequence} item={item} />
          ))}
        </div>
      )}

      {/* Row 2: Historical Chart */}
      <ChartCard
        eyebrow={`Gateway Throughput · ${days} days`}
        metric={s?.total_calls.toLocaleString() ?? "—"}
        metricSuffix="calls"
        chart={
          analytics?.time_series && analytics.time_series.length > 0 ? (
            <BarChart
              data={analytics.time_series}
              series={[
                { dataKey: "internal", name: "Internal" },
                { dataKey: "external", name: "External", tone: "accentSoft" },
              ]}
              categoryKey="date"
              orientation="horizontal"
              formatCategoryTick={(v) => v.slice(5)}
              ariaLabel={`Gateway throughput over ${days} days`}
            />
          ) : undefined
        }
        stats={chartStats}
        loading={analyticsLoading && !analytics}
      />

      {/* Row 3: Caller Table + Config */}
      <div className={styles.detailSplit}>
        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-3)",
              }}
            >
              Per-Caller Breakdown
            </div>
            {analyticsLoading && !analytics ? (
              <Skeleton height={200} />
            ) : (
              <DataTable
                data={analytics?.by_caller ?? []}
                columns={callerColumns}
                getRowId={(row) => row.caller}
                emptyState={
                  <span style={{ color: "var(--color-text-muted)" }}>
                    No external callers in this period
                  </span>
                }
              />
            )}
          </div>
        </Card>

        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-3)",
              }}
            >
              Quick Config
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Default RPM</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configRpm}
                  onChange={(e) => setConfigRpm(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Default RPH</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configRph}
                  onChange={(e) => setConfigRph(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Max Queue Depth</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configDepth}
                  onChange={(e) => setConfigDepth(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <span className={styles.configLabel}>Active Hours</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-label)" }}>
                  {liveData?.mode === "active" ? "In active hours" : "Outside active hours"}
                </span>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={handleConfigSave}
                disabled={configSaving}
              >
                {configSaving ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify it compiles**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/LLMGateway/index.tsx donna-ui/src/pages/LLMGateway/LLMGateway.module.css
git commit -m "feat(llm-gateway): add dedicated LLM Gateway page"
```

---

### Task 9: Add route and sidebar entry

**Files:**
- Modify: `donna-ui/src/App.tsx`
- Modify: `donna-ui/src/layout/Sidebar.tsx`

- [ ] **Step 1: Add route to App.tsx**

In `donna-ui/src/App.tsx`, add import:

```tsx
import LLMGatewayPage from "./pages/LLMGateway";
```

Add route inside the `<Route element={<AppShell />}>` block, after the preferences route:

```tsx
<Route path="/llm-gateway" element={<ErrorBoundary><LLMGatewayPage /></ErrorBoundary>} />
```

- [ ] **Step 2: Add sidebar entry**

In `donna-ui/src/layout/Sidebar.tsx`, add import:

```tsx
import {
  LayoutDashboard,
  ScrollText,
  CheckSquare,
  Bot,
  Settings,
  FileText,
  FlaskConical,
  Lightbulb,
  Radio,
} from "lucide-react";
```

Add to `NAV_ITEMS` array, after the Preferences entry:

```tsx
{ path: "/llm-gateway", label: "LLM Gateway", icon: <Radio size={18} /> },
```

- [ ] **Step 3: Verify it builds**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/App.tsx donna-ui/src/layout/Sidebar.tsx
git commit -m "feat(llm-gateway): add route and sidebar entry"
```

---

### Task 10: Update E2E test helpers and smoke tests

**Files:**
- Modify: `donna-ui/tests/e2e/helpers.ts`
- Modify: `donna-ui/tests/e2e/smoke/dashboard.spec.ts`
- Modify: `donna-ui/tests/e2e/smoke/app-shell.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/llm-gateway.spec.ts`

- [ ] **Step 1: Add mock for `/llm/queue/status` to helpers**

In `donna-ui/tests/e2e/helpers.ts`, add a new route handler **before** the default handler at the end. Add it after the `mockAdminApi` function opens its `page.route` block, alongside the other route matchers:

```typescript
// Also mock /llm/** endpoints for the dashboard LLM queue card
await page.route("**/llm/**", (route) => {
  const url = route.request().url();

  if (url.match(/\/llm\/queue\/status/)) {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        current_request: null,
        internal_queue: { pending: 0, next_items: [] },
        external_queue: { pending: 0, next_items: [] },
        stats_24h: {
          internal_completed: 12,
          external_completed: 5,
          external_interrupted: 1,
        },
        rate_limits: {},
        mode: "active",
      }),
    });
  }

  if (url.match(/\/llm\/queue\/stream/)) {
    // SSE: return a simple initial event then hang
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'data: {"current_request":null,"internal_queue":{"pending":0,"next_items":[]},"external_queue":{"pending":0,"next_items":[]},"stats_24h":{"internal_completed":12,"external_completed":5,"external_interrupted":1},"rate_limits":{},"mode":"active"}\n\n',
    });
  }

  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "{}",
  });
});
```

- [ ] **Step 2: Update dashboard smoke test card count**

In `donna-ui/tests/e2e/smoke/dashboard.spec.ts`, update the assertion:

Change:
```typescript
await expect(gridChildren).toHaveCount(5);
```

To:
```typescript
await expect(gridChildren).toHaveCount(7);
```

The count is 7 because: CostAnalyticsCard wrapper (fullWidth div), LLMQueueCard wrapper (fullWidth div), ParseAccuracyCard, TaskThroughputCard, AgentPerformanceCard, QualityWarningsCard = 6 cards but CostAnalytics and LLMQueue each have a fullWidth wrapper div, making 7 direct children of the grid (2 fullWidth wrappers + 4 regular cards... wait, let me recheck).

Actually looking at the grid structure in Task 7, the grid has:
1. `<div className={styles.fullWidth}>` (wraps CostAnalyticsCard)
2. `<div className={styles.fullWidth}>` (wraps LLMQueueCard)
3. `<ParseAccuracyCard />`
4. `<TaskThroughputCard />`
5. `<AgentPerformanceCard />`
6. `<QualityWarningsCard />`

So that's 6 direct children:

```typescript
await expect(gridChildren).toHaveCount(6);
```

- [ ] **Step 3: Update app-shell test to include LLM Gateway nav item**

In `donna-ui/tests/e2e/smoke/app-shell.spec.ts`, no changes needed — the test checks for "Dashboard", "Tasks", and "Preferences" by name, not by count.

- [ ] **Step 4: Create LLM Gateway smoke test**

Create `donna-ui/tests/e2e/smoke/llm-gateway.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("LLM Gateway smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders page header and live status strip", async ({ page }) => {
    await page.goto("/llm-gateway");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "LLM Gateway" })).toBeVisible();

    // Range selector present
    const rangeTabs = page.locator('[role="tablist"] [role="tab"]');
    await expect(rangeTabs).toHaveCount(4);
  });

  test("sidebar nav item is active on /llm-gateway", async ({ page }) => {
    await page.goto("/llm-gateway");
    const link = page.getByRole("link", { name: "LLM Gateway" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("aria-current", "page");
  });

  test("no AntD class names on the page", async ({ page }) => {
    await page.goto("/llm-gateway");
    await page.waitForLoadState("networkidle");

    const antdCount = await page.locator('[class*="ant-"]').count();
    expect(antdCount).toBe(0);
  });
});
```

- [ ] **Step 5: Run smoke tests**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx playwright test tests/e2e/smoke/dashboard.spec.ts tests/e2e/smoke/llm-gateway.spec.ts tests/e2e/smoke/app-shell.spec.ts --reporter=line 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add donna-ui/tests/e2e/helpers.ts donna-ui/tests/e2e/smoke/dashboard.spec.ts donna-ui/tests/e2e/smoke/llm-gateway.spec.ts
git commit -m "test(llm-gateway): update E2E helpers and add smoke tests"
```

---

### Task 11: Run full test suite and fix lint

**Files:**
- All modified files

- [ ] **Step 1: Run backend tests**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -30`
Expected: All tests PASS (3 pre-existing failures may appear — `test_supabase_sync`, `test_weekly_planner` x2 — these are not related to our changes)

- [ ] **Step 2: Run linter on backend**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences && python -m ruff check src/donna/llm/ src/donna/api/routes/llm.py src/donna/api/routes/admin_dashboard.py --fix 2>&1`
Expected: No errors (or auto-fixed)

- [ ] **Step 3: Run frontend type check**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx tsc --noEmit 2>&1 | head -30`
Expected: No errors

- [ ] **Step 4: Run frontend lint**

Run: `cd /home/feuer/Documents/Projects/donna/.claude/worktrees/wave-8-shadow-preferences/donna-ui && npx eslint src/ --ext .ts,.tsx 2>&1 | tail -20`
Expected: No errors

- [ ] **Step 5: Fix any issues and commit**

```bash
git add -A
git commit -m "fix(llm-gateway): lint and type fixes"
```
