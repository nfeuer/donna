"""Slice 16 — :func:`donna.memory.person_stub.ensure_person_stubs`.

Covers:

- Bare ``[[Name]]`` wikilinks produce stubs; namespaced / aliased /
  heading variants are ignored.
- Existing ``People/{name}.md`` notes are never overwritten.
- ``People`` absent from the safety allowlist → no writes at all.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.integrations.vault import VaultReadError
from donna.memory.person_stub import _extract_bare_names, ensure_person_stubs


def test_extract_bare_names_filters_namespaced_and_alias_and_heading() -> None:
    body = (
        "Attendees: [[People/Alice]], [[Bob]], [[Carol|C]], [[Dave#Notes]], "
        "[[Eve]], [[Projects/X]], [[Bob]]\n"
    )
    names = _extract_bare_names(body)
    # Dedup preserving first-seen order. People/… and aliased/headings
    # excluded.
    assert names == ["Bob", "Eve"]


@pytest.mark.asyncio
async def test_no_people_in_allowlist_is_noop() -> None:
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock()
    vault_client = MagicMock()
    vault_client.stat = AsyncMock()

    created = await ensure_person_stubs(
        "link to [[Alice]]",
        vault_writer=vault_writer,
        vault_client=vault_client,
        safety_allowlist=["Inbox", "Meetings"],
    )

    assert created == []
    vault_writer.write.assert_not_called()
    vault_client.stat.assert_not_called()


@pytest.mark.asyncio
async def test_existing_note_is_not_overwritten() -> None:
    vault_client = MagicMock()
    # stat succeeds → file exists.
    vault_client.stat = AsyncMock(return_value=(1_700_000.0, 100))
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock()

    created = await ensure_person_stubs(
        "hello [[Alice]]",
        vault_writer=vault_writer,
        vault_client=vault_client,
        safety_allowlist=["People"],
    )

    assert created == []
    vault_writer.write.assert_not_called()


@pytest.mark.asyncio
async def test_missing_people_are_created_with_stub_body() -> None:
    vault_client = MagicMock()
    # stat raises "missing:" for both Alice and Bob.
    vault_client.stat = AsyncMock(
        side_effect=VaultReadError("missing: People/X.md")
    )
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock()

    created = await ensure_person_stubs(
        "intro: [[Alice]] and [[Bob]]",
        vault_writer=vault_writer,
        vault_client=vault_client,
        safety_allowlist=["People", "Meetings"],
    )

    assert sorted(created) == ["Alice", "Bob"]
    assert vault_writer.write.await_count == 2
    # Each call has the correct path + stub body markers.
    paths = {call.args[0] for call in vault_writer.write.await_args_list}
    assert paths == {"People/Alice.md", "People/Bob.md"}
    for call in vault_writer.write.await_args_list:
        body = call.args[1]
        assert "autowritten_by: donna" in body
        assert "stub: true" in body
        assert call.kwargs["message"].startswith("autowrite: person_stub ")


@pytest.mark.asyncio
async def test_non_missing_read_error_propagates() -> None:
    vault_client = MagicMock()
    vault_client.stat = AsyncMock(
        side_effect=VaultReadError("path_escape: bad")
    )
    vault_writer = MagicMock()
    vault_writer.write = AsyncMock()

    with pytest.raises(VaultReadError):
        await ensure_person_stubs(
            "[[Alice]]",
            vault_writer=vault_writer,
            vault_client=vault_client,
            safety_allowlist=["People"],
        )
    vault_writer.write.assert_not_called()
