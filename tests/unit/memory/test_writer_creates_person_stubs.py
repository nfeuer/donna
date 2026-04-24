"""Slice 16 — :class:`MemoryInformedWriter` invokes the person-stub hook.

The hook runs after a successful vault write, only for bare wikilinks
not already namespaced, and failures never propagate.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
import structlog.testing

from donna.integrations.vault import VaultReadError
from donna.memory.writer import MemoryInformedWriter


@pytest.mark.asyncio
async def test_writer_invokes_stub_helper_with_rendered_body() -> None:
    """Happy path: body has bare ``[[Alice]]`` → helper called, log emitted."""
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        side_effect=VaultReadError("missing: Meetings/x.md")
    )
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock(return_value="a" * 40)

    router = MagicMock()
    router.get_prompt_template = MagicMock(return_value="prompt: {{ x }}")
    router.complete = AsyncMock(return_value=({"summary": "ok"}, object()))

    renderer = MagicMock()
    # Body contains a bare wikilink → helper should see it.
    renderer.render = MagicMock(
        return_value=(
            "# Meeting\n\nWith [[Alice]] and [[People/Bob]].\n",
            {"idempotency_key": "K1", "type": "meeting"},
        )
    )

    stub_helper = AsyncMock(return_value=["Alice"])

    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,
        logger=MagicMock(),
        safety_allowlist=["Meetings", "People"],
        person_stub_helper=stub_helper,
    )

    with structlog.testing.capture_logs() as events:
        result = await writer.run(
            template="meeting_note.md.j2",
            task_type="draft_meeting_note",
            context_gather=AsyncMock(return_value={"x": "v"}),
            target_path="Meetings/x.md",
            idempotency_key="K1",
            user_id="nick",
            autonomy_level="medium",
        )

    assert result.skipped is False
    stub_helper.assert_awaited_once()
    call_kwargs = stub_helper.await_args.kwargs
    assert call_kwargs["safety_allowlist"] == ["Meetings", "People"]
    assert call_kwargs["vault_writer"] is vault_writer
    assert call_kwargs["vault_client"] is vault_client
    # Body (first positional) is the rendered text.
    assert "[[Alice]]" in stub_helper.await_args.args[0]

    stub_events = [e for e in events if e["event"] == "person_stubs_created"]
    assert len(stub_events) == 1
    assert stub_events[0]["names"] == ["Alice"]
    assert stub_events[0]["count"] == 1
    assert stub_events[0]["source_template"] == "meeting_note.md.j2"


@pytest.mark.asyncio
async def test_stub_helper_failure_does_not_propagate() -> None:
    """A raising helper logs ``person_stub_failed`` and returns a WriteResult."""
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        side_effect=VaultReadError("missing: Meetings/y.md")
    )
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock(return_value="b" * 40)

    router = MagicMock()
    router.get_prompt_template = MagicMock(return_value="p")
    router.complete = AsyncMock(return_value=({"ok": True}, object()))

    renderer = MagicMock()
    renderer.render = MagicMock(
        return_value=("body [[X]]", {"idempotency_key": "K2"})
    )

    stub_helper = AsyncMock(side_effect=RuntimeError("disk full"))

    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,
        logger=MagicMock(),
        safety_allowlist=["Meetings", "People"],
        person_stub_helper=stub_helper,
    )

    with structlog.testing.capture_logs() as events:
        result = await writer.run(
            template="t.md.j2",
            task_type="draft_meeting_note",
            context_gather=AsyncMock(return_value={}),
            target_path="Meetings/y.md",
            idempotency_key="K2",
            user_id="nick",
            autonomy_level="medium",
        )

    # Writer still succeeded.
    assert result.skipped is False
    assert result.sha == "b" * 40

    failed = [e for e in events if e["event"] == "person_stub_failed"]
    assert len(failed) == 1
    assert "disk full" in failed[0]["reason"]
