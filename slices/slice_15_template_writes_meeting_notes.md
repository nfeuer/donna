# Slice 15: Template-Driven Vault Writes — Meeting Note Reference

> **Goal:** Move up-stack from data plane (Slices 12-14) to autonomous writes. Build the shared infrastructure for memory-informed, template-driven vault notes, then ship **one** reference trigger end-to-end: a post-meeting skill that watches `CalendarMirror`, renders `prompts/vault/meeting_note.md.j2`, and writes to `Meetings/{date}-{slug}.md` with backlinks to `People/` notes when they exist. The other four templates (weekly review, person profile, commitment log, daily reflection) explicitly defer to Slice 16 so each trigger can be validated independently without co-mingled risk.

## Relevant Docs

- `CLAUDE.md` (always)
- `spec_v3.md §1.3, §4, §4.3, §7.3, §14` — design principles, model abstraction, invocation logging, agent safety, observability
- `docs/reference-specs/memory-vault-spec.md` — full design (§8 templates, §7 safety)
- `docs/domain/memory-vault.md` — narrative (extend with "Template writes" section)
- `docs/domain/model-layer.md` — how to register a new `task_type` + route
- `docs/domain/integrations.md` — existing calendar integration; `CalendarMirror` schema
- `slices/slice_12_vault_plumbing.md`, `slices/slice_13_memory_store_vault_source.md`, `slices/slice_14_episodic_sources.md` — upstream
- Existing code: `src/donna/integrations/calendar.py`, `src/donna/models/router.py` (for `complete()`), `schemas/` (existing structured-output schemas — mirror pattern)

## What to Build

1. **Template renderer** (`src/donna/memory/templates.py`):
   - `VaultTemplateRenderer(templates_dir: Path)` wraps Jinja2 `Environment` with `StrictUndefined`.
   - `render(template_name: str, context: dict) -> tuple[str, dict]` — returns `(body, frontmatter)`. Templates may emit a `{% set frontmatter = { ... } %}` block consumed at render time, or a dedicated first-line YAML block the renderer strips and returns as a dict.
   - One decision to land explicitly (documented in renderer's module docstring): frontmatter comes from the template itself, not from the caller, so templates are self-contained.
   - Deliberately thin — do not build a full templating framework here.

2. **`MemoryInformedWriter` helper** (`src/donna/memory/writer.py`):
   - Shared async class subsequent skills (weekly review, person profile, etc.) will instantiate with different templates.
   - Interface:
     ```python
     class MemoryInformedWriter:
         def __init__(
             self, *,
             renderer: VaultTemplateRenderer,
             memory_store: MemoryStore,
             vault_writer: VaultWriter,
             router: ModelRouter,
             logger: InvocationLogger,
         ): ...

         async def run(
             self, *,
             template: str,
             task_type: str,              # LLM routing key (config/task_types.yaml)
             context_gather: Callable[[], Awaitable[dict]],   # returns context + memory hits
             target_path: str,            # deterministic, caller-computed
             idempotency_key: str,        # stored in frontmatter; short-circuits re-run
             user_id: str,
             autonomy_level: str,         # "low"|"medium"|"high"
         ) -> WriteResult: ...
     ```
   - Flow: `context_gather()` → `router.complete(prompt, task_type, user_id)` → merge LLM output + gathered context → `renderer.render(template, merged)` → idempotency check (read existing frontmatter if present; skip if `idempotency_key` matches) → `vault_writer.write(target_path, body, expected_mtime=existing_mtime, commit_message=f"autowrite: {template} {idempotency_key}")`.
   - If `autonomy_level == "low"`, writes to `Inbox/` regardless of caller's `target_path`; on `medium|high`, honors `target_path`.
   - All LLM calls logged via `InvocationLogger` as usual; any failure emits `structlog.warn("vault_autowrite_failed", ...)` and returns without writing.

3. **Meeting-note Jinja template** (`prompts/vault/meeting_note.md.j2`):
   - Frontmatter includes `type: meeting`, `calendar_event_id`, `event_start`, `event_end`, `attendees`, `idempotency_key` (the event id), `autowritten_by: donna`, `autowritten_at`.
   - Body sections: attendees (with `[[People/{name}]]` backlinks), agenda-from-description, LLM-drafted summary stub, empty action-items list ("- [ ] TBD"), empty decisions list, links to prior meetings retrieved via memory_search, links to related open tasks.
   - Design intent: the note is a *scaffold* to nudge the user, not a pretend-transcript. Audio transcription (future) will replace the draft summary with real content.

4. **Meeting-end poller** (`src/donna/capabilities/meeting_end_poller.py`):
   - Runs as an asyncio task in the main run loop at `config.memory.skills.meeting_note.poll_interval_seconds` (default 60).
   - Query: `SELECT * FROM calendar_mirror WHERE end_at BETWEEN now()-5min AND now() AND user_id=? AND event_id NOT IN (SELECT metadata_json->>'calendar_event_id' FROM memory_documents WHERE source_type='vault' AND metadata_json->>'type'='meeting')`.
   - For each hit, enqueue a `MeetingNoteSkill` invocation.
   - ASSUMPTION: verify `CalendarMirror` carries `attendees`; if not, extend the schema in this slice (add a column) rather than adding yet another join.

5. **Meeting-note skill** (`src/donna/capabilities/meeting_note_skill.py`):
   - Composes `MemoryInformedWriter`.
   - `context_gather()`: runs three `memory_search` calls and merges:
     - prior meetings with overlapping attendees (`sources=["vault"]`, frontmatter filter `type=meeting`)
     - recent chats mentioning any attendee (`sources=["chat"]`, date_from = event_start - 7d)
     - open tasks tagged to any attendee (`sources=["task"]`, filter on task status open)
   - Caps each category at top-5 by score to keep the LLM prompt bounded.
   - `target_path` = `Meetings/{event_start:%Y-%m-%d}-{slugify(event_title)}.md`.
   - `idempotency_key` = calendar event id (stable across re-poll).
   - `task_type` = `draft_meeting_note`.
   - `autonomy_level` comes from `config/agents.yaml` for the `research` agent (this skill runs under `research`).

6. **New `task_type` + schema** (`config/task_types.yaml` + `schemas/draft_meeting_note.json`):
   - `draft_meeting_note` routes to the reasoner model (Anthropic Claude; see `config/donna_models.yaml`).
   - Structured output: `{summary: str, action_item_candidates: list[str], open_questions: list[str], links_suggested: list[str]}`. The template consumes these fields.
   - Prompt template at `prompts/skills/draft_meeting_note.md.j2` — takes the context bundle, returns the JSON above via the existing `complete(prompt, schema, model_alias)` pattern.

7. **Person-link resolver** (`src/donna/memory/linking.py`):
   - Tiny module, tiny API: `resolve_person_link(attendee_name: str, vault_client: VaultClient) -> str`.
   - Returns `"[[People/{name}]]"` if the file exists, else `"[[{name}]]"` (unresolved wiki-link — Obsidian renders these fine and surfaces them in the "Unresolved links" panel, which is actually a useful nudge to write the profile later).
   - **Does not auto-create stubs** — that's person-profile skill's job in Slice 16.

8. **Config** (`config/memory.yaml`):
   ```yaml
   skills:
     meeting_note:
       enabled: true
       poll_interval_seconds: 60
       lookback_minutes: 5
       autonomy_level: medium
       context_limits:
         prior_meetings: 5
         recent_chats: 5
         open_tasks: 5
   ```
   - Pydantic: `MeetingNoteSkillConfig`, reachable via `MemoryConfig.skills.meeting_note`.

9. **Agent allowlist** (`config/agents.yaml`):
   - `research` agent gains `vault_read`, `vault_write`, `vault_link`, `memory_search` if not already granted by Slice 12/13 (verify before adding duplicates).

10. **Wiring** (`src/donna/cli_wiring.py`):
    - `_try_build_template_renderer()` — non-fatal; returns `None` if `prompts/vault/` missing.
    - `_try_build_meeting_note_skill()` — non-fatal; requires template renderer, `MemoryStore`, `VaultWriter`, `CalendarMirror` client, router, logger.
    - Start the `MeetingEndPoller` task in the run loop next to the existing `VaultSource` watcher.

11. **Observability:**
    - Invocation log: new `task_type=draft_meeting_note`, `model_alias=<reasoner>`, normal token/cost fields (this *is* a cloud LLM call, unlike the local embedding calls).
    - Structlog events: `meeting_end_detected`, `meeting_note_skipped_idempotent`, `meeting_note_written`, `vault_autowrite_failed`.
    - Extend the `memory` Grafana dashboard with a "template writes" panel: counts by template, skip-rate (idempotency hits), and LLM cost per write.

12. **Tests:**
    - Unit: `test_template_renderer.py` (StrictUndefined raises on missing vars, frontmatter extraction, self-contained templates).
    - Unit: `test_memory_informed_writer_idempotency.py` (second call with same `idempotency_key` is a no-op; verified via `vault_writer.write` not called).
    - Unit: `test_memory_informed_writer_autonomy_low_redirects_to_inbox.py`.
    - Unit: `test_person_link_resolver.py` (exists → namespaced link; missing → bare wikilink).
    - Unit: `test_meeting_end_poller_query.py` (fixture `CalendarMirror` rows; verify exclusion when a meeting note already exists).
    - Integration: `test_meeting_note_skill_end_to_end.py`:
      - seed a `CalendarMirror` row ending 2 minutes ago with 2 attendees (one with an existing `People/{name}.md`, one without),
      - seed a prior meeting note in `Meetings/` and one relevant chat message indexed in `memory_documents`,
      - run the skill once (bypass the poller),
      - assert: (a) a new file at `Meetings/{date}-{slug}.md`, (b) frontmatter has `calendar_event_id`, (c) body contains one resolved `[[People/X]]` and one unresolved `[[Y]]`, (d) body references the prior meeting note, (e) `git log` shows one `autowrite:` commit,
      - run the skill again → no new commit, log event `meeting_note_skipped_idempotent`.
    - E2E `test_memory_e2e.py` (extend from Slice 14): run the poller against a fixture calendar, assert a meeting note is written and is itself retrievable via `memory_search`.

## Acceptance Criteria

- [ ] `VaultTemplateRenderer` renders `meeting_note.md.j2` with StrictUndefined; raises clearly on missing context keys
- [ ] `MemoryInformedWriter.run()` flow: `context_gather → router.complete → render → idempotency check → vault_writer.write`, with any step's failure logging and returning without a partial write
- [ ] Re-running the meeting-note skill with the same `calendar_event_id` does **not** produce a second commit
- [ ] `autonomy_level: low` forces the write into `Inbox/` regardless of requested `target_path`
- [ ] `prompts/vault/meeting_note.md.j2` produces valid frontmatter (`calendar_event_id`, `attendees`, `event_start`, `event_end`, `idempotency_key`, `autowritten_by`, `autowritten_at`) plus the body sections listed above
- [ ] `MeetingEndPoller` query excludes events that already have a meeting note (join against `memory_documents.metadata_json->>'calendar_event_id'`)
- [ ] `CalendarMirror.attendees` is populated end-to-end (added to the schema in this slice if not already present)
- [ ] `resolve_person_link` returns `[[People/Name]]` when the file exists, `[[Name]]` otherwise — never auto-creates a stub
- [ ] `draft_meeting_note` task type routes through the existing `ModelRouter`, with structured output validated against `schemas/draft_meeting_note.json`
- [ ] Every LLM call logs to `invocation_log` with correct `task_type`, `tokens_in`, `tokens_out`, `cost_usd`
- [ ] Structlog emits `meeting_end_detected`, `meeting_note_written`, `meeting_note_skipped_idempotent` as appropriate
- [ ] Grafana "template writes" panel shows counts and skip-rate
- [ ] Written meeting notes are re-indexed by `VaultSource` and retrievable via `memory_search` (closed loop: Donna's own writes become memory)
- [ ] `pytest tests/unit/memory tests/integration/test_meeting_note_skill_end_to_end.py` passes

## Not in Scope

- **No other templates wired.** `weekly_review`, `person_profile`, `commitment_log`, `daily_reflection` land in Slice 16 using the same `MemoryInformedWriter` pattern. Their template files are NOT created in this slice — avoid shipping dead code.
- **No auto-creation of `People/{name}.md` stubs.** Missing people stay as unresolved wikilinks. Person-profile skill in Slice 16 creates them.
- **No audio transcription.** The meeting note is a scaffold, not a transcript. Audio → transcript → note is a separate (larger) future bet.
- **No editing of existing meeting notes** post-write. If the calendar event changes after the note is written, the skill does not re-render. (Revisit in Slice 16+.)
- **No Supabase sync** for new metadata fields. Slice 17.
- **No cross-user meeting notes** or shared vaults.
- **No cost-budget gating** specific to template writes (existing global cost guard applies; per-skill caps can come later).
- **No retry with alternative model** on LLM failure — existing `resilient_call` fallback logic applies; do not add skill-specific escalation.

## Session Context

Load only: `CLAUDE.md`, this slice brief, `slices/slice_12_vault_plumbing.md`, `slices/slice_13_memory_store_vault_source.md`, `slices/slice_14_episodic_sources.md`, the parent plan at `/root/.claude/plans/what-are-some-additional-transient-truffle.md`, `spec_v3.md §1.3 / §4 / §4.3 / §7.3 / §14`, `docs/domain/memory-vault.md`, `docs/domain/integrations.md`, `docs/domain/model-layer.md`, `config/task_types.yaml`, `config/agents.yaml`, and the existing `src/donna/integrations/calendar.py` (for `CalendarMirror` schema).

## Handoff to Slice 16

Slice 16 consumes: a stable `MemoryInformedWriter` contract; a working reference skill (meeting-note) to mirror for each of the remaining four templates; the template-render + idempotency + structured-logging patterns. Slice 16 is expected to be mostly additive — one template file + one trigger + one skill class per remaining template, no infrastructure changes. Parallelizable across the four once the pattern lands.
