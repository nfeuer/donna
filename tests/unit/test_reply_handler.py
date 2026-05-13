"""Tests for the full ReplyHandler pipeline (mocked LLM)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    ActionDef,
    ActionParamDef,
    FastPathConfig,
    ReplyActionsConfig,
    ReplyIntentDef,
    ReplyIntentsConfig,
    ReplyMemoryConfig,
    ReplyPlanConfig,
)
from donna.replies.action_registry import ActionRegistry
from donna.replies.handler import ReplyHandler
from donna.replies.llm_classifier import LLMClassifier
from donna.replies.memory import ThreadMemory


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


# --- Full pipeline tests ---


def _intents_config() -> ReplyIntentsConfig:
    return ReplyIntentsConfig(
        fast_path=FastPathConfig(
            max_length=60,
            multi_intent_signals=[" but ", " and also ", " however "],
            confirm_keywords=["yes", "go ahead"],
            reject_keywords=["no", "cancel"],
        ),
        intents={
            "mark_done": ReplyIntentDef(keywords=["done", "finished"], action="mark_done"),
            "reschedule": ReplyIntentDef(keywords=["reschedule", "tomorrow"], action="reschedule"),
        },
    )


def _actions_config() -> ReplyActionsConfig:
    return ReplyActionsConfig(
        memory=ReplyMemoryConfig(window_size=10),
        plan=ReplyPlanConfig(expiry_minutes=60),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
            "reschedule": ActionDef(
                description="Reschedule",
                handler="donna.replies.actions.task_actions.reschedule_task",
                params={
                    "task_id": ActionParamDef(type="string", from_context=True),
                    "when": ActionParamDef(type="string", optional=True),
                },
            ),
        },
    )


@pytest.fixture
async def handler_db():
    conn = await aiosqlite.connect(":memory:")
    for sql in [
        """CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL, task_id TEXT,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)",
        """CREATE TABLE pending_action_plan (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            actions_json TEXT NOT NULL, reply_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_pending_plan_thread ON pending_action_plan(thread_id, status)",
    ]:
        await conn.execute(sql)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_fast_path_returns_immediate(handler_db: aiosqlite.Connection) -> None:
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=MagicMock(),
        db=AsyncMock(),
        context={},
    )
    result = await handler.handle("thread-1", "done", _mock_task(), "overdue")
    assert result.path == "fast"
    assert result.action == "mark_done"


@pytest.mark.asyncio
async def test_complex_reply_routes_to_llm(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark it done. Sound good?",
    )
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=AsyncMock(),
        context={},
    )
    result = await handler.handle(
        "thread-1",
        "I finished it earlier today and also need to call Mike",
        _mock_task(),
        "overdue",
    )
    assert result.path == "llm"
    assert result.pending_plan_id is not None


@pytest.mark.asyncio
async def test_confirm_executes_pending_plan(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark it done. Sound good?",
    )
    mock_db = AsyncMock()
    mock_task_obj = _mock_task()
    mock_db.get_task = AsyncMock(return_value=mock_task_obj)
    mock_db.transition_task_state = AsyncMock()
    mock_db.update_task = AsyncMock()

    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=mock_db,
        context={},
    )
    # First: LLM proposes (reply must trigger LLM path, not fast path)
    complex_reply = "I finished it but also need to update the docs"
    await handler.handle("thread-1", complex_reply, mock_task_obj, "overdue")
    # Second: user confirms
    result = await handler.handle("thread-1", "yes", mock_task_obj, "overdue")
    assert result.path == "plan_confirmed"


@pytest.mark.asyncio
async def test_reject_clears_pending_plan(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="plan reply",
    )
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=AsyncMock(),
        context={},
    )
    await handler.handle(
        "thread-1", "I finished it but also need to update the docs",
        _mock_task(), "overdue",
    )
    result = await handler.handle(
        "thread-1", "no", _mock_task(), "overdue",
    )
    assert result.path == "plan_rejected"
