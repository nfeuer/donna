# Semantic Memory (Slice 13)

The memory layer lives inside the existing `donna_tasks.db` file (`spec_v3.md §16.1`). Three tables are added:

- `memory_documents` — one row per ingested source (a vault note today; chat turns, tasks, and corrections land in slice 14). `(user_id, source_type, source_id)` is unique. Soft-deleted via `deleted_at` so search joins can filter without pruning the ANN index on every tombstone.
- `memory_chunks` — one row per chunk emitted by the chunker. Carries `content`, `token_count`, and a JSON-encoded `heading_path` stack (e.g. `["ProjectPlan", "Design", "Schema"]`) so retrieval answers can cite a note's section header, not just the file path.
- `vec_memory_chunks` — the sqlite-vec `vec0` virtual table. Declared as `(chunk_id TEXT PRIMARY KEY, embedding FLOAT[384])`. Loaded on the shared aiosqlite connection in `Database.connect()`; if the extension wheel is missing the connection still opens and `vec_available` flips to `False`.

## Ingestion path

1. `VaultSource.watch()` — a `watchfiles.awatch` loop (500 ms coalesce) fires `vault_watch_event` for every `.md` change under the vault root, honoring `sources.vault.ignore_globs` plus the vault-wide `vault.ignore_globs`. Deletes translate to soft-delete; adds / modifies route to `_ingest_path`.
2. `VaultSource.backfill(user_id)` — walks the vault via `VaultClient.list(recursive=True)` on boot, compares each file's mtime against the stored `memory_documents.updated_at`, and enqueues anything newer-on-disk. Typical 20-note vault backfills in well under 30 s.
3. `_ingest_path` builds a `Document` carrying `user_id`, `source_type="vault"`, the relative path as `source_id`, the frontmatter title (or filename stem), the `vault:<rel>` URI, and the note body. `donna: local-only` (or `donna_sensitive: true`) in frontmatter flips `sensitive=True`, which propagates to every `RetrievedChunk.metadata["sensitive"]` for downstream prompt-building decisions.
4. `MemoryIngestQueue.run_forever()` drains up to 16 docs per 500 ms window into a single `MemoryStore.upsert_many` call — so `embed_batch` fires once per flush, amortising the SentenceTransformer warm-up over the batch.

## Re-ingest short-circuit

`MemoryStore.upsert(doc)` hashes `doc.content` to `content_hash`. If the existing row matches, we bump `updated_at`, clear `deleted_at`, refresh `title` / `metadata` / `sensitive`, and return without re-embedding. The `invocation_log` row count is the dedup signal: unchanged notes do not add rows for `task_type=embed_vault_chunk`.

## Retrieval

`MemoryStore.search(query, user_id, k, sources, filters)` embeds the query (one invocation with `task_type=embed_memory_query`) and runs a single three-table join — `vec_memory_chunks` (ANN window of `k*4`), `memory_chunks` (content + heading path), `memory_documents` (provenance, sensitivity, soft-delete filter). Scores use MiniLM's unit-normalised outputs: `score = 1 - distance² / 2` (sqlite-vec's `vec0` returns L2 distance). Results below `retrieval.min_score` are dropped; `k` is clamped to `retrieval.max_k`. A structlog `memory_retrieval` event records `k`, hits, sources, and `latency_ms` per call.

## Embedding contract

The default provider is `MiniLMProvider` (384-dim, 256-token window, BERT WordPiece tokenizer). Every `embed` / `embed_batch` emits one `invocation_log` row per input text — `model_alias="minilm-l6-v2"`, `tokens_in=0`, `cost_usd=0.0` — so the Grafana *Memory Vault* dashboard (`docker/grafana/dashboards/memory.json`) tracks embed volume alongside the normal LLM cost panels. Swapping to another provider (for example `bge-small-en-v1.5` or a cloud embedding) is a config-only change in `embedding.provider` plus a `build_embedding_provider` factory branch.

Token counting uses `tiktoken cl100k_base` when the encoding file is available and falls back to a deterministic word+punct heuristic when it isn't (offline CI). The fallback is within ~10% of WordPiece on English prose and typically over-counts, so we err on smaller chunks rather than silent truncation inside the encoder.

## Config

`config/memory.yaml` carries the tunables (`embedding.{provider,version_tag,dim,max_tokens,chunk_overlap}`, `retrieval.{default_k,min_score,max_k}`, `sources.vault.{enabled,chunker,ignore_globs}`). Pydantic aliases keep the slice-12 field names parseable so old configs still boot.

## Fixtures

`tests/fixtures/vault/` carries ~18 sample notes spanning the allowlisted folders plus deliberate `Templates/**` + `.obsidian/**` entries that exercise `ignore_globs`. `Inbox/sensitive-credentials.md` carries `donna: local-only` so the sensitivity-propagation tests have real content to bite on.
