"""Unit tests for MemoryStore upsert/delete/search semantics."""
from __future__ import annotations

import pytest

from donna.memory.store import Document, MemoryStore


def _vault_doc(content: str, *, sensitive: bool = False) -> Document:
    return Document(
        user_id="nick",
        source_type="vault",
        source_id="Inbox/test.md",
        title="Test",
        uri="vault:Inbox/test.md",
        content=content,
        sensitive=sensitive,
    )


@pytest.mark.asyncio
async def test_upsert_skips_reembed_when_hash_unchanged(
    memory_store: MemoryStore, fake_provider
) -> None:
    body = "# Alpha\n\nMemory store smoke test."
    id1 = await memory_store.upsert(_vault_doc(body))
    rows_before = fake_provider.total_embed_rows

    id2 = await memory_store.upsert(_vault_doc(body))
    assert id1 == id2
    # No additional embed calls should have been made.
    assert fake_provider.total_embed_rows == rows_before


@pytest.mark.asyncio
async def test_upsert_reembeds_when_content_changes(
    memory_store: MemoryStore, fake_provider
) -> None:
    await memory_store.upsert(_vault_doc("# Alpha\n\nFirst body."))
    rows_first = fake_provider.total_embed_rows
    await memory_store.upsert(_vault_doc("# Alpha\n\nCompletely new body."))
    assert fake_provider.total_embed_rows > rows_first


@pytest.mark.asyncio
async def test_delete_excludes_soft_deleted_docs_from_search(
    memory_store: MemoryStore,
) -> None:
    await memory_store.upsert(
        _vault_doc("# Alpha\n\nalpha beta gamma for retrieval"),
    )
    before = await memory_store.search(
        query="alpha beta", user_id="nick", k=5,
    )
    assert len(before) >= 1
    ok = await memory_store.delete(
        source_type="vault", source_id="Inbox/test.md", user_id="nick",
    )
    assert ok is True
    after = await memory_store.search(
        query="alpha beta", user_id="nick", k=5,
    )
    assert after == []


@pytest.mark.asyncio
async def test_sensitive_flag_surfaces_on_retrieved_chunk(
    memory_store: MemoryStore,
) -> None:
    await memory_store.upsert(
        _vault_doc("# S\n\nSecret contents that should be marked sensitive.",
                   sensitive=True),
    )
    hits = await memory_store.search(query="secret", user_id="nick", k=5)
    assert hits, "expected at least one hit for seeded doc"
    assert all(h.sensitive for h in hits)
    assert all(h.metadata.get("sensitive") is True for h in hits)


@pytest.mark.asyncio
async def test_put_rejects_existing(memory_store: MemoryStore) -> None:
    doc = _vault_doc("# A\n\nFirst.")
    await memory_store.put(doc)
    with pytest.raises(ValueError, match="already exists"):
        await memory_store.put(doc)


@pytest.mark.asyncio
async def test_search_respects_sources_filter(memory_store: MemoryStore) -> None:
    await memory_store.upsert(_vault_doc("# A\n\nvault body here."))
    other = Document(
        user_id="nick",
        source_type="task",
        source_id="task-1",
        title=None,
        uri=None,
        content="# T\n\ntask body here.",
    )
    await memory_store.upsert(other)
    hits = await memory_store.search(
        query="body here", user_id="nick", k=10, sources=["vault"],
    )
    assert hits
    assert all(h.source_type == "vault" for h in hits)


@pytest.mark.asyncio
async def test_search_respects_path_prefix_filter(memory_store: MemoryStore) -> None:
    await memory_store.upsert(_vault_doc("# A\n\nInbox content for match."))
    project_doc = Document(
        user_id="nick",
        source_type="vault",
        source_id="Projects/donna-memory/overview.md",
        title="Overview",
        uri="vault:Projects/donna-memory/overview.md",
        content="# O\n\nProject content for match.",
    )
    await memory_store.upsert(project_doc)
    hits = await memory_store.search(
        query="content for match",
        user_id="nick",
        k=10,
        filters={"path_prefix": "Projects/"},
    )
    assert hits
    assert all(h.source_path.startswith("Projects/") for h in hits)
