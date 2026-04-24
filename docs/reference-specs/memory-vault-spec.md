# Memory Vault — Reference Spec

> Design spec for the Obsidian vault plumbing (slice 12) and semantic memory layer (slice 13). Companion to `docs/domain/memory-vault.md` (narrative) and `slices/slice_12_*` / `slice_13_*` briefs. Authoritative for config schema, write protocol, error taxonomy, memory schema, and retrieval contract.

## 1. Configuration schema (`config/memory.yaml`)

```yaml
vault:
  root: /donna/vault                       # absolute path (container-local)
  git_author_name: Donna
  git_author_email: donna@homelab.local
  sync_method: webdav                      # webdav | syncthing | manual
  templates_dir: prompts/vault             # unused until slice 15
  ignore_globs:
    - ".obsidian/**"
    - ".trash/**"
    - ".git/**"

safety:
  max_note_bytes: 200000                   # hard cap per write payload
  path_allowlist:                          # top-level folders accepted for writes
    - Inbox
    - Meetings
    - People
    - Projects
    - Daily
    - Reviews
  sensitive_frontmatter_key: donna_sensitive

# slice 13: semantic memory
embedding:
  provider: minilm-l6-v2                   # factory branch in build_embedding_provider
  version_tag: minilm-l6-v2@2024-05        # stamped on every chunk; bump to trigger reindex
  dim: 384                                 # must match the vec0 virtual table column
  max_tokens: 256                          # chunker cap (MiniLM-L6-v2's effective window)
  chunk_overlap: 32

retrieval:
  default_k: 8
  min_score: 0.25
  max_k: 32

sources:
  vault:
    enabled: true
    chunker: markdown_heading
    ignore_globs:                          # layered on top of vault.ignore_globs
      - ".obsidian/**"
      - ".trash/**"
      - ".git/**"
      - "Templates/**"
  # --- slice 14 ---
  chat: false
  tasks: false
  corrections: false
```

`MemoryConfig` (`donna.config`) round-trips every block. Pydantic aliases on `VaultEmbeddingConfig` (`model`, `chunk_tokens`) and `VaultRetrievalConfig` (`top_k`) keep slice-12-era YAML parseable.

## 2. Read protocol (`VaultClient`)

| Method | Returns | Notes |
|---|---|---|
| `read(path)` | `VaultNote(path, content, frontmatter, mtime, size)` | Body is the post-frontmatter content. Raises `VaultReadError` on missing / path escape. |
| `list(folder="", recursive=True)` | `list[str]` | Forward-slash relative paths, filtered by `ignore_globs`. |
| `stat(path)` | `(mtime, size)` | |
| `extract_links(path)` | `list[str]` | Bare `[[target]]` names; aliases and sub-headings are stripped. |

All methods run blocking file I/O via `asyncio.to_thread`. Reads accept any `.md` file under the vault root (even outside `path_allowlist`) so agents can inspect `README.md`, templates, etc.

## 3. Write protocol (`VaultWriter`)

```text
write(path, content, expected_mtime=None, message=None) -> commit_sha
delete(path, message=None)                                 -> commit_sha
move(src, dst, message=None)                               -> commit_sha
undo_last(n=1)                                             -> list[revert_sha]
```

Every mutation follows this fixed order:

1. Size check on the payload (rejects before reading disk).
2. `_resolve_safe_path` — rejects `..`, absolute, non-`.md`, symlink escape, or folder outside `path_allowlist`.
3. If the target exists:
   - Compare on-disk mtime to `expected_mtime` (if supplied). Mismatch → `VaultWriteError(reason="conflict")`.
   - Refuse the write if existing frontmatter has the sensitive key set truthy (`reason="sensitive"`).
4. Parse incoming `content` via `python-frontmatter`. Merge with existing metadata: existing keys win only when the new content omits them (`_merge_frontmatter`).
5. Serialise and write.
6. `GitRepo.commit([relpath], message)` with a pinned author — returns the new SHA.
7. Log a `vault_write` / `vault_delete` / `vault_move` event.

## 4. Error taxonomy

`VaultWriteError(reason=…)` codes:

| Reason | Raised when |
|---|---|
| `path_escape` | Path resolves outside vault root (absolute, `..`, symlink escape). |
| `not_markdown` | Extension is not `.md`. |
| `outside_allowlist` | Top-level folder is not in `safety.path_allowlist`. |
| `too_large` | Payload exceeds `safety.max_note_bytes`. |
| `conflict` | `expected_mtime` stale, or destination of a `move` already exists. |
| `sensitive` | Existing frontmatter has `safety.sensitive_frontmatter_key` set truthy. |
| `missing` | `delete` / `move` source does not exist. |

## 5. Git layout

- One repo, rooted at `vault.root`. Created on first boot via `GitRepo.init_if_missing()`.
- Local `user.name` / `user.email` set on init; never `--global`.
- Every commit authored via `-c user.name=… -c user.email=…` so the repo config can drift without changing author metadata.
- Commit message format: `donna(slice12): <verb> <path>` (overridable per call).
- `undo_last` uses `git revert --no-edit` over the last *n* commits (newest first).

## 6. FTS5 note

The slice-12 brief allows deferring FTS5 search to slice 13. Slice 12 shipped **without** a `vault_search` tool; slice 13 adds the semantic-search half of that surface via `memory_search`. A BM25 / hybrid retrieval layer is out of scope and deferred to slice 17.

## 7. Memory schema (slice 13)

Three tables land in the same `donna_tasks.db` file (`spec_v3.md §16.1`). The Alembic migration is `alembic/versions/f4a5b6c7d8e9_add_memory_and_vault.py`; it loads the sqlite-vec extension on the bind before creating the virtual table.

### 7.1 `memory_documents`

One row per ingested source.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK (uuid4) | Stable id used as FK from `memory_chunks`. |
| `user_id` | TEXT NOT NULL | Scoping from day one per CLAUDE.md. |
| `source_type` | TEXT NOT NULL | `vault` today; `chat`, `task`, `correction` in slice 14. |
| `source_id` | TEXT NOT NULL | For vault: forward-slash relpath (`Inbox/foo.md`). |
| `title` | TEXT NULL | Frontmatter `title` or filename stem. |
| `uri` | TEXT NULL | `vault:<rel>` — used for UI deep links later. |
| `content_hash` | TEXT NOT NULL | sha256 of the document body; dedupe signal. |
| `created_at` / `updated_at` | DATETIME NOT NULL | `updated_at` is the backfill-mtime comparison target. |
| `deleted_at` | DATETIME NULL | Soft-delete; search joins filter `IS NULL`. |
| `sensitive` | BOOLEAN NOT NULL | Set when frontmatter has `donna: local-only` (or `donna_sensitive: true`). |
| `metadata_json` | TEXT NULL | Per-source metadata (mtime, size, filtered frontmatter). |

Indexes: `UNIQUE(user_id, source_type, source_id)` as `ux_memory_doc_user_source`, plus `(user_id, updated_at)` and `(user_id, deleted_at)`.

### 7.2 `memory_chunks`

One row per chunk emitted by the chunker.

| Column | Type | Notes |
|---|---|---|
| `chunk_id` | TEXT PK (uuid4) | Matches the `vec_memory_chunks` key. |
| `document_id` | TEXT FK → `memory_documents.id` ON DELETE CASCADE | |
| `user_id` | TEXT NOT NULL | |
| `chunk_index` | INTEGER NOT NULL | Ordinal within the document. |
| `content` | TEXT NOT NULL | Chunk body as-indexed. |
| `content_hash` | TEXT NOT NULL | sha256 of the chunk content. |
| `heading_path` | TEXT NULL | JSON-encoded `list[str]`, e.g. `["ProjectPlan","Design","Schema"]`. |
| `token_count` | INTEGER NOT NULL | From `count_tokens` (tiktoken or fallback). |
| `embedding_version` | TEXT NOT NULL | `embedding.version_tag` at ingest time; reindex-aware. |
| `created_at` | DATETIME NOT NULL | |

Indexes: `(document_id)` and `(user_id, embedding_version)`.

### 7.3 `vec_memory_chunks`

sqlite-vec `vec0` virtual table:

```sql
CREATE VIRTUAL TABLE vec_memory_chunks USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding FLOAT[384]
)
```

The dim literal must match `embedding.dim` in config. Changing providers without bumping `version_tag` is unsupported.

## 8. Memory runtime contract

### 8.1 `Database.connect()`

After `PRAGMA journal_mode=WAL` / `PRAGMA foreign_keys=ON` the connection loads vec0 via `conn._execute(raw.enable_load_extension, True)` then `conn._execute(raw.load_extension, sqlite_vec.loadable_path())`. Failure (wheel missing, platform unsupported) logs `sqlite_vec_unavailable` and leaves `Database.vec_available = False`. The memory builder in `cli_wiring._try_build_memory_store` inspects that flag and returns `(None, None)`, so `memory_search` stays off the tool registry while every other subsystem keeps booting.

### 8.2 `MemoryStore`

```text
put(doc)                                      -> str                       # insert only; raises if exists
upsert(doc)                                   -> str                       # insert or replace
upsert_many(docs)                             -> list[str]                 # batched; one embed_batch per flush
delete(source_type, source_id, user_id)       -> bool                      # soft-delete
reindex(user_id, source_type=None)            -> int                       # force re-embed
search(query, user_id, k, sources, filters)   -> list[RetrievedChunk]
get_document_meta(source_type, source_id,
                  user_id)                    -> (id, updated_at) | None  # for backfill mtime compare
```

- **Content-hash short-circuit.** `upsert` hashes `doc.content` to `content_hash`. If the stored row matches, only `updated_at`, `deleted_at`, `sensitive`, `title`, `metadata_json`, and `uri` are touched — no chunking, no embedding, no `invocation_log` writes.
- **Replace semantics.** On a hash miss, the chunks for that document are deleted from both `memory_chunks` and `vec_memory_chunks` inside a single transaction, then the new chunks + vectors are inserted. `version_tag` is stamped from `provider.version_tag` so a provider bump is reindex-safe.
- **Soft-delete.** `delete` sets `deleted_at = now`. Rows remain in the vec index so `search`'s join stays fast; the join filter `d.deleted_at IS NULL` hides them.

### 8.3 `memory_search` SQL

```sql
SELECT c.chunk_id, c.document_id, c.content, c.heading_path,
       c.chunk_index, v.distance, d.source_type, d.source_id,
       d.title, d.sensitive, d.metadata_json, d.uri
FROM vec_memory_chunks v
JOIN memory_chunks c     ON c.chunk_id = v.chunk_id
JOIN memory_documents d  ON d.id       = c.document_id
WHERE v.embedding MATCH ? AND k = ?
  AND d.user_id = ? AND d.deleted_at IS NULL
  AND (? OR d.source_type IN (...))
ORDER BY v.distance
```

The `k` bound on the `v.embedding MATCH` clause is the ANN window (`k_eff * 4`, clamped to `retrieval.max_k * 4`); the final result is filtered and truncated to `k_eff = min(k or default_k, max_k)`. Score is `max(0, min(1, 1 - distance² / 2))`; results with `score < retrieval.min_score` are dropped. `filters.path_prefix` is applied in Python after SQL.

### 8.4 Observability

Every embed emits one `invocation_log` row per input text via the existing `InvocationLogger` — `task_type in {embed_vault_chunk, embed_memory_query}`, `model_alias="minilm-l6-v2"`, `tokens_in=0`, `tokens_out=0`, `cost_usd=0.0`, `input_hash=sha256(text)`. Structlog events fire on every ingest (`memory_ingest_batch`), every retrieval (`memory_retrieval`, with `k`, `hits`, `latency_ms`, `sources`), every watcher change (`vault_watch_event`), and every backfill run (`vault_backfill_done`).

The Grafana dashboard at `docker/grafana/dashboards/memory.json` (note: the repo uses `docker/grafana/dashboards/`, not `provisioning/dashboards/`) provides retrieval latency p50/p95, ingest batch count, re-embed counter, and watcher-event breakdown.

## 9. Ingestion contract (`VaultSource`)

### 9.1 Backfill

`VaultSource.backfill(user_id)` runs once on startup. It:

1. Calls `VaultClient.list("", recursive=True)` — which already applies `vault.ignore_globs` — then drops anything not ending in `.md` or matching `sources.vault.ignore_globs`.
2. For each survivor, `stat`s the file for mtime, compares against `memory_documents.updated_at` via `MemoryStore.get_document_meta`.
3. Enqueues `Document` into `MemoryIngestQueue` if the file is new, newer than the stored row, or previously soft-deleted.

Target: under 30 s for the 20-note fixture vault.

### 9.2 Watch

`VaultSource.watch()` runs for the lifetime of the orchestrator. `watchfiles.awatch(vault_root, step=500, debounce=500)`:

- `added` / `modified` → `_ingest_path` → `MemoryIngestQueue.enqueue`.
- `deleted` → `MemoryStore.delete(source_type="vault", source_id=rel, user_id=...)`.
- A rename is observed as a delete + add pair. True rename reconciliation is slice 16.

Expected latency: a `write` on disk should be visible in `memory_search` within ~1.5 s (500 ms debounce + flush window + embed).

### 9.3 Sensitivity propagation

A note is sensitive if its frontmatter contains `donna: local-only` **or** a truthy `donna_sensitive`. `Document.sensitive` flows through to every chunk stored for it; `RetrievedChunk.sensitive` and `RetrievedChunk.metadata["sensitive"]` surface on the tool response so prompt-builders can redact or refuse to echo the content.

## 10. `memory_search` tool

Signature:

```python
async def memory_search(
    *, store, query, user_id, k=None,
    sources=None, filters=None,
) -> dict[str, Any]
```

Response shape:

```json
{
  "ok": true,
  "query": "...",
  "count": 3,
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "source_type": "vault",
      "source_path": "Projects/donna-memory/overview.md",
      "title": "Donna Memory",
      "heading_path": ["Donna Memory", "Architecture", "Storage"],
      "content": "...",
      "score": 0.6132,
      "sensitive": false,
      "metadata": {"mtime": 1.7e9, "size": 812, "frontmatter": {...}, "sensitive": false}
    }
  ]
}
```

Registered in `donna.skills.tools.register_default_tools` under the `memory_store` kwarg, mirroring the `vault_client` / `vault_writer` gating. Granted to `pm`, `scheduler`, `research`, `challenger` in `config/agents.yaml`.

## 11. Non-goals

Slice 13 explicitly defers:

- Chat / task / correction ingestion sources (slice 14).
- Jinja templates under `prompts/vault/` and memory-informed writers (slice 15).
- Supabase sync for `memory_documents` / `memory_chunks` (slice 16).
- Rename / move reconciliation beyond `delete + upsert` (slice 16).
- BM25 / hybrid retrieval and eval harness (slice 17).
- Cloud embedding providers (Voyage-3-lite et al). The `EmbeddingProvider` Protocol supports them but no wiring is shipped.
- Attachment indexing (images, PDFs). V1 is `.md` only.
