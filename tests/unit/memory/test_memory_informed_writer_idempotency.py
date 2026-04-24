"""Slice 15 — idempotency short-circuit in :class:`MemoryInformedWriter`.

Second call with the same ``idempotency_key`` must skip cleanly: no
write, no router call, structlog emits ``meeting_note_skipped_idempotent``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
import structlog.testing

from donna.integrations.vault import VaultNote
from donna.memory.writer import MemoryInformedWriter, WriteResult


def _configure_structlog_for_capture() -> None:
    """Ensure the logger emits plain dicts so capture_logs works reliably."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


@pytest.mark.asyncio
async def test_second_call_with_same_key_is_noop(tmp_path: Path) -> None:
    """First call writes; second call with the same key short-circuits."""
    existing = VaultNote(
        path="Meetings/2026-04-24-sync.md",
        content="# existing body",
        frontmatter={"idempotency_key": "E1", "type": "meeting"},
        mtime=1_000_000.0,
        size=123,
    )

    vault_client = MagicMock()
    vault_client.read = AsyncMock(return_value=existing)

    vault_writer = MagicMock()
    vault_writer.write = AsyncMock(return_value="sha-should-never-be-used")

    router = MagicMock()
    router.get_prompt_template = MagicMock(return_value="SHOULD NOT RENDER")
    router.complete = AsyncMock()

    renderer = MagicMock()
    renderer.render = MagicMock(return_value=("body", {"idempotency_key": "E1"}))

    invocation_logger = MagicMock()

    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,
        logger=invocation_logger,
    )

    context_gather = AsyncMock()

    with structlog.testing.capture_logs() as events:
        result = await writer.run(
            template="meeting_note.md.j2",
            task_type="draft_meeting_note",
            context_gather=context_gather,
            target_path="Meetings/2026-04-24-sync.md",
            idempotency_key="E1",
            user_id="nick",
            autonomy_level="medium",
        )

    assert isinstance(result, WriteResult)
    assert result.skipped is True
    assert result.reason == "idempotent"
    assert result.sha is None

    # Critically — no LLM spend, no context gather, no write.
    context_gather.assert_not_called()
    router.complete.assert_not_called()
    router.get_prompt_template.assert_not_called()
    vault_writer.write.assert_not_called()
    renderer.render.assert_not_called()

    skip_events = [e for e in events if e["event"] == "meeting_note_skipped_idempotent"]
    assert len(skip_events) == 1
    assert skip_events[0]["idempotency_key"] == "E1"
    assert skip_events[0]["path"] == "Meetings/2026-04-24-sync.md"


@pytest.mark.asyncio
async def test_first_write_when_key_differs_still_proceeds() -> None:
    """An existing note with a *different* key does not short-circuit."""
    existing = VaultNote(
        path="Meetings/2026-04-24-sync.md",
        content="# old",
        frontmatter={"idempotency_key": "E_old", "type": "meeting"},
        mtime=1_000_000.0,
        size=50,
    )

    vault_client = MagicMock()
    vault_client.read = AsyncMock(return_value=existing)

    vault_writer = MagicMock()
    vault_writer.write = AsyncMock(return_value="a" * 40)

    router = MagicMock()
    router.get_prompt_template = MagicMock(return_value="render me: {{ x }}")
    router.complete = AsyncMock(
        return_value=({"summary": "s", "action_item_candidates": [], "open_questions": [], "links_suggested": []}, object())
    )

    renderer = MagicMock()
    renderer.render = MagicMock(
        return_value=("# body\n", {"idempotency_key": "E_new", "type": "meeting"})
    )

    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,
        logger=MagicMock(),
    )

    async def gather() -> dict:
        return {"x": "v"}

    result = await writer.run(
        template="meeting_note.md.j2",
        task_type="draft_meeting_note",
        context_gather=gather,
        target_path="Meetings/2026-04-24-sync.md",
        idempotency_key="E_new",
        user_id="nick",
        autonomy_level="medium",
    )

    assert result.skipped is False
    assert result.sha == "a" * 40
    vault_writer.write.assert_awaited_once()
    # expected_mtime must be passed through when we are overwriting.
    _args, kwargs = vault_writer.write.call_args
    assert kwargs["expected_mtime"] == 1_000_000.0
    assert "autowrite: meeting_note.md.j2 E_new" in kwargs["message"]
