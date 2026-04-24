"""Semantic memory layer (slice 13).

Pieces:

- :mod:`donna.memory.embeddings` — ``EmbeddingProvider`` Protocol +
  ``MiniLMProvider`` wrapper around
  :mod:`donna.capabilities.embeddings`.
- :mod:`donna.memory.chunking` — ``MarkdownHeadingChunker`` emits 256-
  token chunks with ``heading_path`` provenance.
- :mod:`donna.memory.store` — ``MemoryStore`` backed by sqlite-vec
  inside ``donna_tasks.db``.
- :mod:`donna.memory.queue` — batched async ingest worker.
- :mod:`donna.memory.sources_vault` — ``VaultSource`` watches the
  Obsidian vault and keeps chunks in sync.

See ``slices/slice_13_memory_store_vault_source.md`` and
``docs/reference-specs/memory-vault-spec.md``.
"""
