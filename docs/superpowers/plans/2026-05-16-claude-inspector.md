# Claude Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a forensics tool for inspecting and optimizing Claude API usage — full request/response capture, a call browser, and proactive insights that surface waste patterns.

**Architecture:** Payload files written to disk on every `complete()` call (fire-and-forget). FIFO evictor keeps total under 1GB. Three new API endpoints serve the call browser, payload detail, and computed insights. A new React page ("Claude Inspector") provides the UI.

**Tech Stack:** Python 3.12 / asyncio / aiosqlite / FastAPI (backend); React 18 / TypeScript / TanStack Table / CSS Modules (frontend)

---

## File Structure

```
src/donna/
  collection/
    __init__.py                         # Package init
    payload_writer.py                   # Async fire-and-forget file writer + size tracker
    payload_evictor.py                  # FIFO eviction (delete oldest dirs until under budget)
  insights/
    __init__.py                         # Package init
    engine.py                           # Insights computation with TTL cache
  api/routes/
    admin_claude.py                     # /admin/claude/* endpoints

alembic/versions/
    add_payload_path_to_invocation_log.py  # Migration: payload_path column

tests/unit/
    test_payload_writer.py
    test_payload_evictor.py
    test_insights_engine.py
    test_admin_claude.py

donna-ui/src/
  pages/ClaudeInspector/
    index.tsx                           # Page layout (insights + browser)
    InsightsPanel.tsx                   # Top optimization cards
    CallBrowser.tsx                     # Filterable table
    CallDetail.tsx                      # Expanded row with full payload
    CallCompare.tsx                     # Side-by-side diff of two calls
    claude-inspector.module.css
  api/
    claude.ts                           # API client functions
```

---

### Task 1: Payload Writer

**Files:**
- Create: `src/donna/collection/__init__.py`
- Create: `src/donna/collection/payload_writer.py`
- Test: `tests/unit/test_payload_writer.py`

- [ ] **Step 1: Write failing test for payload write**

```python
# tests/unit/test_payload_writer.py
"""Unit tests for PayloadWriter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from donna.collection.payload_writer import PayloadWriter


@pytest.fixture
def payload_dir(tmp_path: Path) -> Path:
    return tmp_path / "payloads"


@pytest.fixture
def writer(payload_dir: Path) -> PayloadWriter:
    return PayloadWriter(base_dir=payload_dir, max_bytes=1_073_741_824)


@pytest.mark.asyncio
async def test_write_creates_file(writer: PayloadWriter, payload_dir: Path) -> None:
    request_data = {
        "messages": [{"role": "user", "content": "hello"}],
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
    }
    response_data = {
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    }

    rel_path = await writer.write(
        invocation_id="inv-001",
        request=request_data,
        response=response_data,
    )

    assert rel_path is not None
    full_path = payload_dir / rel_path
    assert full_path.exists()
    data = json.loads(full_path.read_text())
    assert data["request"]["model"] == "claude-sonnet-4-20250514"
    assert data["response"]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_write_returns_none_on_failure(tmp_path: Path) -> None:
    # Point at a read-only path to force failure
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o444)

    writer = PayloadWriter(base_dir=ro_dir, max_bytes=1_073_741_824)
    result = await writer.write(
        invocation_id="inv-fail",
        request={"messages": []},
        response={"content": []},
    )
    assert result is None
    # Restore permissions for cleanup
    ro_dir.chmod(0o755)


@pytest.mark.asyncio
async def test_size_tracking_increments(writer: PayloadWriter, payload_dir: Path) -> None:
    assert writer.current_bytes == 0

    await writer.write(
        invocation_id="inv-size",
        request={"messages": [{"role": "user", "content": "x" * 1000}]},
        response={"content": [{"type": "text", "text": "y" * 500}]},
    )

    assert writer.current_bytes > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_payload_writer.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'donna.collection'"

- [ ] **Step 3: Write the package init and PayloadWriter**

```python
# src/donna/collection/__init__.py
"""Payload collection subsystem for Claude Inspector."""
```

```python
# src/donna/collection/payload_writer.py
"""Fire-and-forget payload file writer for LLM call inspection.

Writes full request/response JSON to disk, organized by date.
Maintains an in-memory size estimate for eviction triggering.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class PayloadWriter:
    """Writes LLM payloads to date-partitioned directories on disk."""

    def __init__(self, base_dir: Path, max_bytes: int = 1_073_741_824) -> None:
        self._base_dir = base_dir
        self._max_bytes = max_bytes
        self._current_bytes = 0

    @property
    def current_bytes(self) -> int:
        return self._current_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def sync_size_from_disk(self) -> None:
        """Recalculate current_bytes by walking the base directory."""
        total = 0
        if self._base_dir.exists():
            for f in self._base_dir.rglob("*.json"):
                total += f.stat().st_size
        self._current_bytes = total

    async def write(
        self,
        invocation_id: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> str | None:
        """Write a payload file. Returns relative path on success, None on failure."""
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        rel_path = f"{date_str}/{invocation_id}.json"

        try:
            day_dir = self._base_dir / date_str
            day_dir.mkdir(parents=True, exist_ok=True)

            payload = {"request": request, "response": response}
            content = json.dumps(payload, separators=(",", ":"))

            file_path = self._base_dir / rel_path
            file_path.write_text(content)

            self._current_bytes += len(content.encode())

            return rel_path
        except OSError:
            logger.warning("payload_write_failed", invocation_id=invocation_id)
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_payload_writer.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/collection/__init__.py src/donna/collection/payload_writer.py tests/unit/test_payload_writer.py
git commit -m "feat(collection): add PayloadWriter for Claude Inspector"
```

---

### Task 2: Payload Evictor

**Files:**
- Create: `src/donna/collection/payload_evictor.py`
- Test: `tests/unit/test_payload_evictor.py`

- [ ] **Step 1: Write failing test for eviction**

```python
# tests/unit/test_payload_evictor.py
"""Unit tests for PayloadEvictor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from donna.collection.payload_evictor import PayloadEvictor
from donna.collection.payload_writer import PayloadWriter


@pytest.fixture
def payload_dir(tmp_path: Path) -> Path:
    d = tmp_path / "payloads"
    d.mkdir()
    return d


def _populate_day(payload_dir: Path, date: str, count: int, size_each: int) -> None:
    """Create count JSON files of approximately size_each bytes in a date dir."""
    day_dir = payload_dir / date
    day_dir.mkdir(exist_ok=True)
    content = json.dumps({"data": "x" * size_each})
    for i in range(count):
        (day_dir / f"inv-{date}-{i:03d}.json").write_text(content)


@pytest.mark.asyncio
async def test_evict_deletes_oldest_dirs_first(payload_dir: Path) -> None:
    # Create 3 days of data, ~500 bytes each file, 10 files per day
    _populate_day(payload_dir, "2026-05-01", 10, 500)
    _populate_day(payload_dir, "2026-05-02", 10, 500)
    _populate_day(payload_dir, "2026-05-03", 10, 500)

    # Total is ~15KB. Set max to 10KB so oldest day gets evicted.
    writer = PayloadWriter(base_dir=payload_dir, max_bytes=10_000)
    writer.sync_size_from_disk()

    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()

    evictor = PayloadEvictor(writer=writer, conn=conn, target_pct=0.6)
    evicted_dates = await evictor.evict()

    assert "2026-05-01" in evicted_dates
    assert not (payload_dir / "2026-05-01").exists()
    assert (payload_dir / "2026-05-03").exists()


@pytest.mark.asyncio
async def test_evict_noop_under_budget(payload_dir: Path) -> None:
    _populate_day(payload_dir, "2026-05-10", 2, 100)

    writer = PayloadWriter(base_dir=payload_dir, max_bytes=1_073_741_824)
    writer.sync_size_from_disk()

    conn = AsyncMock()
    evictor = PayloadEvictor(writer=writer, conn=conn)
    evicted_dates = await evictor.evict()

    assert evicted_dates == []
    assert (payload_dir / "2026-05-10").exists()


@pytest.mark.asyncio
async def test_evict_nulls_payload_path_in_db(payload_dir: Path) -> None:
    _populate_day(payload_dir, "2026-05-01", 5, 400)
    _populate_day(payload_dir, "2026-05-02", 5, 400)

    writer = PayloadWriter(base_dir=payload_dir, max_bytes=3_000)
    writer.sync_size_from_disk()

    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()

    evictor = PayloadEvictor(writer=writer, conn=conn, target_pct=0.5)
    await evictor.evict()

    # Verify SQL was called to null out payload_path for evicted dates
    calls = conn.execute.call_args_list
    update_calls = [c for c in calls if "UPDATE" in str(c)]
    assert len(update_calls) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_payload_evictor.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'donna.collection.payload_evictor'"

- [ ] **Step 3: Write PayloadEvictor**

```python
# src/donna/collection/payload_evictor.py
"""FIFO eviction for payload storage.

Deletes oldest date directories when total size exceeds budget.
Updates invocation_log to null out evicted payload_path values.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import aiosqlite
import structlog

from donna.collection.payload_writer import PayloadWriter

logger = structlog.get_logger()


class PayloadEvictor:
    """Evicts oldest payload directories to keep storage under budget."""

    def __init__(
        self,
        writer: PayloadWriter,
        conn: aiosqlite.Connection,
        target_pct: float = 0.9,
    ) -> None:
        self._writer = writer
        self._conn = conn
        self._target_bytes = int(writer.max_bytes * target_pct)

    async def evict(self) -> list[str]:
        """Run eviction if over budget. Returns list of evicted date strings."""
        if self._writer.current_bytes <= self._writer.max_bytes:
            return []

        base_dir = self._writer._base_dir
        date_dirs = sorted(
            [d for d in base_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )

        evicted: list[str] = []
        for day_dir in date_dirs:
            if self._writer.current_bytes <= self._target_bytes:
                break

            dir_size = sum(f.stat().st_size for f in day_dir.rglob("*") if f.is_file())
            date_str = day_dir.name

            shutil.rmtree(day_dir)
            self._writer._current_bytes -= dir_size
            evicted.append(date_str)

            logger.info("payload_evicted", date=date_str, freed_bytes=dir_size)

        if evicted:
            for date_str in evicted:
                await self._conn.execute(
                    "UPDATE invocation_log SET payload_path = NULL WHERE payload_path LIKE ?",
                    (f"{date_str}/%",),
                )
            await self._conn.commit()

        return evicted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_payload_evictor.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/collection/payload_evictor.py tests/unit/test_payload_evictor.py
git commit -m "feat(collection): add PayloadEvictor with FIFO 1GB cap"
```

---

### Task 3: Alembic Migration — payload_path column

**Files:**
- Create: `alembic/versions/add_payload_path_to_invocation_log.py`

- [ ] **Step 1: Create the migration file**

```python
# alembic/versions/add_payload_path_to_invocation_log.py
"""add payload_path to invocation_log

Revision ID: a1b2c3d4e5f6
Revises: <LOOK UP CURRENT HEAD with `alembic heads`>
Create Date: 2026-05-16 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None  # SET from `alembic heads` output
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("payload_path", sa.String(300), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("payload_path")
```

- [ ] **Step 2: Look up current Alembic head and set down_revision**

Run: `alembic heads`
Update the `down_revision` in the migration file to match the current head.

- [ ] **Step 3: Run the migration**

Run: `alembic upgrade head`
Expected: Migration applies cleanly.

- [ ] **Step 4: Verify column exists**

Run: `python -c "import sqlite3; c=sqlite3.connect('donna_tasks.db'); print([col[1] for col in c.execute('PRAGMA table_info(invocation_log)').fetchall() if col[1]=='payload_path'])"`
Expected: `['payload_path']`

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/add_payload_path_to_invocation_log.py
git commit -m "migration: add payload_path column to invocation_log"
```

---

### Task 4: Integrate PayloadWriter into ModelRouter

**Files:**
- Modify: `src/donna/models/router.py:526-549` (invocation logging block)
- Modify: `src/donna/logging/invocation_logger.py:56-110` (capture returned ID)

- [ ] **Step 1: Update InvocationLogger.log() — it already returns the ID, just need to capture it in the router**

In `src/donna/models/router.py`, the invocation logging block (lines 526-549) currently does:

```python
await self._invocation_logger.log(InvocationMetadata(...))
```

Change to capture the ID:

```python
invocation_id = await self._invocation_logger.log(InvocationMetadata(...))
```

- [ ] **Step 2: Add PayloadWriter as an optional dependency to ModelRouter**

At the top of `src/donna/models/router.py`, after existing imports, the `__init__` signature needs a new optional parameter:

```python
from donna.collection.payload_writer import PayloadWriter
```

In `ModelRouter.__init__`, add:
```python
self._payload_writer: PayloadWriter | None = payload_writer
```

- [ ] **Step 3: Add payload write after invocation log**

After the invocation logging try/except block (after line 549), add:

```python
# Write full request/response payload for Claude Inspector.
if self._payload_writer is not None and invocation_id is not None:
    request_payload = {
        "messages": messages or [{"role": "user", "content": prompt}],
        "model": model_id,
        "tools": tools,
        "max_tokens": call_kwargs.get("max_tokens"),
    }
    response_payload = {
        "content": result,
        "usage": {
            "input_tokens": enriched_metadata.tokens_in,
            "output_tokens": enriched_metadata.tokens_out,
        },
        "stop_reason": "end_turn",
        "model_actual": enriched_metadata.model_actual,
    }

    import hashlib
    system_text = ""
    if messages:
        system_msgs = [m.get("content", "") for m in messages if m.get("role") == "system"]
        system_text = "\n".join(system_msgs) if system_msgs else prompt
    else:
        system_text = prompt
    input_hash = hashlib.sha256(system_text.encode()).hexdigest()[:16]

    try:
        rel_path = await self._payload_writer.write(
            invocation_id=invocation_id,
            request=request_payload,
            response=response_payload,
        )
        if rel_path:
            await self._conn.execute(
                "UPDATE invocation_log SET payload_path = ?, input_hash = ? WHERE id = ?",
                (rel_path, input_hash, invocation_id),
            )
            await self._conn.commit()
    except Exception:
        logger.warning("payload_write_integration_failed", task_type=task_type)
```

- [ ] **Step 4: Run existing router tests to verify no regressions**

Run: `pytest tests/unit/models/ -v`
Expected: All existing tests PASS (PayloadWriter is optional/None by default)

- [ ] **Step 5: Commit**

```bash
git add src/donna/models/router.py
git commit -m "feat: integrate PayloadWriter into ModelRouter complete() path"
```

---

### Task 5: API Endpoints — admin_claude.py

**Files:**
- Create: `src/donna/api/routes/admin_claude.py`
- Modify: `src/donna/api/__init__.py:420` (mount the new router)
- Test: `tests/unit/test_admin_claude.py`

- [ ] **Step 1: Write failing tests for the three endpoints**

```python
# tests/unit/test_admin_claude.py
"""Unit tests for the Claude Inspector endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from donna.api.routes.admin_claude import get_calls, get_payload, get_insights


def _cursor(fetchall=None, fetchone=None):
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_request(conn, payload_dir=None):
    req = AsyncMock()
    req.app.state.db.connection = conn
    req.app.state.payload_dir = payload_dir
    return req


@pytest.mark.asyncio
async def test_get_calls_returns_paginated_list() -> None:
    rows = [
        (
            "inv-001", "2026-05-16T10:00:00", "parse_task", "task-1",
            "sonnet", "anthropic/claude-sonnet-4-20250514",
            500, 1000, 200, 0.006, 0.9, 0, "nick",
            1200, 0, "2026-05-16/inv-001.json",
        ),
    ]
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=[
        _cursor(fetchone=(1,)),  # COUNT
        _cursor(fetchall=rows),  # SELECT
    ])

    request = _make_request(conn, Path("/tmp/payloads"))
    result = await get_calls(
        request=request,
        task_type=None, model=None, date_from=None, date_to=None,
        min_cost=None, min_tokens_in=None, quality_score_below=None,
        sort="timestamp", sort_dir="desc", limit=50, offset=0,
    )

    assert result["total"] == 1
    assert result["calls"][0]["id"] == "inv-001"
    assert result["calls"][0]["has_payload"] is True


@pytest.mark.asyncio
async def test_get_payload_returns_file_contents(tmp_path: Path) -> None:
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    day_dir = payload_dir / "2026-05-16"
    day_dir.mkdir()
    payload = {"request": {"messages": []}, "response": {"content": []}}
    (day_dir / "inv-001.json").write_text(json.dumps(payload))

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=_cursor(fetchone=("2026-05-16/inv-001.json",)))

    request = _make_request(conn, payload_dir)
    result = await get_payload(request=request, invocation_id="inv-001")

    assert result["request"]["messages"] == []
    assert result["response"]["content"] == []


@pytest.mark.asyncio
async def test_get_payload_404_when_evicted(tmp_path: Path) -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=_cursor(fetchone=(None,)))

    request = _make_request(conn, tmp_path)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await get_payload(request=request, invocation_id="inv-gone")
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_admin_claude.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write admin_claude.py**

```python
# src/donna/api/routes/admin_claude.py
"""Claude Inspector endpoints for call browsing, payload retrieval, and insights.

Provides the API layer for the Claude Inspector dashboard page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router

router = admin_router()

_SORTABLE_COLUMNS = {
    "timestamp": "timestamp",
    "cost": "cost_usd",
    "tokens_in": "tokens_in",
    "tokens_out": "tokens_out",
    "latency": "latency_ms",
}


@router.get("/claude/calls")
async def get_calls(
    request: Request,
    task_type: str | None = Query(default=None),
    model: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    min_cost: float | None = Query(default=None),
    min_tokens_in: int | None = Query(default=None),
    quality_score_below: float | None = Query(default=None),
    sort: str = Query(default="timestamp"),
    sort_dir: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated call browser with filters."""
    conn = request.app.state.db.connection
    payload_dir: Path | None = getattr(request.app.state, "payload_dir", None)

    where_clauses: list[str] = []
    params: list[Any] = []

    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)
    if model:
        where_clauses.append("model_alias = ?")
        params.append(model)
    if date_from:
        where_clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("timestamp <= ?")
        params.append(date_to)
    if min_cost is not None:
        where_clauses.append("cost_usd >= ?")
        params.append(min_cost)
    if min_tokens_in is not None:
        where_clauses.append("tokens_in >= ?")
        params.append(min_tokens_in)
    if quality_score_below is not None:
        where_clauses.append("quality_score IS NOT NULL AND quality_score < ?")
        params.append(quality_score_below)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    order_col = _SORTABLE_COLUMNS.get(sort, "timestamp")
    direction = "ASC" if sort_dir == "asc" else "DESC"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM invocation_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, timestamp, task_type, task_id, model_alias, model_actual,
                   latency_ms, tokens_in, tokens_out, cost_usd, quality_score,
                   is_shadow, user_id, estimated_tokens_in, overflow_escalated,
                   payload_path
            FROM invocation_log
            WHERE {where}
            ORDER BY {order_col} {direction}
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()

    calls = []
    for row in rows:
        payload_path = row[15]
        has_payload = False
        if payload_path and payload_dir:
            has_payload = (payload_dir / payload_path).exists()

        calls.append({
            "id": row[0],
            "timestamp": row[1],
            "task_type": row[2],
            "task_id": row[3],
            "model_alias": row[4],
            "model_actual": row[5],
            "latency_ms": row[6],
            "tokens_in": row[7],
            "tokens_out": row[8],
            "cost_usd": float(row[9]),
            "quality_score": float(row[10]) if row[10] is not None else None,
            "is_shadow": bool(row[11]),
            "user_id": row[12],
            "estimated_tokens_in": row[13],
            "overflow_escalated": bool(row[14]),
            "has_payload": has_payload,
        })

    return {"calls": calls, "total": total, "limit": limit, "offset": offset}


@router.get("/claude/calls/{invocation_id}/payload")
async def get_payload(
    request: Request,
    invocation_id: str,
) -> dict[str, Any]:
    """Retrieve full request/response payload for a single invocation."""
    conn = request.app.state.db.connection
    payload_dir: Path = request.app.state.payload_dir

    cursor = await conn.execute(
        "SELECT payload_path FROM invocation_log WHERE id = ?",
        (invocation_id,),
    )
    row = await cursor.fetchone()

    if row is None or row[0] is None:
        raise HTTPException(status_code=404, detail="Payload not found or evicted")

    file_path = payload_dir / row[0]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Payload file missing from disk")

    return json.loads(file_path.read_text())


@router.get("/claude/insights")
async def get_insights(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Proactive optimization insights computed from invocation data."""
    from donna.insights.engine import compute_insights

    conn = request.app.state.db.connection
    payload_dir: Path | None = getattr(request.app.state, "payload_dir", None)

    return await compute_insights(conn=conn, payload_dir=payload_dir, days=days)
```

- [ ] **Step 4: Mount the router in api/__init__.py**

Add after the existing admin router includes (around line 422):

```python
from donna.api.routes import admin_claude
# ...
app.include_router(admin_claude.router, prefix="/admin", tags=["admin"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_admin_claude.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/admin_claude.py src/donna/api/__init__.py tests/unit/test_admin_claude.py
git commit -m "feat(api): add Claude Inspector endpoints for calls, payload, insights"
```

---

### Task 6: Insights Engine

**Files:**
- Create: `src/donna/insights/__init__.py`
- Create: `src/donna/insights/engine.py`
- Test: `tests/unit/test_insights_engine.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_insights_engine.py
"""Unit tests for the insights engine."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.insights.engine import compute_insights


def _cursor(fetchall=None, fetchone=None):
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


@pytest.mark.asyncio
async def test_top_cost_centers() -> None:
    rows = [
        ("parse_task", 2.50, 150, 1200, 300),
        ("dedup_check", 0.80, 50, 800, 100),
    ]
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=_cursor(fetchall=rows))

    result = await compute_insights(conn=conn, payload_dir=None, days=7)

    assert len(result["top_cost_centers"]) == 2
    assert result["top_cost_centers"][0]["task_type"] == "parse_task"
    assert result["top_cost_centers"][0]["total_cost"] == 2.50


@pytest.mark.asyncio
async def test_token_bloat_outliers() -> None:
    # Mock: first call returns medians, second returns outliers
    median_rows = [("parse_task", 1000)]
    outlier_rows = [("inv-big", "parse_task", 3500, 0.012)]

    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=[
        _cursor(fetchall=[("parse_task", 2.5, 150, 1200, 300)]),  # cost centers
        _cursor(fetchall=[]),  # system prompt groups
        _cursor(fetchall=[]),  # quality mismatches
        _cursor(fetchall=median_rows),  # medians
        _cursor(fetchall=outlier_rows),  # outliers
    ])

    result = await compute_insights(conn=conn, payload_dir=None, days=7)

    assert len(result["token_bloat_outliers"]) == 1
    assert result["token_bloat_outliers"][0]["invocation_id"] == "inv-big"
    assert result["token_bloat_outliers"][0]["ratio"] == 3.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_insights_engine.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write insights engine**

```python
# src/donna/insights/__init__.py
"""Insights engine for Claude Inspector."""
```

```python
# src/donna/insights/engine.py
"""Compute optimization insights from invocation data.

Analyzes cost centers, system prompt duplication, quality/cost
mismatches, and token bloat outliers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


async def compute_insights(
    conn: aiosqlite.Connection,
    payload_dir: Path | None,
    days: int = 7,
) -> dict[str, Any]:
    """Compute all insight categories. Returns structured dict."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    top_cost = await _top_cost_centers(conn, since)
    prompt_groups = await _system_prompt_groups(conn, since)
    quality_mismatches = await _quality_cost_mismatches(conn, since)
    bloat_outliers = await _token_bloat_outliers(conn, since)

    return {
        "top_cost_centers": top_cost,
        "system_prompt_groups": prompt_groups,
        "quality_cost_mismatches": quality_mismatches,
        "token_bloat_outliers": bloat_outliers,
    }


async def _top_cost_centers(
    conn: aiosqlite.Connection, since: str
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT task_type, SUM(cost_usd) as total_cost, COUNT(*) as call_count,
                  ROUND(AVG(tokens_in)) as avg_tokens_in,
                  ROUND(AVG(tokens_out)) as avg_tokens_out
           FROM invocation_log
           WHERE timestamp >= ? AND is_shadow = 0
           GROUP BY task_type
           ORDER BY total_cost DESC
           LIMIT 10""",
        (since,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "task_type": r[0],
            "total_cost": round(float(r[1]), 4),
            "call_count": r[2],
            "avg_tokens_in": int(r[3] or 0),
            "avg_tokens_out": int(r[4] or 0),
        }
        for r in rows
    ]


async def _system_prompt_groups(
    conn: aiosqlite.Connection, since: str
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT input_hash, COUNT(*) as call_count,
                  ROUND(AVG(tokens_in)) as avg_tokens_in,
                  SUM(cost_usd) as total_cost,
                  MIN(id) as sample_id
           FROM invocation_log
           WHERE timestamp >= ? AND input_hash != '' AND is_shadow = 0
           GROUP BY input_hash
           HAVING call_count >= 5
           ORDER BY total_cost DESC
           LIMIT 10""",
        (since,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "hash": r[0],
            "call_count": r[1],
            "avg_tokens_in": int(r[2] or 0),
            "estimated_weekly_cost": round(float(r[3]), 4),
            "sample_invocation_id": r[4],
        }
        for r in rows
    ]


async def _quality_cost_mismatches(
    conn: aiosqlite.Connection, since: str
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT task_type, ROUND(AVG(cost_usd), 5) as avg_cost,
                  ROUND(AVG(quality_score), 3) as avg_quality,
                  COUNT(*) as call_count
           FROM invocation_log
           WHERE timestamp >= ? AND quality_score IS NOT NULL AND is_shadow = 0
           GROUP BY task_type
           HAVING avg_cost > (
               SELECT AVG(cost_usd) FROM invocation_log
               WHERE timestamp >= ? AND is_shadow = 0
           ) AND avg_quality < 0.5""",
        (since, since),
    )
    rows = await cursor.fetchall()
    return [
        {
            "task_type": r[0],
            "avg_cost": float(r[1]),
            "avg_quality_score": float(r[2]),
            "call_count": r[3],
        }
        for r in rows
    ]


async def _token_bloat_outliers(
    conn: aiosqlite.Connection, since: str
) -> list[dict[str, Any]]:
    # Get median tokens_in per task_type
    cursor = await conn.execute(
        """SELECT task_type,
                  tokens_in as median_tokens_in
           FROM (
               SELECT task_type, tokens_in,
                      ROW_NUMBER() OVER (PARTITION BY task_type ORDER BY tokens_in) as rn,
                      COUNT(*) OVER (PARTITION BY task_type) as cnt
               FROM invocation_log
               WHERE timestamp >= ? AND is_shadow = 0
           )
           WHERE rn = (cnt + 1) / 2""",
        (since,),
    )
    median_rows = await cursor.fetchall()
    medians = {r[0]: r[1] for r in median_rows}

    if not medians:
        return []

    # Find outliers: tokens_in > 2x median for their task_type
    # Build CASE expression for median lookup
    case_parts = " ".join(
        f"WHEN task_type = '{tt}' THEN {med}" for tt, med in medians.items()
    )
    # Safe: task_type values come from our own DB, not user input

    cursor = await conn.execute(
        f"""SELECT id, task_type, tokens_in, cost_usd
            FROM invocation_log
            WHERE timestamp >= ? AND is_shadow = 0
              AND tokens_in > 2 * (CASE {case_parts} ELSE tokens_in END)
            ORDER BY cost_usd DESC
            LIMIT 10""",
        (since,),
    )
    outlier_rows = await cursor.fetchall()

    return [
        {
            "invocation_id": r[0],
            "task_type": r[1],
            "tokens_in": r[2],
            "median_for_type": medians.get(r[1], 0),
            "ratio": round(r[2] / medians[r[1]], 1) if medians.get(r[1]) else 0,
            "cost_usd": round(float(r[3]), 5),
        }
        for r in outlier_rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_insights_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/insights/__init__.py src/donna/insights/engine.py tests/unit/test_insights_engine.py
git commit -m "feat(insights): add engine for cost centers, prompt groups, bloat detection"
```

---

### Task 7: Frontend API Client

**Files:**
- Create: `donna-ui/src/api/claude.ts`

- [ ] **Step 1: Create the API client module**

```typescript
// donna-ui/src/api/claude.ts
import client from "./client";

export interface ClaudeCall {
  id: string;
  timestamp: string;
  task_type: string;
  task_id: string | null;
  model_alias: string;
  model_actual: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  quality_score: number | null;
  is_shadow: boolean;
  user_id: string;
  estimated_tokens_in: number | null;
  overflow_escalated: boolean;
  has_payload: boolean;
}

export interface ClaudeCallsResponse {
  calls: ClaudeCall[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClaudePayload {
  request: {
    messages: Array<{ role: string; content: string }>;
    model: string;
    tools: unknown[] | null;
    max_tokens: number | null;
  };
  response: {
    content: unknown;
    usage: { input_tokens: number; output_tokens: number };
    stop_reason: string;
    model_actual: string;
  };
}

export interface CostCenter {
  task_type: string;
  total_cost: number;
  call_count: number;
  avg_tokens_in: number;
  avg_tokens_out: number;
}

export interface SystemPromptGroup {
  hash: string;
  call_count: number;
  avg_tokens_in: number;
  estimated_weekly_cost: number;
  sample_invocation_id: string;
}

export interface QualityCostMismatch {
  task_type: string;
  avg_cost: number;
  avg_quality_score: number;
  call_count: number;
}

export interface TokenBloatOutlier {
  invocation_id: string;
  task_type: string;
  tokens_in: number;
  median_for_type: number;
  ratio: number;
  cost_usd: number;
}

export interface ClaudeInsights {
  top_cost_centers: CostCenter[];
  system_prompt_groups: SystemPromptGroup[];
  quality_cost_mismatches: QualityCostMismatch[];
  token_bloat_outliers: TokenBloatOutlier[];
}

export interface ClaudeCallsParams {
  task_type?: string;
  model?: string;
  date_from?: string;
  date_to?: string;
  min_cost?: number;
  min_tokens_in?: number;
  quality_score_below?: number;
  sort?: string;
  sort_dir?: string;
  limit?: number;
  offset?: number;
}

export async function fetchClaudeCalls(
  params: ClaudeCallsParams
): Promise<ClaudeCallsResponse> {
  const { data } = await client.get("/admin/claude/calls", { params });
  return data;
}

export async function fetchClaudePayload(
  invocationId: string
): Promise<ClaudePayload> {
  const { data } = await client.get(
    `/admin/claude/calls/${invocationId}/payload`
  );
  return data;
}

export async function fetchClaudeInsights(
  days: number = 7
): Promise<ClaudeInsights> {
  const { data } = await client.get("/admin/claude/insights", {
    params: { days },
  });
  return data;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/api/claude.ts
git commit -m "feat(ui): add Claude Inspector API client"
```

---

### Task 8: Frontend — Claude Inspector Page (Layout + Insights Panel)

**Files:**
- Create: `donna-ui/src/pages/ClaudeInspector/index.tsx`
- Create: `donna-ui/src/pages/ClaudeInspector/InsightsPanel.tsx`
- Create: `donna-ui/src/pages/ClaudeInspector/claude-inspector.module.css`
- Modify: `donna-ui/src/App.tsx` (add route)

- [ ] **Step 1: Create the CSS module**

```css
/* donna-ui/src/pages/ClaudeInspector/claude-inspector.module.css */
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-5);
}

.insightsGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}

.insightCard {
  background: var(--color-surface-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-2);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.insightCard h4 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-2);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin: 0;
}

.insightCard p {
  font-size: var(--font-size-base);
  color: var(--color-text-1);
  margin: 0;
  line-height: 1.4;
}

.insightValue {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--color-accent);
}

.insightLink {
  font-size: var(--font-size-sm);
  color: var(--color-accent);
  cursor: pointer;
  text-decoration: none;
  margin-top: auto;
}

.insightLink:hover {
  text-decoration: underline;
}

.browserSection {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.filterBar {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  align-items: center;
}

.filterBar select,
.filterBar input {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-1);
  background: var(--color-surface-1);
  color: var(--color-text-1);
  font-size: var(--font-size-sm);
}

.table {
  width: 100%;
  border-collapse: collapse;
}

.table th,
.table td {
  padding: var(--space-2) var(--space-3);
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-size-sm);
}

.table th {
  font-weight: 600;
  color: var(--color-text-2);
  cursor: pointer;
  user-select: none;
}

.table th:hover {
  color: var(--color-text-1);
}

.table tr:hover {
  background: var(--color-surface-2);
}

.clickableRow {
  cursor: pointer;
}

.costCell {
  font-variant-numeric: tabular-nums;
}

.detailPanel {
  background: var(--color-surface-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-2);
  padding: var(--space-4);
  margin: var(--space-2) 0;
}

.payloadSection {
  margin-top: var(--space-3);
}

.payloadSection h5 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  margin: 0 0 var(--space-2);
  color: var(--color-text-2);
}

.codeBlock {
  background: var(--color-surface-2);
  border-radius: var(--radius-1);
  padding: var(--space-3);
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  line-height: 1.5;
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.collapsible {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.collapsible::before {
  content: "▶";
  font-size: 10px;
  transition: transform 0.15s ease;
}

.collapsible[data-open="true"]::before {
  transform: rotate(90deg);
}

.copyBtn {
  font-size: var(--font-size-xs);
  padding: var(--space-1) var(--space-2);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-1);
  cursor: pointer;
  color: var(--color-text-2);
}

.copyBtn:hover {
  background: var(--color-surface-3);
}

.pagination {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  justify-content: center;
  padding: var(--space-3) 0;
}

.comparePanel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
```

- [ ] **Step 2: Create InsightsPanel component**

```tsx
// donna-ui/src/pages/ClaudeInspector/InsightsPanel.tsx
import { useCallback } from "react";
import type { ClaudeInsights } from "../../api/claude";
import styles from "./claude-inspector.module.css";

interface Props {
  insights: ClaudeInsights | null;
  loading: boolean;
  onFilterTaskType: (taskType: string) => void;
}

export default function InsightsPanel({ insights, loading, onFilterTaskType }: Props) {
  if (loading || !insights) {
    return (
      <div className={styles.insightsGrid}>
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className={styles.insightCard}>
            <h4>Loading...</h4>
          </div>
        ))}
      </div>
    );
  }

  const topCost = insights.top_cost_centers[0];
  const topMismatch = insights.quality_cost_mismatches[0];
  const topBloat = insights.token_bloat_outliers[0];
  const topPromptGroup = insights.system_prompt_groups[0];

  return (
    <div className={styles.insightsGrid}>
      {topCost && (
        <div className={styles.insightCard}>
          <h4>Top Cost Center</h4>
          <p>
            <span className={styles.insightValue}>${topCost.total_cost.toFixed(2)}</span>
            {" "}on <code>{topCost.task_type}</code>
          </p>
          <p>{topCost.call_count} calls, avg {topCost.avg_tokens_in} tokens in</p>
          <span
            className={styles.insightLink}
            onClick={() => onFilterTaskType(topCost.task_type)}
          >
            View calls →
          </span>
        </div>
      )}

      {topMismatch && (
        <div className={styles.insightCard}>
          <h4>Quality/Cost Mismatch</h4>
          <p>
            <code>{topMismatch.task_type}</code> — avg quality{" "}
            <span className={styles.insightValue}>
              {(topMismatch.avg_quality_score * 100).toFixed(0)}%
            </span>
          </p>
          <p>Avg cost ${topMismatch.avg_cost.toFixed(4)} across {topMismatch.call_count} calls</p>
          <span
            className={styles.insightLink}
            onClick={() => onFilterTaskType(topMismatch.task_type)}
          >
            Investigate →
          </span>
        </div>
      )}

      {topBloat && (
        <div className={styles.insightCard}>
          <h4>Token Bloat</h4>
          <p>
            <span className={styles.insightValue}>{topBloat.ratio}x</span> median for{" "}
            <code>{topBloat.task_type}</code>
          </p>
          <p>{topBloat.tokens_in.toLocaleString()} tokens (median: {topBloat.median_for_type.toLocaleString()})</p>
          <span
            className={styles.insightLink}
            onClick={() => onFilterTaskType(topBloat.task_type)}
          >
            View outliers →
          </span>
        </div>
      )}

      {topPromptGroup && (
        <div className={styles.insightCard}>
          <h4>Repeated System Prompt</h4>
          <p>
            <span className={styles.insightValue}>{topPromptGroup.call_count}</span> calls share same prompt
          </p>
          <p>~{topPromptGroup.avg_tokens_in} tokens, ${topPromptGroup.estimated_weekly_cost.toFixed(2)}/week</p>
          <span className={styles.insightLink}>
            Consider prompt caching →
          </span>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create the page index**

```tsx
// donna-ui/src/pages/ClaudeInspector/index.tsx
import { useState, useEffect, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import {
  fetchClaudeInsights,
  fetchClaudeCalls,
  type ClaudeInsights,
  type ClaudeCall,
  type ClaudeCallsParams,
} from "../../api/claude";
import InsightsPanel from "./InsightsPanel";
import CallBrowser from "./CallBrowser";
import styles from "./claude-inspector.module.css";

export default function ClaudeInspector() {
  const [insights, setInsights] = useState<ClaudeInsights | null>(null);
  const [calls, setCalls] = useState<ClaudeCall[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [insightsLoading, setInsightsLoading] = useState(true);
  const [filters, setFilters] = useState<ClaudeCallsParams>({
    sort: "timestamp",
    sort_dir: "desc",
    limit: 50,
    offset: 0,
  });

  const loadInsights = useCallback(async () => {
    setInsightsLoading(true);
    try {
      const data = await fetchClaudeInsights(7);
      setInsights(data);
    } catch {
      setInsights(null);
    } finally {
      setInsightsLoading(false);
    }
  }, []);

  const loadCalls = useCallback(async (params: ClaudeCallsParams) => {
    setLoading(true);
    try {
      const data = await fetchClaudeCalls(params);
      setCalls(data.calls);
      setTotal(data.total);
    } catch {
      setCalls([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadInsights();
  }, [loadInsights]);

  useEffect(() => {
    loadCalls(filters);
  }, [filters, loadCalls]);

  const handleFilterTaskType = useCallback((taskType: string) => {
    setFilters((prev) => ({ ...prev, task_type: taskType, offset: 0 }));
  }, []);

  const handleFiltersChange = useCallback((newFilters: ClaudeCallsParams) => {
    setFilters(newFilters);
  }, []);

  return (
    <div className={styles.page}>
      <PageHeader eyebrow="Forensics" title="Claude Inspector" />

      <InsightsPanel
        insights={insights}
        loading={insightsLoading}
        onFilterTaskType={handleFilterTaskType}
      />

      <CallBrowser
        calls={calls}
        total={total}
        loading={loading}
        filters={filters}
        onFiltersChange={handleFiltersChange}
      />
    </div>
  );
}
```

- [ ] **Step 4: Add the route to App.tsx**

Add import at the top with the other lazy/page imports:
```tsx
import ClaudeInspector from "./pages/ClaudeInspector";
```

Add route inside the `<Route element={<AppShell />}>` block:
```tsx
<Route path="/claude" element={<ErrorBoundary><ClaudeInspector /></ErrorBoundary>} />
```

- [ ] **Step 5: Verify TypeScript compiles (will fail — CallBrowser doesn't exist yet, that's OK)**

This step is deferred until Task 9.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/ClaudeInspector/index.tsx donna-ui/src/pages/ClaudeInspector/InsightsPanel.tsx donna-ui/src/pages/ClaudeInspector/claude-inspector.module.css donna-ui/src/App.tsx
git commit -m "feat(ui): add Claude Inspector page with insights panel"
```

---

### Task 9: Frontend — Call Browser

**Files:**
- Create: `donna-ui/src/pages/ClaudeInspector/CallBrowser.tsx`

- [ ] **Step 1: Create CallBrowser component**

```tsx
// donna-ui/src/pages/ClaudeInspector/CallBrowser.tsx
import { useState, useCallback } from "react";
import type { ClaudeCall, ClaudeCallsParams } from "../../api/claude";
import { Button } from "../../primitives/Button";
import CallDetail from "./CallDetail";
import styles from "./claude-inspector.module.css";

interface Props {
  calls: ClaudeCall[];
  total: number;
  loading: boolean;
  filters: ClaudeCallsParams;
  onFiltersChange: (filters: ClaudeCallsParams) => void;
}

export default function CallBrowser({
  calls,
  total,
  loading,
  filters,
  onFiltersChange,
}: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);

  const handleSort = useCallback(
    (col: string) => {
      const isSame = filters.sort === col;
      onFiltersChange({
        ...filters,
        sort: col,
        sort_dir: isSame && filters.sort_dir === "desc" ? "asc" : "desc",
      });
    },
    [filters, onFiltersChange]
  );

  const handlePagePrev = useCallback(() => {
    const offset = Math.max(0, (filters.offset || 0) - (filters.limit || 50));
    onFiltersChange({ ...filters, offset });
  }, [filters, onFiltersChange]);

  const handlePageNext = useCallback(() => {
    const offset = (filters.offset || 0) + (filters.limit || 50);
    if (offset < total) {
      onFiltersChange({ ...filters, offset });
    }
  }, [filters, onFiltersChange, total]);

  const handleCompareToggle = useCallback((id: string) => {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  }, []);

  const currentPage = Math.floor((filters.offset || 0) / (filters.limit || 50)) + 1;
  const totalPages = Math.ceil(total / (filters.limit || 50));

  return (
    <div className={styles.browserSection}>
      <div className={styles.filterBar}>
        <input
          type="text"
          placeholder="Task type..."
          value={filters.task_type || ""}
          onChange={(e) =>
            onFiltersChange({ ...filters, task_type: e.target.value || undefined, offset: 0 })
          }
        />
        <input
          type="text"
          placeholder="Model..."
          value={filters.model || ""}
          onChange={(e) =>
            onFiltersChange({ ...filters, model: e.target.value || undefined, offset: 0 })
          }
        />
        <input
          type="date"
          value={filters.date_from || ""}
          onChange={(e) =>
            onFiltersChange({ ...filters, date_from: e.target.value || undefined, offset: 0 })
          }
        />
        <input
          type="date"
          value={filters.date_to || ""}
          onChange={(e) =>
            onFiltersChange({ ...filters, date_to: e.target.value || undefined, offset: 0 })
          }
        />
        {filters.task_type && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onFiltersChange({ ...filters, task_type: undefined, offset: 0 })}
          >
            Clear filters
          </Button>
        )}
      </div>

      <table className={styles.table}>
        <thead>
          <tr>
            <th onClick={() => handleSort("timestamp")}>
              Time {filters.sort === "timestamp" ? (filters.sort_dir === "desc" ? "↓" : "↑") : ""}
            </th>
            <th>Task Type</th>
            <th>Model</th>
            <th onClick={() => handleSort("tokens_in")}>
              Tokens In {filters.sort === "tokens_in" ? (filters.sort_dir === "desc" ? "↓" : "↑") : ""}
            </th>
            <th onClick={() => handleSort("tokens_out")}>
              Tokens Out {filters.sort === "tokens_out" ? (filters.sort_dir === "desc" ? "↓" : "↑") : ""}
            </th>
            <th onClick={() => handleSort("cost")}>
              Cost {filters.sort === "cost" ? (filters.sort_dir === "desc" ? "↓" : "↑") : ""}
            </th>
            <th>Quality</th>
            <th onClick={() => handleSort("latency")}>
              Latency {filters.sort === "latency" ? (filters.sort_dir === "desc" ? "↓" : "↑") : ""}
            </th>
            <th>Compare</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={9}>Loading...</td>
            </tr>
          ) : calls.length === 0 ? (
            <tr>
              <td colSpan={9}>No calls found</td>
            </tr>
          ) : (
            calls.map((call) => (
              <>
                <tr
                  key={call.id}
                  className={styles.clickableRow}
                  onClick={() => setExpandedId(expandedId === call.id ? null : call.id)}
                >
                  <td>{new Date(call.timestamp).toLocaleString()}</td>
                  <td><code>{call.task_type}</code></td>
                  <td>{call.model_alias}</td>
                  <td>{call.tokens_in.toLocaleString()}</td>
                  <td>{call.tokens_out.toLocaleString()}</td>
                  <td className={styles.costCell}>${call.cost_usd.toFixed(4)}</td>
                  <td>{call.quality_score != null ? `${(call.quality_score * 100).toFixed(0)}%` : "—"}</td>
                  <td>{call.latency_ms}ms</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={compareIds.includes(call.id)}
                      onChange={() => handleCompareToggle(call.id)}
                    />
                  </td>
                </tr>
                {expandedId === call.id && (
                  <tr key={`${call.id}-detail`}>
                    <td colSpan={9}>
                      <CallDetail invocationId={call.id} hasPayload={call.has_payload} />
                    </td>
                  </tr>
                )}
              </>
            ))
          )}
        </tbody>
      </table>

      <div className={styles.pagination}>
        <Button variant="ghost" size="sm" onClick={handlePagePrev} disabled={currentPage <= 1}>
          ← Prev
        </Button>
        <span>
          Page {currentPage} of {totalPages} ({total} total)
        </span>
        <Button variant="ghost" size="sm" onClick={handlePageNext} disabled={currentPage >= totalPages}>
          Next →
        </Button>
      </div>

      {compareIds.length === 2 && (
        <CallCompareWrapper ids={compareIds} />
      )}
    </div>
  );
}

function CallCompareWrapper({ ids }: { ids: string[] }) {
  // Lazy import to avoid circular dependency
  const CallCompare = require("./CallCompare").default;
  return <CallCompare leftId={ids[0]} rightId={ids[1]} />;
}
```

- [ ] **Step 2: Verify TypeScript compiles (will need CallDetail and CallCompare — continue to next tasks)**

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/ClaudeInspector/CallBrowser.tsx
git commit -m "feat(ui): add Claude Inspector call browser table"
```

---

### Task 10: Frontend — Call Detail Panel

**Files:**
- Create: `donna-ui/src/pages/ClaudeInspector/CallDetail.tsx`

- [ ] **Step 1: Create CallDetail component**

```tsx
// donna-ui/src/pages/ClaudeInspector/CallDetail.tsx
import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import { fetchClaudePayload, type ClaudePayload } from "../../api/claude";
import styles from "./claude-inspector.module.css";

interface Props {
  invocationId: string;
  hasPayload: boolean;
}

export default function CallDetail({ invocationId, hasPayload }: Props) {
  const [payload, setPayload] = useState<ClaudePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [requestOpen, setRequestOpen] = useState(true);
  const [responseOpen, setResponseOpen] = useState(true);

  useEffect(() => {
    if (!hasPayload) return;
    setLoading(true);
    fetchClaudePayload(invocationId)
      .then(setPayload)
      .catch(() => setPayload(null))
      .finally(() => setLoading(false));
  }, [invocationId, hasPayload]);

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard");
  }, []);

  if (!hasPayload) {
    return (
      <div className={styles.detailPanel}>
        <p>Payload evicted or not captured for this invocation.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className={styles.detailPanel}>
        <p>Loading payload...</p>
      </div>
    );
  }

  if (!payload) {
    return (
      <div className={styles.detailPanel}>
        <p>Failed to load payload.</p>
      </div>
    );
  }

  const requestJson = JSON.stringify(payload.request, null, 2);
  const responseJson = JSON.stringify(payload.response, null, 2);

  return (
    <div className={styles.detailPanel}>
      <div className={styles.payloadSection}>
        <div
          className={styles.collapsible}
          data-open={requestOpen}
          onClick={() => setRequestOpen(!requestOpen)}
        >
          <h5>Request ({payload.request.messages?.length || 0} messages)</h5>
        </div>
        {requestOpen && (
          <>
            <button
              className={styles.copyBtn}
              onClick={() => handleCopy(requestJson)}
            >
              Copy
            </button>
            <pre className={styles.codeBlock}>{requestJson}</pre>
          </>
        )}
      </div>

      <div className={styles.payloadSection}>
        <div
          className={styles.collapsible}
          data-open={responseOpen}
          onClick={() => setResponseOpen(!responseOpen)}
        >
          <h5>Response</h5>
        </div>
        {responseOpen && (
          <>
            <button
              className={styles.copyBtn}
              onClick={() => handleCopy(responseJson)}
            >
              Copy
            </button>
            <pre className={styles.codeBlock}>{responseJson}</pre>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/pages/ClaudeInspector/CallDetail.tsx
git commit -m "feat(ui): add Claude Inspector call detail panel"
```

---

### Task 11: Frontend — Call Compare View

**Files:**
- Create: `donna-ui/src/pages/ClaudeInspector/CallCompare.tsx`

- [ ] **Step 1: Create CallCompare component**

```tsx
// donna-ui/src/pages/ClaudeInspector/CallCompare.tsx
import { useState, useEffect } from "react";
import { fetchClaudePayload, type ClaudePayload } from "../../api/claude";
import styles from "./claude-inspector.module.css";

interface Props {
  leftId: string;
  rightId: string;
}

export default function CallCompare({ leftId, rightId }: Props) {
  const [left, setLeft] = useState<ClaudePayload | null>(null);
  const [right, setRight] = useState<ClaudePayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchClaudePayload(leftId).catch(() => null),
      fetchClaudePayload(rightId).catch(() => null),
    ]).then(([l, r]) => {
      setLeft(l);
      setRight(r);
      setLoading(false);
    });
  }, [leftId, rightId]);

  if (loading) {
    return <div className={styles.comparePanel}><p>Loading comparison...</p></div>;
  }

  return (
    <div className={styles.comparePanel}>
      <div>
        <h5>Call: {leftId.slice(0, 8)}...</h5>
        {left ? (
          <pre className={styles.codeBlock}>
            {JSON.stringify(left.request, null, 2)}
          </pre>
        ) : (
          <p>Payload unavailable</p>
        )}
      </div>
      <div>
        <h5>Call: {rightId.slice(0, 8)}...</h5>
        {right ? (
          <pre className={styles.codeBlock}>
            {JSON.stringify(right.request, null, 2)}
          </pre>
        ) : (
          <p>Payload unavailable</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify full TypeScript compilation**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Run vite build to check for bundling issues**

Run: `cd donna-ui && npx vite build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/ClaudeInspector/CallCompare.tsx
git commit -m "feat(ui): add Claude Inspector side-by-side comparison view"
```

---

### Task 12: Wire Up PayloadWriter in App Startup

**Files:**
- Modify: `src/donna/api/__init__.py` (app startup — create PayloadWriter, store on app.state)

- [ ] **Step 1: Find where app.state.db is set and add PayloadWriter initialization nearby**

In `src/donna/api/__init__.py`, during app startup (lifespan or startup event), add:

```python
from pathlib import Path
from donna.collection.payload_writer import PayloadWriter

# After DB connection is established:
payload_dir = Path("data/payloads")
payload_writer = PayloadWriter(base_dir=payload_dir)
payload_writer.sync_size_from_disk()
app.state.payload_dir = payload_dir
app.state.payload_writer = payload_writer
```

Also update the ModelRouter instantiation to pass the writer:

```python
# Where ModelRouter is created, add payload_writer=payload_writer
```

- [ ] **Step 2: Add nav link to the AppShell sidebar**

Find the navigation config (likely in a shell/sidebar component) and add an entry for `/claude` with label "Claude Inspector".

- [ ] **Step 3: Run the backend to verify startup**

Run: `python -m donna.api` (or however the dev server starts)
Expected: No import errors, server starts cleanly

- [ ] **Step 4: Commit**

```bash
git add src/donna/api/__init__.py
git commit -m "feat: wire PayloadWriter into app startup and state"
```

---

### Task 13: Scheduled Eviction

**Files:**
- Modify: app startup or scheduler setup to run eviction hourly

- [ ] **Step 1: Add an hourly eviction job**

In the app startup/scheduler code, add a background task that runs `PayloadEvictor.evict()` every hour:

```python
import asyncio
from donna.collection.payload_evictor import PayloadEvictor

async def _eviction_loop(writer: PayloadWriter, conn) -> None:
    evictor = PayloadEvictor(writer=writer, conn=conn)
    while True:
        await asyncio.sleep(3600)
        writer.sync_size_from_disk()
        await evictor.evict()

# In startup:
asyncio.create_task(_eviction_loop(payload_writer, db_conn))
```

- [ ] **Step 2: Commit**

```bash
git add src/donna/api/__init__.py
git commit -m "feat: add hourly payload eviction background task"
```

---

### Task 14: End-to-End Verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Run frontend build**

Run: `cd donna-ui && npx vite build`
Expected: Build succeeds with no errors

- [ ] **Step 3: Start the dev server and verify the page loads**

Run the backend and frontend dev servers. Navigate to `/claude` in the browser.
Verify:
- Insights panel renders (may be empty if no data yet)
- Call browser table renders with column headers
- Filters are interactive
- No console errors

- [ ] **Step 4: Trigger a test invocation and verify payload capture**

Make a test API call that triggers `complete()`. Check:
- `data/payloads/{today}/` contains a new `.json` file
- The invocation_log row has a non-null `payload_path`
- Clicking the row in the UI shows the full request/response

- [ ] **Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: address e2e verification issues for Claude Inspector"
```
