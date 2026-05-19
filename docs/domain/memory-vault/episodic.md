# Episodic Sources (Slice 14)

Three new `MemorySource`-shaped modules on top of the same `MemoryStore`. All three observe the relevant source-of-truth write path, upsert a document, and expose a backfill entry point for the `donna memory backfill` CLI.

## Observer wiring

- **`Database` — constructor injection (Option A).** `Database.__init__` takes an optional `memory_observer` and `add_chat_message` / `create_task` / `update_task` each `await self._fire_memory_observer(method, event)`. Exceptions are logged (`memory_ingest_failed`) and swallowed; the source-of-truth write has already committed by the time the observer fires and a memory-layer failure must never unwind the caller. `cli_wiring._build_episodic_sources()` builds a `_CombinedDbObserver` that fans events out to `ChatSource` / `TaskSource` and attaches it via `Database.set_memory_observer(...)`.
- **`correction_logger` — module-level registry (Option B).** `log_correction` calls `donna.memory.observers.dispatch("correction", event)`. `CorrectionSource.__init__` is wired up via `register_observer("correction", source.observe)` during startup. Using the registry here keeps `log_correction`'s signature stable (widening it would churn every existing call site).

The asymmetry is deliberate — the `Database` already takes a handful of collaborators via its constructor, so one more is cheap and keeps call sites explicit; the `correction_logger` is a single loose function and staying out of its signature is worth the small pattern split.

## Source summaries

- **`ChatSource`** (`src/donna/memory/sources_chat.py`). Maintains a per-session rolling buffer keyed by `session_id`; flushes a turn document when the role flips, the buffer exceeds `max_tokens`, or the session transitions to `closed` / `expired`. `source_id` is `"{session_id}:{first_msg_id}-{last_msg_id}"` — re-running backfill upserts the same row, so row counts stay stable. Respects `sources.chat.index_roles` (default `[user, assistant]`), `min_chars`, and the configured `task_verbs` list.
- **`TaskSource`** (`src/donna/memory/sources_task.py`). Source-of-truth is the `tasks` table. Content hash is driven by `title + description + notes_json + status + domain + deadline` via `TaskChunker`; non-semantic fields (priority, scheduling times) deliberately don't bump the hash so retrieval stays cheap. A status transition into a terminal state listed in `sources.task.reindex_on_status` (default `done`, `cancelled`) busts the content hash so the final-state context always lands in the index. A `"delete"` event calls `MemoryStore.delete(source_type="task", source_id=task_id, user_id=...)`. The delete-event handler is tested but awaits a soft-delete path on the `tasks` table. *Tracked as [G-18](../../superpowers/followups/open-backlog.md).*
- **`CorrectionSource`** (`src/donna/memory/sources_correction.py`). One chunk per correction event; template is `"Field {field} changed from {original!r} to {corrected!r} on input: {input!r} (task_type={task_type})"`. `source_id` is the correction row `id`, so the second call to `log_correction` for the same row is a no-op upsert.

## Why episodic sources skip the ingest queue

`VaultSource` enqueues into `MemoryIngestQueue` because the boot-time backfill replays dozens of files in one burst — batching `embed_batch` over the burst is a real win. Chat / task / correction events arrive at human-typing rate (one at a time), so the batching window almost never fires with more than one event in it. The chat source also keeps a per-session in-memory buffer that depends on synchronous ordering (a queue would let two messages from the same session be processed out of order). And `TaskSource`'s "force re-embed on terminal status" path needs to bust the stored `content_hash` immediately before the upsert, which doesn't fit the queue's batched `upsert_many` contract. We accept the per-event cost (one `embed_batch` per upsert) and revisit if a bulk-import workload ever bursts chat ingest.

## Backfill CLI

`donna memory backfill [--source vault|chat|task|correction|all] [--user-id UID]` boots a minimal orchestrator (Database + MemoryStore + sources) and calls each selected source's `backfill(user_id)` in sequence. Idempotent — a second invocation leaves `memory_documents` / `memory_chunks` row counts unchanged (the `UNIQUE(user_id, source_type, source_id)` index is the enforcer). One source failing doesn't stop the rest; the command exits non-zero if any raised so CI can notice.

## Observability

- Invocation log: `task_type` in `{embed_chat_turn, embed_task, embed_correction}` (in addition to slice-13's `embed_vault_chunk` / `embed_memory_query`). `model_alias="minilm-l6-v2"`, `tokens_in=0`, `tokens_out=0`, `cost_usd=0.0`.
- Structlog events: `memory_ingest_chat_turn`, `memory_ingest_task`, `memory_ingest_correction` on success (each carries `latency_ms` for the full upsert round-trip); `memory_ingest_failed` on observer failure (with `source_type` + `reason`); `memory_backfill_{chat,task,correction}_done` on backfill completion.
- Grafana: slice-13's `memory` dashboard renders per-source gauges because it groups by `source_type`. Slice 14's follow-up commit added a per-source ingest-latency histogram panel driven by the `latency_ms` field above, so chat/task/correction counts and p50/p95 latencies are visible out of the box.

## Task-verb morphology

`ChatTurnChunker._keep` rescues short messages that would otherwise be dropped when they contain a configured `task_verbs` token. The match is tokenized and covers the bare verb plus `-s` / `-ed` / `-ing` inflections and the `e`-drop variants (`schedule` -> `scheduling` / `scheduled`). The check is token-level, so superset words like `callous` or `callable` intentionally slip through without rescuing an otherwise-short noisy message.
