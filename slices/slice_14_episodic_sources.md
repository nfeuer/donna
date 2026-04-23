# Slice 14: Episodic Sources — Chat, Task, Correction

> **Goal:** Extend the unified semantic index from Slice 13 beyond the vault. Wire three new `MemorySource` implementations — `ChatSource`, `TaskSource`, `CorrectionSource` — onto the existing SQLite write paths so every user chat turn, task mutation, and correction event automatically flows into `memory_documents` / `memory_chunks`. No schema changes; two new chunkers; idempotent backfill for data that already exists at the moment of deploy. End state: `memory_search` returns results spanning all four sources with correct provenance.

## Relevant Docs

- `CLAUDE.md` (always)
- `spec_v3.md §1.3, §4.3, §14, §16.4, §17` — design principles, invocation logging, observability, data classification, security
- `docs/reference-specs/memory-vault-spec.md` — full design
- `docs/domain/memory-vault.md` — narrative (extend with episodic sources section)
- `docs/domain/preferences.md` — existing correction logging contract
- `slices/slice_12_vault_plumbing.md`, `slices/slice_13_memory_store_vault_source.md` — upstream dependencies
- Existing code: `src/donna/tasks/database.py` (`add_chat_message`, `create_task`, `update_task`), `src/donna/preferences/correction_logger.py`, `src/donna/chat/engine.py` (session lifecycle)

## What to Build

1. **ChatTurnChunker** (`src/donna/memory/chunking.py`):
   - `ChatTurnChunker(max_tokens=256, merge_consecutive_roles=True, min_tokens=12)`.
   - Receives ordered `ChatMessage` list, merges consecutive same-role messages into turns, splits a turn when it exceeds `max_tokens`, drops messages under `min_chars` unless they contain a question mark or a task verb (defined in config: `sources.chat.task_verbs`).
   - Returns `list[Chunk]` plus a turn-spans metadata dict (for building `source_id` = `"{session_id}:{first_msg_id}-{last_msg_id}"`).

2. **TaskChunker** (`src/donna/memory/chunking.py`):
   - `TaskChunker(max_tokens=256)`.
   - Renders `title + description + notes_json + status + domain + deadline` via a small template, returns a single chunk when it fits and splits at `notes` boundaries when it does not.

3. **ChatSource** (`src/donna/memory/sources_chat.py`):
   - Observes `Database.add_chat_message()` via an observer protocol (see §5 below).
   - Maintains a per-session rolling buffer; flushes a turn document when (a) role flips, (b) buffer > `max_tokens`, or (c) session transitions to `EXPIRED` / `CLOSED`.
   - `source_id` = `"{session_id}:{first_msg_id}-{last_msg_id}"`; extending the range on the same-role buffer just upserts the same document (idempotent by `UNIQUE(user_id, source_type, source_id)`).
   - `backfill(user_id)`: scans `conversation_messages` joined with `conversation_sessions` for all sessions, regroups into turns, upserts. Idempotent.
   - Respects `sources.chat.index_roles` (default `[user, assistant]`).

4. **TaskSource** (`src/donna/memory/sources_task.py`):
   - Observes `Database.create_task()` and `Database.update_task()`.
   - Re-embed trigger: re-embed only if `title`, `description`, or `notes_json` changed since last upsert (compare `content_hash`), OR status transitions to `DONE` / `CANCELLED` (final-state context is high-signal).
   - `backfill(user_id)`: walks `tasks` WHERE `deleted_at IS NULL`, upserts all.
   - On task soft-delete, calls `MemoryStore.delete(source_type="task", source_id=task_id, user_id=...)`.

5. **CorrectionSource** (`src/donna/memory/sources_correction.py`):
   - Observes `correction_logger.log_correction()` post-commit.
   - One chunk per correction event via a fixed template: `"Field {field_corrected} changed from '{original_value}' to '{corrected_value}' on input: '{input_text}' (task_type={task_type})"`.
   - `backfill(user_id)`: walks `correction_log` rows, upserts all. Idempotent.

6. **Observer wiring** (choose ONE; land the decision in `docs/domain/memory-vault.md`):
   - **Option A — constructor injection.** `Database.__init__` takes an optional `memory_observer` callback; `add_chat_message` / `create_task` / `update_task` call it fire-and-forget. `correction_logger.log_correction()` takes the same observer via its own constructor.
   - **Option B — module-level registry.** A small `src/donna/memory/observers.py` with `register_observer(source_type, callback)` / `dispatch(event)`. Existing DB/logger modules gain two-line calls to `dispatch(...)` after successful commit.
   - **Recommendation:** Option A for `Database` (it already takes `chat_config`, `logger`, etc.); Option B for `correction_logger` to avoid widening its constructor. Keep the inconsistency explicitly documented, don't fight it.
   - Failures in observer callbacks **never** propagate to callers; always swallow + `structlog.warn("memory_ingest_failed", source_type=..., reason=...)`.

7. **Memory config extensions** (`config/memory.yaml` + `src/donna/config.py`):
   - Populate the `sources.chat`, `sources.task`, `sources.correction` blocks left as stubs in Slice 13.
   - Pydantic: `ChatSourceConfig(enabled, index_roles, min_chars, task_verbs, merge_consecutive_same_role, chunker)`, `TaskSourceConfig(enabled, reindex_on_status, chunker)`, `CorrectionSourceConfig(enabled)`.
   - `sources.chat.task_verbs` defaults to `["do", "call", "email", "schedule", "remind", "send", "book", "buy", "check", "review"]`.

8. **Backfill CLI** (`src/donna/cli.py`):
   - New subcommand: `donna memory backfill [--source vault|chat|task|correction|all] [--user-id UID]`.
   - Invokes each enabled source's `backfill(user_id)` sequentially; prints progress + final counts via structlog.
   - Idempotent: re-running leaves row counts unchanged (enforced by `UNIQUE(user_id, source_type, source_id)`).
   - Exit non-zero if any source raises, but continue the remaining sources so partial progress lands.

9. **Wiring** (`src/donna/cli_wiring.py`):
   - When `memory_store` is built, also build each enabled source and register its observer.
   - Add each source to the main async run loop (their `drain` tasks sit alongside `VaultSource`'s watcher).
   - Keep it non-fatal: if source construction fails, log + continue with the remaining sources.

10. **Observability:**
    - Invocation log: new `task_type` values `embed_chat_turn`, `embed_task`, `embed_correction` with `model_alias="minilm-l6-v2"`, `tokens_in=0`, `tokens_out=0`, `cost_usd=0.0`, `user_id`, `task_id` (for `embed_task`), `skill_id=null`.
    - Structlog events: `memory_ingest_chat_turn`, `memory_ingest_task`, `memory_ingest_correction`, plus the shared `memory_ingest_batch` from Slice 13.
    - Extend the `memory` Grafana dashboard with per-source chunk-count gauges and per-source ingest-latency histograms.

11. **Tests:**
    - Unit: `test_chat_turn_chunker.py` (role-flip boundary, consecutive merge, min-char + task-verb filter, turn-spans metadata).
    - Unit: `test_task_chunker.py` (single chunk vs split on long notes).
    - Unit: `test_chat_source_turn_emit.py` (flush on role flip, on size, on session close).
    - Unit: `test_task_source_reembed_rules.py` (no re-embed when unrelated fields change; re-embed on `title` change; re-embed on status→DONE).
    - Unit: `test_correction_source.py` (template rendering, idempotent upsert).
    - Unit: `test_memory_backfill_idempotent.py` — run backfill twice, assert row counts equal.
    - Integration: `test_chat_source_hook.py` — call `Database.add_chat_message` through real code path, verify chunk appears and `memory_search(sources=["chat"])` returns it.
    - Integration: `test_task_source_hook.py` — mirror of above for task create/update/status-change.
    - Integration: `test_correction_source_hook.py` — mirror for correction logger.
    - **E2E** `test_memory_e2e.py` (extend from Slice 13): seed 20 vault notes + 5 chat messages + 3 tasks + 2 corrections, run `memory_search("what did we decide about Sarah's onboarding")`, assert the top hit is the right vault chunk AND that `sources=["chat", "task"]` filter returns the expected subset spanning both non-vault types.

## Acceptance Criteria

- [ ] `ChatTurnChunker` merges consecutive same-role messages, splits at `max_tokens=256`, and drops messages < `min_chars` unless they contain `?` or a configured task verb
- [ ] `TaskChunker` produces one chunk for typical tasks and splits at `notes_json` boundaries when long
- [ ] `Database.add_chat_message()` triggers `ChatSource` observer; turn document is upserted with `source_id = "{session_id}:{first_msg_id}-{last_msg_id}"`
- [ ] `Database.create_task()` and `update_task()` trigger `TaskSource` observer; re-embed skipped when title/description/notes unchanged; re-embed forced on status→`DONE` or `CANCELLED`
- [ ] Task soft-delete calls `MemoryStore.delete(source_type="task", ...)`
- [ ] `correction_logger.log_correction()` triggers `CorrectionSource` observer; one chunk per correction
- [ ] Observer failures never propagate to the caller; logged with `structlog.warn("memory_ingest_failed", ...)`
- [ ] `memory_search(sources=["chat"])`, `sources=["task"]`, `sources=["correction"]` each return only results of that type
- [ ] `memory_search` with no `sources` filter returns results spanning all four types when data exists
- [ ] `donna memory backfill --source all --user-id NICK` is idempotent: second run leaves `memory_documents` / `memory_chunks` row counts unchanged
- [ ] Backfill continues past a single source's failure and exits non-zero with a summary
- [ ] Invocation log contains rows with `task_type in {embed_chat_turn, embed_task, embed_correction}`
- [ ] Grafana dashboard shows per-source chunk counts and ingest latency
- [ ] E2E `test_memory_e2e.py` asserts cross-source retrieval on a seeded fixture set
- [ ] `pytest tests/unit/memory tests/integration/test_chat_source_hook.py tests/integration/test_task_source_hook.py tests/integration/test_correction_source_hook.py tests/integration/test_memory_e2e.py` passes

## Not in Scope

- **No schema changes.** `memory_documents` and `memory_chunks` already accommodate all three source types.
- **No Jinja templates under `prompts/vault/`.** Slice 15.
- **No agent skill that writes memory-informed notes** (meeting notes from chat transcripts, weekly review from correction history). Slice 15.
- **No Supabase sync** for `memory_documents` / `memory_chunks`. Slice 16.
- **No retention / purge job** for old chat / task / correction chunks. Slice 16+.
- **No orphan cleanup** when sessions are hard-deleted (sessions currently only soft-expire — revisit if a hard-delete job ever lands).
- **No BM25 / hybrid retrieval.** Slice 17.
- **No per-source retrieval weighting / reranking.** Slice 17.
- **No cross-user memory sharing.** Each user's index stays isolated by `user_id`.

## Session Context

Load only: `CLAUDE.md`, this slice brief, `slices/slice_12_vault_plumbing.md`, `slices/slice_13_memory_store_vault_source.md`, the parent plan at `/root/.claude/plans/what-are-some-additional-transient-truffle.md`, `spec_v3.md §1.3 / §4.3 / §14 / §16.4 / §17`, `docs/domain/memory-vault.md`, `docs/domain/preferences.md`, and the three existing source-of-truth modules being hooked: `src/donna/tasks/database.py`, `src/donna/preferences/correction_logger.py`, `src/donna/chat/engine.py`.

## Handoff to Slice 15

Slice 15 consumes: a populated unified index with all four source types flowing; stable `memory_search(sources=[...])` filter semantics so the new meeting-note / weekly-review / person-profile skills can scope retrieval; backfill command available for re-seeding after fixture changes. No new data-plane work expected in Slice 15 — it moves up-stack into templated autonomous writes.
