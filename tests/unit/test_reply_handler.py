"""Tests for the full ReplyHandler pipeline (mocked LLM)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.replies.llm_classifier import LLMClassifier


@pytest.fixture
async def classifier_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL, task_id TEXT,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)"
    )
    await conn.commit()
    yield conn
    await conn.close()


def _mock_router(actions: list, reply: str, reasoning: str = "test") -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(return_value=(
        {"reasoning": reasoning, "actions": actions, "reply_to_user": reply},
        MagicMock(),
    ))
    return router


def _mock_task() -> MagicMock:
    t = MagicMock()
    t.id = "t-1"
    t.title = "Build thing"
    t.status = "scheduled"
    t.domain = "work"
    t.priority = 2
    t.scheduled_start = "2026-05-12T09:00:00+00:00"
    t.estimated_duration = 30
    t.nudge_count = 0
    t.reschedule_count = 0
    return t


@pytest.mark.asyncio
async def test_classify_returns_actions_and_reply(classifier_db: aiosqlite.Connection) -> None:
    from donna.config import ReplyActionsConfig, ReplyMemoryConfig, ReplyPlanConfig, ActionDef, ActionParamDef
    from donna.replies.action_registry import ActionRegistry
    from donna.replies.memory import ThreadMemory

    config = ReplyActionsConfig(
        memory=ReplyMemoryConfig(), plan=ReplyPlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
        },
    )
    registry = ActionRegistry(config)
    memory = ThreadMemory(classifier_db)
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark 'Build thing' as done. Sound good?",
    )

    classifier = LLMClassifier(router=router, registry=registry, memory=memory)
    result = await classifier.classify(
        thread_id="thread-1",
        user_reply="I finished it earlier today",
        task=_mock_task(),
        context_type="overdue",
    )

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "mark_done"
    assert "mark" in result["reply_to_user"].lower() or "done" in result["reply_to_user"].lower()
    router.complete.assert_called_once()


@pytest.mark.asyncio
async def test_classify_strips_invalid_actions(classifier_db: aiosqlite.Connection) -> None:
    from donna.config import ReplyActionsConfig, ReplyMemoryConfig, ReplyPlanConfig, ActionDef, ActionParamDef
    from donna.replies.action_registry import ActionRegistry
    from donna.replies.memory import ThreadMemory

    config = ReplyActionsConfig(
        memory=ReplyMemoryConfig(), plan=ReplyPlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
        },
    )
    registry = ActionRegistry(config)
    memory = ThreadMemory(classifier_db)
    router = _mock_router(
        actions=[
            {"action": "mark_done", "params": {}},
            {"action": "fly_to_moon", "params": {}},
        ],
        reply="reply text",
    )

    classifier = LLMClassifier(router=router, registry=registry, memory=memory)
    result = await classifier.classify(
        thread_id="thread-1",
        user_reply="done and fly me to the moon",
        task=_mock_task(),
        context_type="overdue",
    )

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "mark_done"
