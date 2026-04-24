"""Unit tests for :class:`donna.memory.sources_task.TaskSource` re-embed rules."""
from __future__ import annotations

import pytest

from donna.config import TaskSourceConfig
from donna.memory.sources_task import SOURCE_TYPE, TaskSource
from donna.memory.store import MemoryStore


def _task(**overrides: object) -> dict[str, object]:
    base = {
        "id": "task-1",
        "user_id": "nick",
        "title": "Original title",
        "description": "Original description",
        "status": "backlog",
        "domain": "work",
        "priority": 2,
        "deadline": None,
        "notes": ["alpha"],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_unchanged_content_skips_reembed(
    memory_store: MemoryStore, fake_provider
) -> None:
    cfg = TaskSourceConfig(enabled=True)
    source = TaskSource(store=memory_store, cfg=cfg)
    await source.observe_task({"action": "create", "task": _task()})
    first = fake_provider.total_embed_rows
    # Update with a non-semantic field only (priority) — content hash
    # should be unchanged so no new embed calls happen.
    await source.observe_task(
        {
            "action": "update",
            "task": _task(priority=4),
            "previous_status": "backlog",
        }
    )
    assert fake_provider.total_embed_rows == first


@pytest.mark.asyncio
async def test_title_change_forces_reembed(
    memory_store: MemoryStore, fake_provider
) -> None:
    cfg = TaskSourceConfig(enabled=True)
    source = TaskSource(store=memory_store, cfg=cfg)
    await source.observe_task({"action": "create", "task": _task()})
    first = fake_provider.total_embed_rows
    await source.observe_task(
        {
            "action": "update",
            "task": _task(title="Renamed task"),
            "previous_status": "backlog",
        }
    )
    assert fake_provider.total_embed_rows > first


@pytest.mark.asyncio
async def test_status_done_forces_reembed_even_when_content_unchanged(
    memory_store: MemoryStore, fake_provider
) -> None:
    cfg = TaskSourceConfig(enabled=True, reindex_on_status=["done", "cancelled"])
    source = TaskSource(store=memory_store, cfg=cfg)
    await source.observe_task({"action": "create", "task": _task()})
    first = fake_provider.total_embed_rows
    await source.observe_task(
        {
            "action": "update",
            "task": _task(status="done"),
            "previous_status": "in_progress",
        }
    )
    assert fake_provider.total_embed_rows > first


@pytest.mark.asyncio
async def test_delete_action_soft_deletes_document(
    memory_store: MemoryStore,
) -> None:
    cfg = TaskSourceConfig(enabled=True)
    source = TaskSource(store=memory_store, cfg=cfg)
    await source.observe_task({"action": "create", "task": _task()})
    await source.observe_task({"action": "delete", "task": _task()})
    conn = memory_store._conn  # type: ignore[attr-defined]
    async with conn.execute(
        "SELECT deleted_at FROM memory_documents "
        "WHERE source_type=? AND source_id=?",
        (SOURCE_TYPE, "task-1"),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] is not None


@pytest.mark.asyncio
async def test_disabled_source_is_noop(
    memory_store: MemoryStore, fake_provider
) -> None:
    cfg = TaskSourceConfig(enabled=False)
    source = TaskSource(store=memory_store, cfg=cfg)
    await source.observe_task({"action": "create", "task": _task()})
    assert fake_provider.total_embed_rows == 0
