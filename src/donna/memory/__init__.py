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
- :mod:`donna.memory.sources_chat` — ``ChatSource`` indexes turns
  from ``conversation_messages`` (slice 14).
- :mod:`donna.memory.sources_task` — ``TaskSource`` indexes task
  mutations on create/update (slice 14).
- :mod:`donna.memory.sources_correction` — ``CorrectionSource``
  indexes correction-log rows (slice 14).
- :mod:`donna.memory.observers` — module-level observer registry
  used by ``correction_logger`` (slice 14).

See ``slices/slice_13_memory_store_vault_source.md``,
``slices/slice_14_episodic_sources.md``, and
``docs/reference-specs/memory-vault-spec.md``.
"""
