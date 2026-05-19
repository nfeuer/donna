# Template Writes (Slices 15-16)

Donna writes vault notes autonomously in response to triggers. Slice 15 introduces the outbound path with the meeting-note skill as the reference implementation; slice 16 fills in the four cadence-driven templates, adds person-stub auto-creation, and replaces delete-plus-upsert rename handling with content-hash reconciliation.

## Slice 15 — template writes

### Components

- **`VaultTemplateRenderer`** (`src/donna/memory/templates.py`) — a
  thin `FileSystemLoader` + `StrictUndefined` Jinja environment.
  Templates are self-contained: each template emits its own
  frontmatter as a first-line `---` YAML block; the renderer parses
  and returns it separately via `python-frontmatter`.
  Missing context keys raise `jinja2.UndefinedError`.
- **`MemoryInformedWriter`** (`src/donna/memory/writer.py`) — the
  shared orchestrator every template-write skill delegates to. Owns
  autonomy-based path redirection, frontmatter-keyed idempotency,
  prompt-template rendering, routed LLM completion, vault-template
  rendering, and commit. Any failure logs `vault_autowrite_failed`
  and returns a skipped `WriteResult` — never a partial write.
- **`resolve_person_link`** (`src/donna/memory/linking.py`) — looks up
  `People/{name}.md` in the vault; returns `[[People/{name}]]` when
  present, `[[{name}]]` otherwise. Never auto-creates stubs.
- **`MeetingNoteSkill`** + **`MeetingEndPoller`**
  (`src/donna/capabilities/`) — the reference trigger. The poller
  scans `calendar_mirror` once per
  `config.memory.skills.meeting_note.poll_interval_seconds` for
  events that ended within the lookback window and don't already
  have a meeting note indexed. The skill composes memory-search
  context (prior meetings, recent chats, open tasks), resolves
  attendee wikilinks, and delegates to `MemoryInformedWriter`.

### Idempotency contract

Every autowritten note carries an `idempotency_key` frontmatter field
(the calendar event id for meeting notes). Before any LLM spend, the
writer reads the target path; if the existing note's
`idempotency_key` matches, it emits
`vault_autowrite_skipped_idempotent` and returns without work. This
makes re-polling safe and cheap.

### Autonomy-level -> path redirection

`config/memory.yaml:skills.meeting_note.autonomy_level` is the
skill-local control. At `low`, every write is redirected to
`Inbox/{basename}` regardless of the caller-computed `target_path`.
At `medium` / `high`, the caller's path is honoured. This is
distinct from `config/agents.yaml:research.autonomy`, which governs
the research agent's overall tool budget and timeout. Per-template
beats per-agent so Slice 16 templates can differ.

### CalendarMirror.attendees

`CalendarMirror` gained a nullable `attendees TEXT` column (migration
`c9d1e3f5a7b2`). `calendar.py::_parse_event` reads
`items[i].attendees` from the Google API, normalising each entry to
`{name, email}` (name = `displayName` or email local-part);
`calendar_sync.py::_update_mirror` JSON-encodes the list on write.
The meeting-note skill parses the JSON and passes it through to the
template + wikilink resolver.

### Observability

- Invocation log: new `task_type=draft_meeting_note`,
  `model_alias=reasoner`, standard token/cost fields (this is a
  paid cloud call, unlike the local embedding calls).
- Structlog events:
  `meeting_end_detected` (poller found an eligible event),
  `vault_autowrite_skipped_idempotent` (writer found a matching key),
  `vault_autowrite_written` (happy path),
  `vault_autowrite_failed` (any step raised).
  Slice 16 renamed the two writer-owned events from `meeting_note_*`
  to the generic `vault_autowrite_*` form and added a `template`
  field so Grafana breaks counts down per template.
- Grafana `memory` dashboard gains a "Template writes" row (writes
  by template, skip rate, LLM cost, failures).

## Slice 16 — cadence writes, person stubs, rename reconciliation

Slice 16 fills in the four template writes slice 15 deferred, adds a
central `People/{name}.md` stub auto-creator, and replaces
delete-plus-upsert rename handling with content-hash reconciliation.
No infrastructure changes to `VaultTemplateRenderer`,
`MemoryInformedWriter`, or `resolve_person_link` beyond two optional
constructor kwargs on the writer (`safety_allowlist`,
`person_stub_helper`).

### Cadence-driven skills

Four new skills, all sharing one `MemoryInformedWriter` instance:

- **`daily_reflection`** (`src/donna/capabilities/daily_reflection_skill.py`)
  — nightly. Target `Reflections/{YYYY-MM-DD}.md`, idempotency key
  the ISO date. Context: today's meeting notes, terminal task
  mutations, chat highlights.
- **`commitment_log`** (`src/donna/capabilities/commitment_log_skill.py`)
  — nightly. Target `Commitments/{YYYY-MM-DD}.md`, idempotency key
  the ISO date. LLM extracts explicit speech-act commitments; one
  file per day so idempotency is trivial and git log gives the
  running view.
- **`weekly_review`** (`src/donna/capabilities/weekly_review_skill.py`)
  — Sunday evening. Target `WeeklyReview/{iso_year}-W{iso_week:02d}.md`,
  idempotency key the ISO week label. Also loads the prior week's
  review (if any) for carry-over commitments.
- **`person_profile`** (`src/donna/capabilities/person_profile_skill.py`
  \+ `person_mention_counter.py`) — Sunday evening. Two triggers:
  **mention_threshold** (`PersonMentionCounter` sweep of
  `memory_chunks.content LIKE '%[[Name]]%'` over
  `lookback_days`) and **stub_fill** (weekly scan of `People/*.md`
  for notes shorter than `min_body_chars`). Overwrite guard: refuses
  to touch notes that are non-empty *and* lack
  `autowritten_by: donna` in frontmatter — Donna never overwrites a
  user-edited profile. Idempotency key `{name}@{iso_week}`.

All four route to the `reasoner` alias via new task_types
(`draft_daily_reflection`, `extract_commitments`,
`draft_weekly_review`, `draft_person_profile`) in
`config/task_types.yaml` + `config/donna_models.yaml`.

### Time triggers

`AsyncCronScheduler` (`src/donna/skills/crons/scheduler.py`) gained
optional `day_of_week: int | None` (Mon=0..Sun=6) and
`minute_utc: int = 0` kwargs — enough to cover daily +
sub-hour-granular weekly triggers without introducing APScheduler.
The existing positional `AsyncCronScheduler(hour_utc, task)`
signature is preserved for back-compat with the other cron users in
the codebase.

### Person-stub auto-creation

`donna.memory.person_stub.ensure_person_stubs` scans a rendered body
for bare `[[Name]]` wikilinks (namespaced, aliased, and heading
variants are excluded) and writes a `People/{name}.md` stub when
missing. Wired into `MemoryInformedWriter.run` after a successful
`vault_writer.write`; failures never propagate (logged as
`person_stub_failed`). `People` must be in
`safety.path_allowlist` — the helper is a no-op otherwise.

Stubs carry `type: person`, `name`, `stub: true`,
`autowritten_by: donna` frontmatter, which the `person_profile`
skill later detects and rewrites with full context.

### Rename reconciliation

`VaultSource.watch()` now buffers `Change.deleted` events for
`sources.vault.rename_window_seconds` (default 2 s) keyed by the
row's `content_hash`. If a matching `Change.added` arrives within the
window, the pending delete is cancelled and `MemoryStore.rename`
updates `source_id` in place — no chunk or embedding churn. On
miss, the delete flushes normally; on target collision, the caller
falls back to delete+upsert.

Structlog events: `vault_rename_buffered`, `vault_rename_matched`,
`vault_rename_flushed_as_delete`.

See `slices/slice_16_autowrite_cadences_and_rename.md` and
`spec_v3.md §30.7` for the full scope + deferrals handed to slice 17.

See `slices/slice_15_template_writes_meeting_notes.md` and
`spec_v3.md §1.3 / §4 / §4.3 / §7.3 / §14`.
