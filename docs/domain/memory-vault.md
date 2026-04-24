# Memory Vault

> Slice 12 (vault plumbing) + Slice 13 (semantic memory). Design constraints trace back to `spec_v3.md §1.3 / §3.2.4 / §4.3 / §7.3 / §14 / §17 / §30`.

## Why a vault

Task state lives in SQLite, conversation context dies with the session, and there's no file-based workspace agents can hand back to the user. An Obsidian-compatible markdown vault gives Donna a durable, human-editable, version-controlled surface for meeting notes, people profiles, daily logs, and research artefacts. Slice 12 establishes the plumbing; slices 13–15 layer semantic retrieval, episodic ingestion, and template-driven writes on top.

## Architecture at a glance

| Piece | File | Responsibility |
|---|---|---|
| Config | `config/memory.yaml` + `donna.config.MemoryConfig` | Vault root, git author, safety envelope, ignore globs. |
| Read client | `donna.integrations.vault.VaultClient` | `read`, `list`, `stat`, `extract_links`. Async, read-only. |
| Write client | `donna.integrations.vault.VaultWriter` | `write`, `delete`, `move`, `undo_last`. Sole mutation path. |
| Git wrapper | `donna.integrations.git_repo.GitRepo` | `subprocess`-based `init_if_missing`, `commit`, `revert`, `log`. |
| Tools | `donna.skills.tools.vault_{read,write,list,link,undo_last}` | LLM-facing skill tools. |
| WebDAV | `docker/donna-vault.yml` + `docker/caddy/vault.Caddyfile.example` | Sync channel for Obsidian desktop / mobile clients. |
| Memory store | `donna.memory.store.MemoryStore` | Upsert / search over `memory_documents` + `memory_chunks` + `vec_memory_chunks` (sqlite-vec). |
| Embeddings | `donna.memory.embeddings.MiniLMProvider` | Wraps the shared MiniLM-L6-v2 loader in `capabilities.embeddings`. |
| Chunker | `donna.memory.chunking.MarkdownHeadingChunker` | 256-token chunks with heading-path provenance. |
| Ingest queue | `donna.memory.queue.MemoryIngestQueue` | Batches upserts so `embed_batch` runs once per flush. |
| Vault source | `donna.memory.sources_vault.VaultSource` | `watchfiles` watcher + boot-time backfill, keeps the store in sync with disk. |
| `memory_search` tool | `donna.skills.tools.memory_search` | Agent-facing retrieval entry point. |

The client and writer mirror the Gmail integration line-for-line: single module per integration, async methods over `asyncio.to_thread`, non-fatal startup via `_try_build_vault_client` / `_try_build_vault_writer` in `donna.cli_wiring`.

## Safety envelope

`VaultWriter` rejects any write that violates the invariants in `spec_v3.md §7.3`:

1. Path must resolve under the configured vault root (no `..`, no absolute, no symlink escape).
2. Extension must be `.md`.
3. Top-level folder must be in `safety.path_allowlist` (`Inbox`, `Meetings`, `People`, `Projects`, `Daily`, `Reviews` by default).
4. Payload size ≤ `safety.max_note_bytes` (200 KB default).
5. If `expected_mtime` is supplied and differs from on-disk, the write fails with `VaultWriteError(reason="conflict")` **before** any disk change.
6. If the target exists with frontmatter and the new content omits it, the existing frontmatter is preserved on keys the new content does not supply.
7. Every successful mutation produces exactly one git commit with author `Donna <donna@homelab.local>` (from config) and a structured message.
8. `undo_last` always uses `git revert` — never `git reset` — so the audit trail is preserved.

Failures raise `VaultWriteError(reason=...)` with reason codes: `path_escape`, `not_markdown`, `outside_allowlist`, `too_large`, `conflict`, `sensitive`, `missing`.

## Agent surface

Agents declared in `config/agents.yaml` gain the vault tools once the writer is built at boot, and `memory_search` once the store is built:

| Agent | Tools granted |
|---|---|
| `pm`, `scheduler`, `research`, `challenger` | `vault_read`, `vault_write`, `vault_list`, `vault_link`, `vault_undo_last`, `memory_search` |

If `config/memory.yaml` is missing or the vault root is unreachable, the vault tools simply aren't registered — boot still succeeds, and the rest of the skill system keeps running. Likewise, if sqlite-vec fails to load (`Database.vec_available == False`) the memory store and `memory_search` stay offline without taking the orchestrator down.

## Semantic memory (slice 13)

The memory layer lives inside the existing `donna_tasks.db` file (`spec_v3.md §16.1`). Three tables are added:

- `memory_documents` — one row per ingested source (a vault note today; chat turns, tasks, and corrections land in slice 14). `(user_id, source_type, source_id)` is unique. Soft-deleted via `deleted_at` so search joins can filter without pruning the ANN index on every tombstone.
- `memory_chunks` — one row per chunk emitted by the chunker. Carries `content`, `token_count`, and a JSON-encoded `heading_path` stack (e.g. `["ProjectPlan", "Design", "Schema"]`) so retrieval answers can cite a note's section header, not just the file path.
- `vec_memory_chunks` — the sqlite-vec `vec0` virtual table. Declared as `(chunk_id TEXT PRIMARY KEY, embedding FLOAT[384])`. Loaded on the shared aiosqlite connection in `Database.connect()`; if the extension wheel is missing the connection still opens and `vec_available` flips to `False`.

### Ingestion path

1. `VaultSource.watch()` — a `watchfiles.awatch` loop (500 ms coalesce) fires `vault_watch_event` for every `.md` change under the vault root, honoring `sources.vault.ignore_globs` plus the vault-wide `vault.ignore_globs`. Deletes translate to soft-delete; adds / modifies route to `_ingest_path`.
2. `VaultSource.backfill(user_id)` — walks the vault via `VaultClient.list(recursive=True)` on boot, compares each file's mtime against the stored `memory_documents.updated_at`, and enqueues anything newer-on-disk. Typical 20-note vault backfills in well under 30 s.
3. `_ingest_path` builds a `Document` carrying `user_id`, `source_type="vault"`, the relative path as `source_id`, the frontmatter title (or filename stem), the `vault:<rel>` URI, and the note body. `donna: local-only` (or `donna_sensitive: true`) in frontmatter flips `sensitive=True`, which propagates to every `RetrievedChunk.metadata["sensitive"]` for downstream prompt-building decisions.
4. `MemoryIngestQueue.run_forever()` drains up to 16 docs per 500 ms window into a single `MemoryStore.upsert_many` call — so `embed_batch` fires once per flush, amortising the SentenceTransformer warm-up over the batch.

### Re-ingest short-circuit

`MemoryStore.upsert(doc)` hashes `doc.content` to `content_hash`. If the existing row matches, we bump `updated_at`, clear `deleted_at`, refresh `title` / `metadata` / `sensitive`, and return without re-embedding. The `invocation_log` row count is the dedup signal: unchanged notes do not add rows for `task_type=embed_vault_chunk`.

### Retrieval

`MemoryStore.search(query, user_id, k, sources, filters)` embeds the query (one invocation with `task_type=embed_memory_query`) and runs a single three-table join — `vec_memory_chunks` (ANN window of `k*4`), `memory_chunks` (content + heading path), `memory_documents` (provenance, sensitivity, soft-delete filter). Scores use MiniLM's unit-normalised outputs: `score = 1 - distance² / 2` (sqlite-vec's `vec0` returns L2 distance). Results below `retrieval.min_score` are dropped; `k` is clamped to `retrieval.max_k`. A structlog `memory_retrieval` event records `k`, hits, sources, and `latency_ms` per call.

### Embedding contract

The default provider is `MiniLMProvider` (384-dim, 256-token window, BERT WordPiece tokenizer). Every `embed` / `embed_batch` emits one `invocation_log` row per input text — `model_alias="minilm-l6-v2"`, `tokens_in=0`, `cost_usd=0.0` — so the Grafana *Memory Vault* dashboard (`docker/grafana/dashboards/memory.json`) tracks embed volume alongside the normal LLM cost panels. Swapping to another provider (for example `bge-small-en-v1.5` or a cloud embedding) is a config-only change in `embedding.provider` plus a `build_embedding_provider` factory branch.

Token counting uses `tiktoken cl100k_base` when the encoding file is available and falls back to a deterministic word+punct heuristic when it isn't (offline CI). The fallback is within ~10% of WordPiece on English prose and typically over-counts, so we err on smaller chunks rather than silent truncation inside the encoder.

### Config

`config/memory.yaml` carries the tunables (`embedding.{provider,version_tag,dim,max_tokens,chunk_overlap}`, `retrieval.{default_k,min_score,max_k}`, `sources.vault.{enabled,chunker,ignore_globs}`). Pydantic aliases keep the slice-12 field names parseable so old configs still boot.

### Fixtures

`tests/fixtures/vault/` carries ~18 sample notes spanning the allowlisted folders plus deliberate `Templates/**` + `.obsidian/**` entries that exercise `ignore_globs`. `Inbox/sensitive-credentials.md` carries `donna: local-only` so the sensitivity-propagation tests have real content to bite on.

## Sync channel

A Caddy container (`donna-vault` compose service) exposes the vault root over WebDAV with HTTP basic auth. Obsidian desktop (Remote Sync plugin), Obsidian mobile (WebDAV plugin), and any WebDAV-aware editor can mount the endpoint. Writes made by humans over WebDAV and writes made by agents via `VaultWriter` share the same on-disk repo, so git history reflects both.

See `docs/operations/vault-sync.md` for bring-up steps and client configuration.

## What slices 12 + 13 do **not** do

- No chat / task / correction ingestion — slice 14 adds `ChatSource`, `TaskSource`, `CorrectionSource` on top of the same `MemoryStore`.
- No Jinja templates under `prompts/vault/` and no memory-informed writers (meeting notes, weekly reviews) — slice 15.
- No Supabase sync for `memory_documents` / `memory_chunks` — slice 16.
- No rename / move reconciliation beyond `delete + upsert` — slice 16.
- No BM25 / hybrid retrieval or eval harness — slice 17.
- No off-server backup push — the vault is on local NVMe, captured by the existing backup rotation (`docs/operations/backup-recovery.md`).

## Handoff contract for slice 14

Slice 14 inherits:

- A stable `MemoryStore.upsert` / `upsert_many` contract.
- The `MemorySource`-shaped pattern modelled by `VaultSource` (watcher + backfill + `_ingest_path`) to copy for chat / task / correction sources.
- `invocation_log` `task_type` values already present in the Grafana dashboard (`embed_vault_chunk`, `embed_memory_query`).
- A fixture set under `tests/fixtures/vault/` that the end-to-end memory test can extend.

No schema changes are expected in slice 14 — `memory_documents` / `memory_chunks` already accommodate every source_type.

## Episodic sources (slice 14)

Slice 14 adds three new `MemorySource`-shaped modules on top of the same `MemoryStore`. All three observe the relevant source-of-truth write path, upsert a document, and expose a backfill entry point for the `donna memory backfill` CLI.

### Observer wiring

- **`Database` — constructor injection (Option A).** `Database.__init__` takes an optional `memory_observer` and `add_chat_message` / `create_task` / `update_task` each `await self._fire_memory_observer(method, event)`. Exceptions are logged (`memory_ingest_failed`) and swallowed; the source-of-truth write has already committed by the time the observer fires and a memory-layer failure must never unwind the caller. `cli_wiring._build_episodic_sources()` builds a `_CombinedDbObserver` that fans events out to `ChatSource` / `TaskSource` and attaches it via `Database.set_memory_observer(...)`.
- **`correction_logger` — module-level registry (Option B).** `log_correction` calls `donna.memory.observers.dispatch("correction", event)`. `CorrectionSource.__init__` is wired up via `register_observer("correction", source.observe)` during startup. Using the registry here keeps `log_correction`'s signature stable (widening it would churn every existing call site).

The asymmetry is deliberate — the `Database` already takes a handful of collaborators via its constructor, so one more is cheap and keeps call sites explicit; the `correction_logger` is a single loose function and staying out of its signature is worth the small pattern split.

### Source summaries

- **`ChatSource`** (`src/donna/memory/sources_chat.py`). Maintains a per-session rolling buffer keyed by `session_id`; flushes a turn document when the role flips, the buffer exceeds `max_tokens`, or the session transitions to `closed` / `expired`. `source_id` is `"{session_id}:{first_msg_id}-{last_msg_id}"` — re-running backfill upserts the same row, so row counts stay stable. Respects `sources.chat.index_roles` (default `[user, assistant]`), `min_chars`, and the configured `task_verbs` list.
- **`TaskSource`** (`src/donna/memory/sources_task.py`). Source-of-truth is the `tasks` table. Content hash is driven by `title + description + notes_json + status + domain + deadline` via `TaskChunker`; non-semantic fields (priority, scheduling times) deliberately don't bump the hash so retrieval stays cheap. A status transition into a terminal state listed in `sources.task.reindex_on_status` (default `done`, `cancelled`) busts the content hash so the final-state context always lands in the index. A `"delete"` event calls `MemoryStore.delete(source_type="task", source_id=task_id, user_id=...)` — but note that as of slice 14 there is no soft-delete path on the `tasks` table or `Database` API, so this branch is **dormant in production**; it's kept ready for the day a soft-delete lands and exercised directly by unit tests.
- **`CorrectionSource`** (`src/donna/memory/sources_correction.py`). One chunk per correction event; template is `"Field {field} changed from {original!r} to {corrected!r} on input: {input!r} (task_type={task_type})"`. `source_id` is the correction row `id`, so the second call to `log_correction` for the same row is a no-op upsert.

### Why episodic sources skip the ingest queue

`VaultSource` enqueues into `MemoryIngestQueue` because the boot-time backfill replays dozens of files in one burst — batching `embed_batch` over the burst is a real win. Chat / task / correction events arrive at human-typing rate (one at a time), so the batching window almost never fires with more than one event in it. The chat source also keeps a per-session in-memory buffer that depends on synchronous ordering (a queue would let two messages from the same session be processed out of order). And `TaskSource`'s "force re-embed on terminal status" path needs to bust the stored `content_hash` immediately before the upsert, which doesn't fit the queue's batched `upsert_many` contract. We accept the per-event cost (one `embed_batch` per upsert) and revisit if a bulk-import workload ever bursts chat ingest.

### Backfill CLI

`donna memory backfill [--source vault|chat|task|correction|all] [--user-id UID]` boots a minimal orchestrator (Database + MemoryStore + sources) and calls each selected source's `backfill(user_id)` in sequence. Idempotent — a second invocation leaves `memory_documents` / `memory_chunks` row counts unchanged (the `UNIQUE(user_id, source_type, source_id)` index is the enforcer). One source failing doesn't stop the rest; the command exits non-zero if any raised so CI can notice.

### Observability

- Invocation log: `task_type` in `{embed_chat_turn, embed_task, embed_correction}` (in addition to slice-13's `embed_vault_chunk` / `embed_memory_query`). `model_alias="minilm-l6-v2"`, `tokens_in=0`, `tokens_out=0`, `cost_usd=0.0`.
- Structlog events: `memory_ingest_chat_turn`, `memory_ingest_task`, `memory_ingest_correction` on success (each carries `latency_ms` for the full upsert round-trip); `memory_ingest_failed` on observer failure (with `source_type` + `reason`); `memory_backfill_{chat,task,correction}_done` on backfill completion.
- Grafana: slice-13's `memory` dashboard renders per-source gauges because it groups by `source_type`. Slice 14's follow-up commit added a per-source ingest-latency histogram panel driven by the `latency_ms` field above, so chat/task/correction counts and p50/p95 latencies are visible out of the box.

### Task-verb morphology

`ChatTurnChunker._keep` rescues short messages that would otherwise be dropped when they contain a configured `task_verbs` token. The match is tokenized and covers the bare verb plus `-s` / `-ed` / `-ing` inflections and the `e`-drop variants (`schedule` → `scheduling` / `scheduled`). The check is token-level, so superset words like `callous` or `callable` intentionally slip through without rescuing an otherwise-short noisy message.
