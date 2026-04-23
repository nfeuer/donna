# Slice 13: Memory Store, Vector Index, Vault Source

> **Goal:** Turn the vault from Slice 12 into a semantic index. Add a `MemoryStore` backed by sqlite-vec inside `donna_tasks.db`, an `EmbeddingProvider` abstraction (default: the existing MiniLM-L6-v2 from `src/donna/capabilities/embeddings.py`), a markdown-aware chunker, and a `VaultSource` that watches the vault directory and keeps chunks in sync with the files on disk. Expose `memory_search` as an agent tool returning provenance-tagged `RetrievedChunk`s. Vault is the only source wired in this slice — chat / task / correction sources land in Slice 14.

## BLOCKER — resolve before starting

Parent plan §14 #2. MiniLM-L6-v2's effective sequence length is **256 tokens**; content beyond is silently truncated by the model. This slice commits to one of:

- **(a)** Stay on MiniLM with `max_tokens: 256 / overlap: 32` chunking — default in this brief.
- **(b)** Swap to `bge-small-en-v1.5` (512 window, 384-dim, drop-in).
- **(c)** Cloud embedding (Voyage-3-lite) — out of scope for V1.

Assume (a) unless Nick picks otherwise. If (b) is chosen, update `EmbeddingProvider` default + chunker config + `embedding.version_tag` before the migration is squashed onto `main`.

## Relevant Docs

- `CLAUDE.md` (always)
- `spec_v3.md §1.3, §4, §4.3, §14, §16.1, §16.4` — model abstraction, invocation logging, observability, DB strategy, data classification
- `docs/reference-specs/memory-vault-spec.md` — full design
- `docs/domain/memory-vault.md` — narrative
- `docs/domain/model-layer.md` — router / provider pattern the `EmbeddingProvider` mirrors
- `slices/slice_12_vault_plumbing.md` — upstream dependencies (`VaultClient`, `VaultConfig`, path layout)

## What to Build

1. **Dependencies** (`pyproject.toml`):
   - Add `sqlite-vec>=0.1.6`, `watchfiles>=0.21`, `python-frontmatter>=1.1`.
   - Run `uv sync`; commit updated lock file.

2. **SQLite extension load** (`src/donna/tasks/database.py:connect()`):
   - Before any schema access: `conn.enable_load_extension(True)`; load `vec0` via `sqlite_vec.load(conn)`.
   - Handle environments without the extension (dev machines without the wheel installed) — log a structured warning and disable memory features instead of crashing.

3. **Alembic migration** `alembic/versions/<rev>_add_memory_and_vault.py`:
   - `memory_documents` table (full column list in parent plan §3.1).
   - `memory_chunks` table (parent plan §3.2).
   - `vec_memory_chunks` virtual table: `USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[384])`.
   - Indexes: `UNIQUE(user_id, source_type, source_id)`, `(user_id, updated_at)`, `(user_id, deleted_at)`, `(document_id)`, `(user_id, embedding_version)`.
   - `op.get_bind().connection.enable_load_extension(True)` + load before `CREATE VIRTUAL TABLE`.
   - Downgrade drops all three tables in reverse order.

4. **Memory config extensions** (`config/memory.yaml` + `src/donna/config.py`):
   - Populate the `embedding`, `retrieval`, and `sources.vault` blocks left as stubs in Slice 12.
   - Pydantic models: `EmbeddingConfig(provider, version_tag, dim, max_tokens)`, `RetrievalConfig(default_k, min_score, max_k)`, `VaultSourceConfig(enabled, chunker, ignore_globs)`.

5. **EmbeddingProvider Protocol** (`src/donna/memory/embeddings.py`):
   - `Protocol` with `name: str`, `dim: int`, `max_tokens: int`, `async embed(text)`, `async embed_batch(texts)`.
   - `MiniLMProvider` wraps `src/donna/capabilities/embeddings.py:embed_text` — reuse that module, do not duplicate model-load logic.
   - Factory: `build_embedding_provider(cfg: EmbeddingConfig) -> EmbeddingProvider`; raise on unknown provider.

6. **Chunker** (`src/donna/memory/chunking.py`):
   - `Chunker` Protocol.
   - `MarkdownHeadingChunker(max_tokens=256, overlap_tokens=32, min_tokens=32)`: splits on H1/H2/H3, records `heading_path` stack, merges sub-`min_tokens` sections forward, keeps code fences intact where feasible.
   - Token counting: use a single tokenizer across the codebase (reuse whatever `src/donna/models/` settled on; if none, use `tiktoken` with a neutral encoding). Document the choice in a one-line module docstring.

7. **MemoryStore** (`src/donna/memory/store.py`):
   - Methods: `put`, `upsert`, `delete`, `reindex`, `search` (parent plan §2.3).
   - `upsert` short-circuits re-embed when `content_hash` unchanged.
   - `delete` is soft (`deleted_at = now`); search filters `WHERE deleted_at IS NULL`.
   - `search` runs the join from parent plan §5 and returns `RetrievedChunk` with provenance.
   - Every embed call writes one `invocation_log` row via the existing `InvocationLogger` (`task_type` per parent plan §10).

8. **Ingest queue** (`src/donna/memory/queue.py`):
   - Plain `asyncio.Queue` + a batched worker that drains up to N events, calls `embed_batch`, then one `MemoryStore.upsert` transaction.
   - Does **not** reuse `src/donna/llm/queue.py` (that is a token-budgeted LLM dispatcher).

9. **VaultSource** (`src/donna/memory/sources_vault.py`):
   - `watchfiles.awatch(vault_root)` with 500 ms coalesce.
   - On add/modify: read via `VaultClient`, split frontmatter, chunk, upsert.
   - On delete: `MemoryStore.delete(source_type="vault", source_id=<rel path>)`. Rename = delete + upsert (more robust rename handling is Slice 16).
   - `backfill(user_id)` on startup: walk vault root, upsert every path whose mtime > stored `updated_at` (or absent). Honors `sources.vault.ignore_globs`.
   - Respects `donna: local-only` frontmatter → `Document.sensitive = True`.

10. **`memory_search` tool** (`src/donna/skills/tools/memory_search.py`):
    - Signature matches parent plan §6 (`query`, `user_id`, `k`, `sources`, `filters`).
    - Registered in `skills/tools/__init__.py:register_default_tools()`, gated on a `memory_store` kwarg.
    - Add to `pm`, `research`, `challenger`, `scheduler` `allowed_tools` in `config/agents.yaml`.

11. **Wiring** (`src/donna/cli_wiring.py`):
    - `_try_build_memory_store()` — non-fatal: if `sqlite-vec` unavailable or embedding provider fails to init, return `None` and log a warning.
    - Start the `VaultSource` watcher + backfill task as part of the main async run loop.
    - Pass `memory_store` into `register_default_tools`.

12. **Observability:**
    - Invocation log: `task_type in {embed_memory_query, embed_vault_chunk}`, `model_alias="minilm-l6-v2"`, `tokens_in=0`, `tokens_out=0`, `cost_usd=0.0`.
    - Structlog events: `memory_retrieval`, `memory_ingest_batch`, `vault_watch_event`.
    - Add a `memory` dashboard JSON under `docker/grafana/provisioning/dashboards/` showing retrieval latency histogram, chunks-total gauge (by source_type), reembed counter.

13. **Fixtures** (`fixtures/vault/`):
    - ~20 sample `.md` notes covering nested headings, code fences, wikilinks, frontmatter, `donna: local-only` sensitive flag, minimal notes. Parent plan §11.4 lists specific paths — use them verbatim.

14. **Tests:**
    - Unit: `test_markdown_heading_chunker.py` (nested headings, 256-token cap, overlap, code-fence integrity); `test_embedding_provider.py` (Protocol conformance, fake provider for speed); `test_memory_store_upsert.py` (content-hash short-circuit, soft-delete, sensitive flag); `test_sqlite_vec_migration.py` (upgrade/downgrade with extension).
    - Integration: `test_vault_source_roundtrip.py` — write a note → watcher → chunks embedded → `memory_search` returns the chunk with correct `source_path` + `heading_path`.
    - **Performance benchmark:** `test_memory_search_latency.py` — seed 10 000 fake-embedded chunks, assert p50 retrieval < 100 ms.
    - Most unit tests use a deterministic hash-based fake `EmbeddingProvider`. Only `test_vault_source_roundtrip.py` loads the real MiniLM model (behind a `@pytest.mark.slow` marker).

## Acceptance Criteria

- [ ] `uv sync` installs `sqlite-vec`, `watchfiles`, `python-frontmatter`; lock file committed
- [ ] `Database.connect()` loads `vec0` and degrades gracefully if the extension is absent (warn + disable, no crash)
- [ ] `alembic upgrade head` creates `memory_documents`, `memory_chunks`, `vec_memory_chunks` with all indexes; `alembic downgrade -1` reverses cleanly
- [ ] `EmbeddingProvider` Protocol is honored by `MiniLMProvider` and by the test fake; `build_embedding_provider()` raises on unknown `provider`
- [ ] `MarkdownHeadingChunker` respects `max_tokens=256`, 32-token overlap, preserves `heading_path`, never splits inside a fenced code block when the block fits in one chunk
- [ ] `MemoryStore.upsert` skips re-embedding when `content_hash` is unchanged (verified by invocation_log row count)
- [ ] `MemoryStore.delete` sets `deleted_at` and excludes those rows from `search` results
- [ ] `VaultSource.backfill` indexes every `.md` under vault root (minus `ignore_globs`) in under 30 s for the 20-note fixture
- [ ] `watchfiles` picks up a manual edit within 1 s of the change; `memory_search` reflects the new content on the next query
- [ ] `Document.sensitive = True` when frontmatter contains `donna: local-only`; the flag surfaces on `RetrievedChunk.metadata`
- [ ] `memory_search` returns `RetrievedChunk`s with correct `source_path`, `heading_path`, and `score`; respects `sources`, `min_score`, and `filters` kwargs
- [ ] `memory_search` retrieval p50 < 100 ms at 10 000 chunks (benchmark test)
- [ ] Every embed call writes one `invocation_log` row with the expected `task_type` and `model_alias`
- [ ] `pm`, `research`, `challenger`, `scheduler` can successfully invoke `memory_search` end-to-end through the existing tool registry + agent allowlist path
- [ ] Grafana dashboard JSON renders without error under the existing provisioning path
- [ ] `pytest tests/unit/memory tests/integration/test_vault_source_roundtrip.py tests/integration/test_memory_search_latency.py` passes

## Not in Scope

- **No `ChatSource`, `TaskSource`, `CorrectionSource`.** Slice 14.
- **No Jinja templates under `prompts/vault/`.** Slice 15.
- **No agent skill that *writes* memory-informed notes (meeting notes, weekly reviews).** Slice 15.
- **No Supabase sync** for `memory_documents` / `memory_chunks`. Slice 16.
- **No rename/move reconciliation beyond delete+upsert.** Slice 16.
- **No BM25 / hybrid retrieval.** Slice 17.
- **No retrieval quality gate / eval harness.** Slice 17.
- **No Obsidian MCP bridge.** Indefinite defer.
- **No attachment indexing** (images, PDFs). V1 is `.md` only.
- **No cloud embedding provider.** Architecture supports it; not wired.

## Session Context

Load only: `CLAUDE.md`, this slice brief, `slices/slice_12_vault_plumbing.md`, the parent plan at `/root/.claude/plans/what-are-some-additional-transient-truffle.md`, `spec_v3.md §1.3 / §4 / §4.3 / §14 / §16.1 / §16.4`, `docs/domain/memory-vault.md`, `docs/domain/model-layer.md`, `docs/reference-specs/memory-vault-spec.md`, and the existing `src/donna/capabilities/embeddings.py` (the module being reused for MiniLM).

## Handoff to Slice 14

Slice 14 consumes: a stable `MemoryStore.upsert` contract; `MemorySource` Protocol and ingest queue pattern to copy for chat/task/correction sources; invocation_log `task_type` naming already registered in Grafana; and a fixture set that `test_memory_e2e.py` can extend. No schema changes expected in Slice 14 — the `memory_documents` / `memory_chunks` tables already accommodate the other three source types.
