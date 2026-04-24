"""Slice 16 — :class:`PersonProfileSkill` overwrite guard.

User-edited ``People/{name}.md`` notes (body present, no
``autowritten_by: donna`` frontmatter) are NEVER re-rendered.
Empty notes and Donna-owned autowrites (stubs or previous weekly
refreshes) ARE re-rendered.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.capabilities.person_profile_skill import PersonProfileSkill
from donna.config import PersonProfileSkillConfig
from donna.integrations.vault import VaultNote, VaultReadError


def _mk_skill(vault_client: MagicMock) -> PersonProfileSkill:
    writer = MagicMock()
    writer.run = AsyncMock(
        return_value=MagicMock(skipped=False, sha="a" * 40, path="x", reason=None)
    )
    return PersonProfileSkill(
        writer=writer,
        memory_store=MagicMock(),
        vault_client=vault_client,
        mention_counter=MagicMock(),
        config=PersonProfileSkillConfig(autonomy_level="low"),
        user_id="nick",
    )


@pytest.mark.asyncio
async def test_user_edited_profile_is_not_overwritten() -> None:
    """A non-empty note without ``autowritten_by: donna`` is left alone."""
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        return_value=VaultNote(
            path="People/Alice.md",
            content="# Alice\n\nNick's hand-written notes.\n",
            frontmatter={"type": "person"},  # NO autowritten_by
            mtime=1.0,
            size=50,
        )
    )
    skill = _mk_skill(vault_client)

    result = await skill.run_for_person(
        "Alice", "mention_threshold", today=date(2026, 4, 24)
    )

    assert result.skipped is True
    assert result.reason == "user_owned"
    skill._writer.run.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_empty_note_is_refreshed() -> None:
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        return_value=VaultNote(
            path="People/Alice.md",
            content="",
            frontmatter={},
            mtime=1.0,
            size=0,
        )
    )
    skill = _mk_skill(vault_client)
    skill._gather_context = AsyncMock(return_value={})  # type: ignore[method-assign]

    await skill.run_for_person("Alice", "stub_fill", today=date(2026, 4, 24))

    skill._writer.run.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_donna_autowrite_is_refreshed() -> None:
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        return_value=VaultNote(
            path="People/Alice.md",
            content="# Alice\n\nprevious autowrite\n",
            frontmatter={"autowritten_by": "donna", "stub": True},
            mtime=1.0,
            size=30,
        )
    )
    skill = _mk_skill(vault_client)
    skill._gather_context = AsyncMock(return_value={})  # type: ignore[method-assign]

    await skill.run_for_person(
        "Alice", "mention_threshold", today=date(2026, 4, 24)
    )

    skill._writer.run.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_missing_note_is_created() -> None:
    vault_client = MagicMock()
    vault_client.read = AsyncMock(
        side_effect=VaultReadError("missing: People/Alice.md")
    )
    skill = _mk_skill(vault_client)
    skill._gather_context = AsyncMock(return_value={})  # type: ignore[method-assign]

    await skill.run_for_person(
        "Alice", "mention_threshold", today=date(2026, 4, 24)
    )

    skill._writer.run.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = skill._writer.run.call_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["idempotency_key"] == "Alice@2026-W17"
    assert kwargs["target_path"] == "People/Alice.md"
