"""Slice 15 — autonomy-based path redirection in :class:`MemoryInformedWriter`.

``autonomy_level="low"`` forces the write into ``Inbox/{basename}``
regardless of the caller-computed ``target_path``. ``medium`` / ``high``
honour the original path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.integrations.vault import VaultReadError
from donna.memory.writer import MemoryInformedWriter


def _build_writer(render_fm: dict | None = None):
    vault_client = MagicMock()
    # File does not exist → first-write path, no idempotent skip.
    vault_client.read = AsyncMock(
        side_effect=VaultReadError("missing: Meetings/x.md")
    )
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock(return_value="c" * 40)

    router = MagicMock()
    router.get_prompt_template = MagicMock(return_value="p: {{ x }}")
    router.complete = AsyncMock(
        return_value=(
            {
                "summary": "s",
                "action_item_candidates": [],
                "open_questions": [],
                "links_suggested": [],
            },
            object(),
        )
    )

    renderer = MagicMock()
    renderer.render = MagicMock(
        return_value=("# body\n", render_fm or {"idempotency_key": "E1"})
    )
    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,
        logger=MagicMock(),
    )
    return writer, vault_writer


@pytest.mark.asyncio
async def test_low_autonomy_redirects_to_inbox() -> None:
    writer, vault_writer = _build_writer()

    async def gather() -> dict:
        return {"x": "v"}

    result = await writer.run(
        template="meeting_note.md.j2",
        task_type="draft_meeting_note",
        context_gather=gather,
        target_path="Meetings/2026-04-24-sync.md",
        idempotency_key="E1",
        user_id="nick",
        autonomy_level="low",
    )

    assert result.skipped is False
    # First positional arg is the path; slice §2 keyword-agnostic here.
    written_path = vault_writer.write.call_args.args[0]
    assert written_path == "Inbox/2026-04-24-sync.md"
    assert result.path == "Inbox/2026-04-24-sync.md"


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["medium", "high"])
async def test_non_low_autonomy_honors_target_path(level: str) -> None:
    writer, vault_writer = _build_writer()

    async def gather() -> dict:
        return {"x": "v"}

    result = await writer.run(
        template="meeting_note.md.j2",
        task_type="draft_meeting_note",
        context_gather=gather,
        target_path="Meetings/2026-04-24-sync.md",
        idempotency_key="E1",
        user_id="nick",
        autonomy_level=level,
    )

    assert result.skipped is False
    written_path = vault_writer.write.call_args.args[0]
    assert written_path == "Meetings/2026-04-24-sync.md"
    assert result.path == "Meetings/2026-04-24-sync.md"
