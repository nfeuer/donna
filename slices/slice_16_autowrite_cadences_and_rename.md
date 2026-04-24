# Slice 16: Cadence-Driven Autowrites, Person Stubs, and Rename Reconciliation

> **Goal:** Close out the deferrals flagged in `spec_v3.md §30.7` by shipping the four remaining template writes (`weekly_review`, `daily_reflection`, `person_profile`, `commitment_log`), adding a central `People/{name}.md` stub auto-creator, and replacing slice 13's "rename = delete + upsert" behavior with true content-hash-based rename reconciliation in `VaultSource.watch()`. Reuses slice 15's `VaultTemplateRenderer` / `MemoryInformedWriter` / `resolve_person_link` unchanged except for a minor `safety_allowlist` + `person_stub_helper` addition on the writer constructor and a rename of two structlog events.

## Relevant Docs

- `CLAUDE.md` (always)
- `spec_v3.md §1.3, §4, §4.3, §7.3, §14, §30.7, §30.8` — design principles, model abstraction, invocation logging, agent safety, memory/vault narrative
- `docs/reference-specs/memory-vault-spec.md §11, §12` — full template-write design + slice 15 handoff
- `docs/domain/memory-vault.md` — narrative (extend with "Slice 16" subsection)
- `slices/slice_15_template_writes_meeting_notes.md` — pattern to mirror; handoff note at the tail of the file

## What to Build

1. **`daily_reflection` skill** (`src/donna/capabilities/daily_reflection_skill.py`)
   - Context: today's meeting notes, terminal task mutations, chat highlights from `memory_chunks` windowed via a small shared `_list_documents(conn, ...)` helper.
   - Template: `prompts/vault/daily_reflection.md.j2` (self-contained frontmatter).
   - Target path: `Reflections/{YYYY-MM-DD}.md`. Idempotency key: `day.isoformat()`.
   - Task type: `draft_daily_reflection` → reasoner. Schema: `schemas/draft_daily_reflection.json`.

2. **`commitment_log` skill** (`src/donna/capabilities/commitment_log_skill.py`)
   - Context: today's chat + task signals via the same helper. LLM extracts commitments (speech-act classification).
   - Template: `prompts/vault/commitment_log.md.j2`. Target path: `Commitments/{YYYY-MM-DD}.md`. Idempotency key: the day.
   - Task type: `extract_commitments` → reasoner. Schema: `schemas/extract_commitments.json` with `{commitments: [{statement, owner, due_hint, source_ref, confidence}], summary}`.

3. **`weekly_review` skill** (`src/donna/capabilities/weekly_review_skill.py`)
   - Context: the week's meetings, completed tasks, commitment logs, chat highlights, plus the prior week's review (if any) for carry-over.
   - Template: `prompts/vault/weekly_review.md.j2`. Target path: `WeeklyReview/{iso_year}-W{iso_week:02d}.md`. Idempotency key: the ISO week label.
   - Task type: `draft_weekly_review` → reasoner.

4. **`person_profile` skill** (`src/donna/capabilities/person_profile_skill.py` + `person_mention_counter.py`)
   - Two triggers fan into one skill:
     - **Mention threshold** — `PersonMentionCounter.scan` SQL sweep of `memory_chunks` matching `[[Name]]` / `[[People/Name]]` over `lookback_days`; emits names ≥ `trigger_mentions_threshold`.
     - **Stub fill** — weekly scan of `People/*.md`; any note shorter than `min_body_chars` is enqueued.
   - Overwrite guard: skill short-circuits when the existing note is user-edited (non-empty + no `autowritten_by: donna` frontmatter). Empty notes and prior Donna autowrites are refreshed.
   - Idempotency key: `{name}@{iso_week}` — re-renders weekly as new context accrues.
   - Task type: `draft_person_profile` → reasoner.

5. **Person-stub auto-creation** (`src/donna/memory/person_stub.py`)
   - `ensure_person_stubs(body, *, vault_writer, vault_client, safety_allowlist)` scans a rendered body for bare `[[Name]]` wikilinks and creates missing `People/{name}.md` notes. Namespaced / aliased / heading wikilinks are skipped. Never overwrites. Writes a tiny `stub: true` frontmatter so the `person_profile` skill can fill them later.
   - Wired into `MemoryInformedWriter.run` after a successful `vault_writer.write`. Failures never propagate — logged as `person_stub_failed` (`person_stub_created` on success).

6. **Rename reconciliation** (`src/donna/memory/sources_vault.py` + `memory/store.py`)
   - `_RenameBuffer(ttl_seconds)` holds pending deletes keyed by content hash (FIFO lists per hash).
   - On `Change.deleted`: fetch the row's `content_hash` via `MemoryStore.get_document_meta_with_hash`, buffer it, and schedule a deferred flush via `asyncio.create_task(_flush_delete_after)`. On TTL expiry with no pair, call `MemoryStore.delete` as before.
   - On `Change.added`: compute the new file's hash with the same `_hash_content` the store uses. If the buffer has a match, cancel the pending delete flush and call `MemoryStore.rename(source_type, old_source_id, new_source_id, user_id)` — a single `UPDATE` with no chunk / embedding churn. On collision or miss, fall through to the standard ingest path.
   - Structlog: `vault_rename_buffered`, `vault_rename_matched`, `vault_rename_flushed_as_delete`.
   - Config: `sources.vault.rename_window_seconds` (default 2.0s).

7. **`AsyncCronScheduler` extension** (`src/donna/skills/crons/scheduler.py`)
   - Optional kwargs `day_of_week: int | None = None` (Mon=0..Sun=6) and `minute_utc: int = 0`. Weekly path bumps to the next occurrence of the configured weekday; daily path preserved for back-compat.
   - Decision: single class, not a sibling — consistent with the `AutomationScheduler` / `MeetingEndPoller` pattern elsewhere in the codebase; APScheduler is not currently a dependency and adopting it would be a separate cross-cutting slice.

8. **Structlog event rename (writer-owned)**
   - `meeting_note_written` → `vault_autowrite_written`
   - `meeting_note_skipped_idempotent` → `vault_autowrite_skipped_idempotent`
   - `vault_autowrite_failed` unchanged.
   - Each event now carries a `template` field so the Grafana panel can break counts down by template.

9. **Config** (`config/memory.yaml` + `src/donna/config.py`)
   - New skill blocks under `skills:`: `daily_reflection`, `commitment_log`, `weekly_review`, `person_profile`.
   - `safety.path_allowlist` additions: `WeeklyReview`, `Reflections`, `Commitments`.
   - `sources.vault.rename_window_seconds: 2.0`.
   - Pydantic: four new `*SkillConfig` + `*ContextLimits` classes hanging off `MemorySkillsConfig`.
   - `config/task_types.yaml`: `draft_daily_reflection`, `draft_weekly_review`, `draft_person_profile`, `extract_commitments`.
   - `config/donna_models.yaml`: route each to `reasoner`.

10. **Wiring** (`src/donna/cli_wiring.py` + `src/donna/cli.py`)
    - A single shared `_try_build_memory_informed_writer` constructs one writer (with `safety_allowlist`) reused by every skill.
    - Four new non-fatal try-builders + four `_start_*_cron` helpers. Each returns `None` silently when prerequisites are missing.

11. **Observability**
    - Grafana `docker/grafana/dashboards/memory.json` gains two new panels (Person stubs per day, Rename matched-vs-flushed) plus the event-name updates on the existing template-writes panels. Cost panel generalised over the five autowrite task_types.
    - Structlog events: `weekly_review_triggered`, `daily_reflection_triggered`, `commitment_log_triggered`, `person_profile_triggered`, `person_profile_skipped_user_owned`, `person_stub_created`, `person_stub_failed`, `vault_rename_buffered`, `vault_rename_matched`, `vault_rename_flushed_as_delete`, plus the two renamed `vault_autowrite_*` events.

## Acceptance Criteria

- [x] Four new skills land with skill class + vault template + LLM prompt + JSON schema each.
- [x] `MemoryInformedWriter` emits `vault_autowrite_written` / `vault_autowrite_skipped_idempotent` with a `template` field; slice-15 integration test updated.
- [x] `ensure_person_stubs` creates missing `People/{name}.md` stubs from bare wikilinks and never overwrites existing notes; gated on `safety.path_allowlist` membership.
- [x] `AsyncCronScheduler` supports weekly + `minute_utc` schedules; extended unit tests cover daily / weekly past-today / weekly today-past-hour / invalid-arg cases.
- [x] `PersonProfileSkill` re-renders empty notes and Donna autowrites but NEVER overwrites user-edited profiles.
- [x] `VaultSource.watch()` reconciles a `deleted` + `added` pair with matching content hash via a new `MemoryStore.rename`; chunk count stays constant and no embedding is re-computed.
- [x] `MemoryStore.rename` returns `False` on missing source or target collision, letting the caller fall back to delete+upsert.
- [x] All slice-16 unit tests pass (`test_person_stub.py`, `test_writer_creates_person_stubs.py`, `test_daily_reflection_skill_context.py`, `test_commitment_log_skill_context.py`, `test_weekly_review_skill_context.py`, `test_person_mention_counter.py`, `test_person_profile_overwrite_guard.py`, `test_rename_buffer.py`, `test_memory_store_rename.py`).
- [x] Slice-15 `test_meeting_note_skill_end_to_end.py` still passes; now also asserts the auto-created `People/Bob.md` stub and the renamed structlog events.
- [x] Grafana "Template writes" panels render per-template breakdown using the new event names; new "Person stubs per day" and "Vault renames matched vs. flushed" panels added.

## Not in Scope

- No new LLM provider wiring (cloud embedding providers remain deferred to slice 17).
- No Supabase sync for the new metadata fields or the `rename` path — deferred to slice 17.
- No audio transcription / real meeting summaries. Scaffolds only.
- No cross-user reviews or shared commitment logs — single-user model.
- No APScheduler migration — keep the home-grown `AsyncCronScheduler` in line with the rest of the codebase. Revisit as a project-wide refactor.
- No re-rendering of already-written meeting notes when the calendar event changes post-write (slice 16+).
- No BM25 / hybrid retrieval or eval harness (slice 17).
- No rename reconciliation across user IDs or soft-deleted documents. Buffer entries live in-process only; a crash within the TTL window degrades gracefully to delete+upsert.
- No commitment-log running view (daily-file pattern; git log + the `WeeklyReview` skill give the rollup).

## Session Context

Load only: `CLAUDE.md`, this brief, `slices/slice_15_template_writes_meeting_notes.md`, `spec_v3.md §30.7 / §30.8`, `docs/reference-specs/memory-vault-spec.md §11 / §12`, `docs/domain/memory-vault.md`, `config/memory.yaml`, `config/task_types.yaml`, the slice-15 `MeetingNoteSkill` + `MemoryInformedWriter` implementations.

## Handoff to Slice 17

Slice 16 consumes and leaves intact: a shared `MemoryInformedWriter` with `safety_allowlist` + `person_stub_helper`, a central stub hook, five template writes on the same contract, a rename-aware `VaultSource.watch()`, and an extended `AsyncCronScheduler`. Slice 17 inherits: Supabase sync for `memory_documents` / `memory_chunks` + the new `attendees` column, BM25 / hybrid retrieval, evaluation harness, cloud embedding providers, and an APScheduler migration if the project decides to normalise scheduler plumbing.
