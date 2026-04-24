"""Frontmatter is preserved on overwrite when the new content omits it."""
from __future__ import annotations

import pytest

from donna.integrations.vault import VaultWriter


@pytest.mark.asyncio
async def test_existing_frontmatter_survives_body_only_overwrite(
    writer: VaultWriter,
) -> None:
    original = "---\ntitle: Hello\ntags:\n  - meeting\n---\n# old body\n"
    await writer.write("Meetings/m1.md", original)

    # New content has no frontmatter block — the old frontmatter must remain.
    await writer.write("Meetings/m1.md", "# new body\n")

    note = await writer._client.read("Meetings/m1.md")
    assert note.frontmatter == {"title": "Hello", "tags": ["meeting"]}
    assert note.content.strip() == "# new body"


@pytest.mark.asyncio
async def test_new_frontmatter_overrides_matching_keys_only(
    writer: VaultWriter,
) -> None:
    original = "---\ntitle: Old\nauthor: Nick\n---\nbody\n"
    await writer.write("Meetings/m2.md", original)

    # Overwrite sets title but not author: author must survive.
    incoming = "---\ntitle: New\n---\nbody2\n"
    await writer.write("Meetings/m2.md", incoming)

    note = await writer._client.read("Meetings/m2.md")
    assert note.frontmatter["title"] == "New"
    assert note.frontmatter["author"] == "Nick"


@pytest.mark.asyncio
async def test_sensitive_note_refuses_overwrite(writer: VaultWriter) -> None:
    original = "---\ndonna_sensitive: true\n---\nsecret\n"
    await writer.write("Inbox/secret.md", original)

    with pytest.raises(Exception) as exc:
        await writer.write("Inbox/secret.md", "leak")
    assert getattr(exc.value, "reason", None) == "sensitive"
