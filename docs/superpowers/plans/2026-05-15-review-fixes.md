# Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 issues from code review: BigInteger migration, untracked thread guard, skill cascade dead end, Ollama loud failure, atomic dispatcher success path, tool_use loop tests.

**Architecture:** Targeted fixes across the automation, Discord, skill, and model layers. Each task is independent — no cross-task dependencies except Task 6 (tests) which validates Task 5's repository change.

**Tech Stack:** Python 3.12, Alembic, aiosqlite, discord.py, pytest

---

### Task 1: BigInteger migration for Discord snowflake IDs

**Files:**
- Create: `alembic/versions/c2d3e4f5a6b7_bigint_overdue_thread.py`

- [ ] **Step 1: Create the migration file**

SQLite does not support `ALTER COLUMN`, so the table must be recreated. The migration renames the old table, creates a new one with `BigInteger`, copies data, and drops the old table.

```python
"""widen overdue_thread_map.discord_thread_id to BigInteger

SQLite stores 64-bit integers regardless of declared type, so existing
data is unaffected.  This migration corrects the schema declaration for
Postgres portability (Supabase sync).

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-15 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("overdue_thread_map", "_overdue_thread_map_old")
    op.create_table(
        "overdue_thread_map",
        sa.Column("discord_thread_id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
    )
    op.execute(
        "INSERT INTO overdue_thread_map "
        "SELECT discord_thread_id, task_id, created_at "
        "FROM _overdue_thread_map_old"
    )
    op.drop_table("_overdue_thread_map_old")
    op.create_index(
        "idx_overdue_thread_map_task",
        "overdue_thread_map",
        ["task_id"],
    )


def downgrade() -> None:
    op.rename_table("overdue_thread_map", "_overdue_thread_map_old")
    op.create_table(
        "overdue_thread_map",
        sa.Column("discord_thread_id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
    )
    op.execute(
        "INSERT INTO overdue_thread_map "
        "SELECT discord_thread_id, task_id, created_at "
        "FROM _overdue_thread_map_old"
    )
    op.drop_table("_overdue_thread_map_old")
    op.create_index(
        "idx_overdue_thread_map_task",
        "overdue_thread_map",
        ["task_id"],
    )
```

- [ ] **Step 2: Run migration against dev DB**

```bash
.venv/bin/python -m alembic upgrade head
```

Expected: applies cleanly, no errors.

- [ ] **Step 3: Verify data preserved**

```bash
sqlite3 /mnt/donna/db/donna_tasks.db "SELECT count(*) FROM overdue_thread_map"
```

Expected: same row count as before migration.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/c2d3e4f5a6b7_bigint_overdue_thread.py
git commit -m "fix(migration): widen overdue_thread_map snowflake to BigInteger"
```

---

### Task 2: Guard untracked thread replies with done intent check

**Files:**
- Modify: `src/donna/integrations/discord_bot.py:442-445`

- [ ] **Step 1: Replace unconditional done intent call with guarded version**

In `src/donna/integrations/discord_bot.py`, replace lines 442-445:

```python
        if in_untracked_thread:
            log.info("untracked_thread_reply", raw_text=raw_text[:60])
            await self._handle_done_intent(message, user_id, log)
            return
```

With:

```python
        if in_untracked_thread:
            log.info("untracked_thread_reply", raw_text=raw_text[:60])
            if _detect_done_intent(raw_text):
                await self._handle_done_intent(message, user_id, log)
            else:
                await message.reply(
                    "I see your reply but I'm not sure what you'd like me to do. "
                    "Try **done** to mark a task complete."
                )
            return
```

- [ ] **Step 2: Run lint**

```bash
.venv/bin/python -m ruff check src/donna/integrations/discord_bot.py
```

Expected: All checks passed.

- [ ] **Step 3: Commit**

```bash
git add src/donna/integrations/discord_bot.py
git commit -m "fix(discord): guard untracked thread replies with done intent check"
```

---

### Task 3: Fix skill cascade dead end in product_watch v3

**Files:**
- Modify: `skills/product_watch/skill.yaml:58-70`

- [ ] **Step 1: Add `on_failure: continue` to `claude_with_triage` and fix `claude_fallback` condition**

In `skills/product_watch/skill.yaml`, the `claude_with_triage` step (line 58-63) currently reads:

```yaml
  - name: claude_with_triage
    kind: llm
    prompt: steps/extract_with_triage.md
    output_schema: schemas/extract_product_info_v1.json
    model: parser
    condition: "state.triage_for_claude.success and not (state.try_local_extract.success or state.try_vision_extract.success)"
```

Replace it with (adding `on_failure: continue`):

```yaml
  - name: claude_with_triage
    kind: llm
    prompt: steps/extract_with_triage.md
    output_schema: schemas/extract_product_info_v1.json
    model: parser
    condition: "state.triage_for_claude.success and not (state.try_local_extract.success or state.try_vision_extract.success)"
    on_failure: continue
```

Then the `claude_fallback` step (line 65-70) currently has:

```yaml
    condition: "not (state.try_local_extract.success or state.try_vision_extract.success or state.triage_for_claude.success)"
```

Replace with (check `claude_with_triage.success` instead of `triage_for_claude.success`):

```yaml
    condition: "not (state.try_local_extract.success or state.try_vision_extract.success or state.claude_with_triage.success)"
```

- [ ] **Step 2: Commit**

```bash
git add skills/product_watch/skill.yaml
git commit -m "fix(skills): add on_failure to claude_with_triage, fix fallback condition"
```

---

### Task 4: Ollama loud failure for unsupported params

**Files:**
- Modify: `src/donna/models/providers/ollama.py:74` (top of `complete()` body, after `session = self._get_session()`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_ollama_unsupported.py`:

```python
"""Verify OllamaProvider rejects tool_use and multi-turn messages."""

import pytest

from donna.models.providers.ollama import OllamaProvider


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider(base_url="http://localhost:11434")


@pytest.mark.asyncio
async def test_tools_raises(provider: OllamaProvider) -> None:
    with pytest.raises(NotImplementedError, match="tool_use"):
        await provider.complete(
            prompt="test", model="test", tools=[{"name": "web_fetch"}],
        )


@pytest.mark.asyncio
async def test_messages_raises(provider: OllamaProvider) -> None:
    with pytest.raises(NotImplementedError, match="multi-turn"):
        await provider.complete(
            prompt="test", model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/models/test_ollama_unsupported.py -v
```

Expected: FAIL — no `NotImplementedError` raised.

- [ ] **Step 3: Add guards to OllamaProvider.complete()**

In `src/donna/models/providers/ollama.py`, after line 73 (`session = self._get_session()`), add:

```python
        if tools:
            raise NotImplementedError("Ollama does not support tool_use")
        if messages:
            raise NotImplementedError("Ollama does not support multi-turn messages")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/models/test_ollama_unsupported.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run lint and typecheck**

```bash
.venv/bin/python -m ruff check src/donna/models/providers/ollama.py tests/unit/models/test_ollama_unsupported.py
.venv/bin/python -m mypy src/donna/models/providers/ollama.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/donna/models/providers/ollama.py tests/unit/models/test_ollama_unsupported.py
git commit -m "fix(ollama): raise NotImplementedError for tools and messages params"
```

---

### Task 5: Atomic dispatcher success reset via advance_schedule

**Files:**
- Modify: `src/donna/automations/repository.py:190-214`
- Modify: `src/donna/automations/dispatcher.py:286-295`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/automations/test_advance_schedule_overrides.py`:

```python
"""Verify advance_schedule applies status and failure_count overrides atomically."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.repository import AutomationRepository


@pytest.fixture
def repo() -> AutomationRepository:
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return AutomationRepository(conn)


@pytest.mark.asyncio
async def test_advance_schedule_without_overrides(repo: AutomationRepository) -> None:
    """Baseline: overrides not passed, SQL has no status/failure_count clause."""
    now = datetime.now(UTC)
    await repo.advance_schedule(
        automation_id="a1",
        last_run_at=now,
        next_run_at=now,
        increment_run_count=True,
        increment_failure_count=False,
    )
    sql = repo._conn.execute.call_args[0][0]
    assert "status" not in sql
    assert repo._conn.commit.await_count == 1


@pytest.mark.asyncio
async def test_advance_schedule_with_overrides(repo: AutomationRepository) -> None:
    """When overrides provided, SQL includes status and failure_count clauses."""
    now = datetime.now(UTC)
    await repo.advance_schedule(
        automation_id="a1",
        last_run_at=now,
        next_run_at=now,
        increment_run_count=True,
        increment_failure_count=False,
        status_override="active",
        failure_count_override=0,
    )
    sql = repo._conn.execute.call_args[0][0]
    params = repo._conn.execute.call_args[0][1]
    assert "status = ?" in sql
    assert "failure_count = ?" in sql
    assert "active" in params
    assert 0 in params
    assert repo._conn.commit.await_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/automations/test_advance_schedule_overrides.py -v
```

Expected: FAIL — `test_advance_schedule_with_overrides` fails because `advance_schedule` does not accept the new params.

- [ ] **Step 3: Add override params to `advance_schedule`**

In `src/donna/automations/repository.py`, replace the `advance_schedule` method (lines 190-214) with:

```python
    async def advance_schedule(
        self,
        automation_id: str,
        *,
        last_run_at: datetime,
        next_run_at: datetime | None,
        increment_run_count: bool,
        increment_failure_count: bool,
        status_override: str | None = None,
        failure_count_override: int | None = None,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        clauses = [
            "last_run_at = ?",
            "next_run_at = ?",
            "run_count = run_count + ?",
            "failure_count = failure_count + ?",
        ]
        params: list[Any] = [
            last_run_at.isoformat(),
            next_run_at.isoformat() if next_run_at else None,
            1 if increment_run_count else 0,
            1 if increment_failure_count else 0,
        ]
        if status_override is not None:
            clauses.append("status = ?")
            params.append(status_override)
        if failure_count_override is not None:
            clauses.append("failure_count = ?")
            params.append(failure_count_override)
        clauses.append("updated_at = ?")
        params.append(now_iso)
        params.append(automation_id)
        await self._conn.execute(
            f"UPDATE automation SET {', '.join(clauses)} WHERE id = ?",
            tuple(params),
        )
        await self._conn.commit()
```

Note: when `failure_count_override` is set alongside `increment_failure_count`, the override runs second (SQL is evaluated left-to-right), so `failure_count = ?` overwrites `failure_count = failure_count + ?`. This is the intended behavior — the override is absolute.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/automations/test_advance_schedule_overrides.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Update dispatcher to use merged call**

In `src/donna/automations/dispatcher.py`, replace lines 286-295:

```python
        await self._repo.advance_schedule(
            automation_id=automation.id, last_run_at=now,
            next_run_at=next_run_at,
            increment_run_count=True,
            increment_failure_count=not run_succeeded,
        )
        if run_succeeded:
            await self._repo.update_fields(
                automation.id, failure_count=0, status="active",
            )
```

With:

```python
        advance_kwargs: dict[str, Any] = dict(
            automation_id=automation.id, last_run_at=now,
            next_run_at=next_run_at,
            increment_run_count=True,
            increment_failure_count=not run_succeeded,
        )
        if run_succeeded:
            advance_kwargs["status_override"] = "active"
            advance_kwargs["failure_count_override"] = 0
        await self._repo.advance_schedule(**advance_kwargs)
```

You will also need to add `Any` to the typing imports at the top of `dispatcher.py` if not already present. Check the existing imports first — `Any` is likely already imported.

- [ ] **Step 6: Run lint and typecheck**

```bash
.venv/bin/python -m ruff check src/donna/automations/repository.py src/donna/automations/dispatcher.py
.venv/bin/python -m mypy src/donna/automations/repository.py src/donna/automations/dispatcher.py
```

Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/repository.py src/donna/automations/dispatcher.py tests/unit/automations/test_advance_schedule_overrides.py
git commit -m "fix(automations): atomic success reset in advance_schedule"
```

---

### Task 6: Tests for executor tool_use loop

**Files:**
- Create: `tests/unit/skills/test_executor_tool_loop.py`

- [ ] **Step 1: Create test directory if needed**

```bash
mkdir -p tests/unit/skills
touch tests/unit/skills/__init__.py
```

- [ ] **Step 2: Write all 5 test cases**

Create `tests/unit/skills/test_executor_tool_loop.py`:

```python
"""Unit tests for SkillExecutor._complete_with_tool_loop.

Tests the tool_use loop without real LLM calls — the router and tool
registry are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from donna.models.types import CompletionMetadata
from donna.skills.executor import SkillExecutor
from donna.skills.tool_registry import ToolRegistry


def _meta(cost: float = 0.01) -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=100, tokens_in=50, tokens_out=50,
        cost_usd=cost, model_actual="test/model",
    )


def _tool_use_output(
    tool_name: str = "web_fetch",
    tool_id: str = "call_1",
    tool_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "_tool_use": [
            {"id": tool_id, "name": tool_name, "input": tool_input or {"url": "https://example.com"}},
        ],
        "_content": [{"type": "tool_use", "id": tool_id, "name": tool_name}],
    }


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("web_fetch", AsyncMock(return_value={"status": 200, "body": "ok"}))
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> SkillExecutor:
    router = AsyncMock()
    return SkillExecutor(model_router=router, tool_registry=registry)


@pytest.mark.asyncio
async def test_no_tools_passthrough(executor: SkillExecutor) -> None:
    """When tool_definitions is None, the router is called directly."""
    executor._router.complete = AsyncMock(return_value=({"answer": 42}, _meta()))
    output, meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=[], tool_definitions=None,
    )
    assert output == {"answer": 42}
    assert cost == pytest.approx(0.01)
    executor._router.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_round_tool_use(executor: SkillExecutor) -> None:
    """Router returns tool_use, tool dispatches, router returns text."""
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(), _meta(0.02)),
        ({"answer": "fetched"}, _meta(0.03)),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "fetched"}
    assert cost == pytest.approx(0.05)
    assert executor._router.complete.await_count == 2


@pytest.mark.asyncio
async def test_multi_round_tool_use(executor: SkillExecutor) -> None:
    """Two rounds of tool calls before final text response."""
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(tool_id="call_1"), _meta(0.01)),
        (_tool_use_output(tool_id="call_2"), _meta(0.01)),
        ({"answer": "done"}, _meta(0.01)),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "done"}
    assert cost == pytest.approx(0.03)
    assert executor._router.complete.await_count == 3


@pytest.mark.asyncio
async def test_max_rounds_exceeded(executor: SkillExecutor) -> None:
    """RuntimeError raised after max_rounds of tool_use."""
    executor._router.complete = AsyncMock(
        return_value=(_tool_use_output(), _meta()),
    )
    tools = [{"name": "web_fetch", "input_schema": {}}]
    with pytest.raises(RuntimeError, match="exceeded 3 rounds"):
        await executor._complete_with_tool_loop(
            prompt="test", task_type="t", user_id="u",
            tool_names=["web_fetch"], tool_definitions=tools,
            max_rounds=3,
        )


@pytest.mark.asyncio
async def test_tool_dispatch_error(executor: SkillExecutor) -> None:
    """Tool dispatch failure sends is_error tool_result, loop continues."""
    executor._tool_registry = ToolRegistry()
    executor._tool_registry.register(
        "web_fetch", AsyncMock(side_effect=RuntimeError("connection refused")),
    )
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(), _meta()),
        ({"answer": "recovered"}, _meta()),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "recovered"}
    second_call_messages = executor._router.complete.call_args_list[1].kwargs.get(
        "messages",
        executor._router.complete.call_args_list[1][1] if len(executor._router.complete.call_args_list[1]) > 1 else None,
    )
    if second_call_messages is None:
        second_call_kwargs = executor._router.complete.call_args_list[1].kwargs
        second_call_messages = second_call_kwargs["messages"]
    error_msg = second_call_messages[-1]
    assert error_msg["content"][0]["is_error"] is True
    assert "connection refused" in error_msg["content"][0]["content"]
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/skills/test_executor_tool_loop.py -v
```

Expected: 5 passed.

- [ ] **Step 4: Run lint**

```bash
.venv/bin/python -m ruff check tests/unit/skills/test_executor_tool_loop.py
```

Expected: All checks passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/skills/test_executor_tool_loop.py tests/unit/skills/__init__.py
git commit -m "test(skills): add unit tests for executor tool_use loop"
```

---

### Task 7: Final validation

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -x -q --tb=short
```

Expected: all pass (except pre-existing flaky `test_memory_informed_writer_idempotency`).

- [ ] **Step 2: Run lint on all changed files**

```bash
.venv/bin/python -m ruff check src/donna/automations/repository.py src/donna/automations/dispatcher.py src/donna/integrations/discord_bot.py src/donna/models/providers/ollama.py skills/product_watch/skill.yaml
```

- [ ] **Step 3: Run typecheck on all changed files**

```bash
.venv/bin/python -m mypy src/donna/automations/repository.py src/donna/automations/dispatcher.py src/donna/integrations/discord_bot.py src/donna/models/providers/ollama.py
```
