"""Unit test: episodic sources pass the right task_type to the provider."""
from __future__ import annotations

import pytest

from donna.config import ChatSourceConfig, CorrectionSourceConfig, TaskSourceConfig
from donna.memory.sources_chat import ChatSource
from donna.memory.sources_correction import CorrectionSource
from donna.memory.sources_task import TaskSource
from donna.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_task_source_passes_embed_task_task_type(
    memory_store: MemoryStore, fake_provider
) -> None:
    source = TaskSource(store=memory_store, cfg=TaskSourceConfig(enabled=True))
    await source.observe_task(
        {
            "action": "create",
            "task": {
                "id": "t1",
                "user_id": "nick",
                "title": "Test task",
                "status": "backlog",
                "notes": [],
            },
        }
    )
    assert fake_provider.last_task_type == "embed_task"


@pytest.mark.asyncio
async def test_chat_source_passes_embed_chat_turn_task_type(
    memory_store: MemoryStore, fake_provider
) -> None:
    source = ChatSource(
        store=memory_store, cfg=ChatSourceConfig(enabled=True, min_chars=1),
    )
    base = {"session_id": "S1", "user_id": "nick"}
    await source.observe_message(
        {**base, "message": {"id": "m1", "role": "user", "content": "hello world"}}
    )
    await source.observe_session_closed({**base, "status": "closed"})
    assert fake_provider.last_task_type == "embed_chat_turn"


@pytest.mark.asyncio
async def test_correction_source_passes_embed_correction_task_type(
    memory_store: MemoryStore, fake_provider
) -> None:
    source = CorrectionSource(
        store=memory_store, cfg=CorrectionSourceConfig(enabled=True),
    )
    await source.observe(
        {
            "id": "c1",
            "user_id": "nick",
            "task_type": "classify_priority",
            "task_id": "t1",
            "input_text": "urgent",
            "field_corrected": "priority",
            "original_value": "2",
            "corrected_value": "4",
        }
    )
    assert fake_provider.last_task_type == "embed_correction"
