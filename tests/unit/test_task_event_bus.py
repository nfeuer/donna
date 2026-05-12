"""Unit tests for the task lifecycle event bus."""

from __future__ import annotations

import pytest

from donna.tasks.events import TaskEventBus


@pytest.fixture
def bus() -> TaskEventBus:
    return TaskEventBus()


@pytest.mark.asyncio
async def test_subscribe_and_emit(bus: TaskEventBus) -> None:
    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_created", handler)
    await bus.emit("task_created", task="fake-task", source="discord")

    assert len(received) == 1
    assert received[0]["task"] == "fake-task"
    assert received[0]["source"] == "discord"


@pytest.mark.asyncio
async def test_emit_no_subscribers(bus: TaskEventBus) -> None:
    await bus.emit("task_created", task="fake-task")


@pytest.mark.asyncio
async def test_multiple_subscribers(bus: TaskEventBus) -> None:
    calls: list[str] = []

    async def handler_a(task, **ctx):
        calls.append("a")

    async def handler_b(task, **ctx):
        calls.append("b")

    bus.subscribe("task_created", handler_a)
    bus.subscribe("task_created", handler_b)
    await bus.emit("task_created", task="t")

    assert calls == ["a", "b"]


@pytest.mark.asyncio
async def test_subscriber_error_is_isolated(bus: TaskEventBus) -> None:
    calls: list[str] = []

    async def bad_handler(task, **ctx):
        raise RuntimeError("boom")

    async def good_handler(task, **ctx):
        calls.append("ok")

    bus.subscribe("task_created", bad_handler)
    bus.subscribe("task_created", good_handler)
    await bus.emit("task_created", task="t")

    assert calls == ["ok"]


@pytest.mark.asyncio
async def test_different_event_types(bus: TaskEventBus) -> None:
    created: list[str] = []
    changed: list[str] = []

    async def on_created(task, **ctx):
        created.append(task)

    async def on_changed(task, **ctx):
        changed.append(task)

    bus.subscribe("task_created", on_created)
    bus.subscribe("task_state_changed", on_changed)

    await bus.emit("task_created", task="t1")
    await bus.emit("task_state_changed", task="t2", old_status="backlog", new_status="scheduled")

    assert created == ["t1"]
    assert changed == ["t2"]
