# Memory Vault — Reference Spec

> Design spec for the Obsidian vault plumbing (slice 12), the semantic memory layer (slice 13), the episodic ingestion sources (slice 14), and the template-driven vault writes (slice 15). Companion to `docs/domain/memory-vault.md` (narrative) and `slices/slice_12_*` / `slice_13_*` / `slice_14_*` / `slice_15_*` briefs. Authoritative for config schema, write protocol, error taxonomy, memory schema, retrieval contract, and template-write contract.

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
- A rename is observed as a delete + add pair. Slice 16 reconciles these via the `_RenameBuffer` (content-hash keyed, `sources.vault.rename_window_seconds` TTL): if a matching add arrives within the window, `MemoryStore.rename` updates `source_id` in place (no re-embed); otherwise the delete flushes to `MemoryStore.delete` as before.

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

Shipped in later slices (historical notes):

- **Slice 14** — Chat / task / correction ingestion sources (`ChatSource`, `TaskSource`, `CorrectionSource`) wired onto the same `MemoryStore` / `MemoryIngestQueue`.
- **Slice 15** — Template-driven vault writes (`VaultTemplateRenderer`, `MemoryInformedWriter`, `MeetingNoteSkill`, `MeetingEndPoller`). See §12 below.
- **Slice 16** — Remaining template writes (`weekly_review`, `daily_reflection`, `person_profile`, `commitment_log`), central `People/{name}.md` stub auto-creator via `donna.memory.person_stub.ensure_person_stubs` wired into `MemoryInformedWriter`, and content-hash rename reconciliation in `VaultSource.watch()` (new `MemoryStore.rename`; 2s TTL buffer). `AsyncCronScheduler` extended with optional `day_of_week` / `minute_utc` kwargs. Writer structlog events renamed from `meeting_note_*` to `vault_autowrite_*` with a `template` field.

Still deferred:

- Re-rendering autowritten notes when the source data changes post-write → slice 17+.
- Supabase sync for `memory_documents` / `memory_chunks` and the `calendar_mirror.attendees` column → slice 17.
- BM25 / hybrid retrieval and eval harness → slice 17.
- Cloud embedding providers (Voyage-3-lite et al). The `EmbeddingProvider` Protocol supports them but no wiring is shipped.
- Attachment indexing (images, PDFs). V1 is `.md` only.

## 12. Template-driven vault writes (slice 15)

Slice 15 adds the first autonomous outbound path: Donna writes scaffold notes into the vault in response to external triggers, using a small, reusable stack that Slice 16's four remaining templates (`weekly_review`, `person_profile`, `commitment_log`, `daily_reflection`) extend without infrastructure changes.

### 12.1 Config schema extension (`memory.yaml`)

```yaml
skills:
  meeting_note:
    enabled: true
    poll_interval_seconds: 60
    lookback_minutes: 5
    autonomy_level: medium       # low | medium | high
    context_limits:
      prior_meetings: 5
      recent_chats: 5
      open_tasks: 5
```

Pydantic model: `MemorySkillsConfig` → `MeetingNoteSkillConfig` → `MeetingNoteContextLimits` in `src/donna/config.py`. `autonomy_level` is a `Literal["low","medium","high"]` — typos fail at load time. Per-template `autonomy_level` is the active value for path redirection; it may differ from `config/agents.yaml`'s per-agent `autonomy` field (see `spec_v3.md §7.3`).

### 12.2 `VaultTemplateRenderer`

```python
class VaultTemplateRenderer:
    def __init__(self, templates_dir: Path) -> None: ...
    def render(self, template_name: str, context: dict) -> tuple[str, dict]:
        """Return (body_without_frontmatter, frontmatter_dict)."""
```

Contract:

- Templates live on disk under `prompts/vault/` and are **self-contained**: each template emits its own frontmatter as a first-line `---\n...\n---\n` YAML block.
- Backed by a `jinja2.Environment` with `FileSystemLoader(templates_dir)`, `undefined=StrictUndefined`, `autoescape=False`. Missing context keys raise `jinja2.UndefinedError`.
- Renders the template, then parses the result via `frontmatter.loads` (same `python-frontmatter` library `VaultClient`/`VaultWriter` use in §3). Returns the post-frontmatter body and a plain dict of the frontmatter.
- Reuses `wrap_context` from `src/donna/skills/_render.py` so dotted access (`{{ event.title }}`) works the same as in DSL-side Jinja.
- Missing templates raise `jinja2.TemplateNotFound`; missing `templates_dir` raises `FileNotFoundError` at construction.

### 12.3 `MemoryInformedWriter`

```python
class MemoryInformedWriter:
    def __init__(
        self, *,
        renderer: VaultTemplateRenderer,
        vault_client: VaultClient,
        vault_writer: VaultWriter,
        router: ModelRouter,
        logger: InvocationLogger,  # reserved; router already logs
    ) -> None: ...

    async def run(
        self, *,
        template: str,
        task_type: str,
        context_gather: Callable[[], Awaitable[dict]],
        target_path: str,
        idempotency_key: str,
        user_id: str,
        autonomy_level: Literal["low","medium","high"],
    ) -> WriteResult: ...
```

Flow (in order):

1. **Autonomy redirect.** If `autonomy_level == "low"`, `effective_path = f"Inbox/{Path(target_path).name}"`; else `effective_path = target_path`. `Inbox/` is in `safety.path_allowlist` by default.
2. **Idempotency check (before any LLM spend).** `vault_client.read(effective_path)` under `try/except VaultReadError` filtering on the `"missing: "` prefix. If the existing note's `frontmatter["idempotency_key"] == idempotency_key`, emit `meeting_note_skipped_idempotent` and return `WriteResult(skipped=True, reason="idempotent")`. No context gather, no router call, no render.
3. **Gather context** via the caller's async callback.
4. **Load and render the skill prompt** via `router.get_prompt_template(task_type)` (the raw template file from disk) through a local `StrictUndefined` Jinja environment, then `router.complete(rendered, task_type=..., user_id=...)`. The parsed LLM output and `CompletionMetadata` come back, and the invocation is logged to `invocation_log` per §8.4 + `spec_v3.md §4.3`.
5. **Merge** `{**context, "llm": llm_output, "now_iso": datetime.now(UTC).isoformat()}`.
6. **Render the vault template** via `renderer.render(template, merged)` → `(body, fm)`.
7. **Serialise + write.** `frontmatter.dumps(frontmatter.Post(body, **fm))`, then `vault_writer.write(effective_path, serialized, expected_mtime=existing.mtime if existing else None, message=f"autowrite: {template} {idempotency_key}")`.
8. **Emit** `meeting_note_written` with `path`, `template`, `idempotency_key`, `autonomy_level`, `redirected_to_inbox`, and the resulting `sha`.

**Failure policy:** any exception in steps 3–7 emits `vault_autowrite_failed` with `reason` + `exc_type` and returns `WriteResult(skipped=True, reason=str(exc))`. No partial write is ever committed.

**`WriteResult`:**

| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | The `effective_path` after any redirect |
| `sha` | `str \| None` | Git commit SHA on success; `None` on skip |
| `skipped` | `bool` | True for idempotent short-circuits and failures |
| `reason` | `str \| None` | `"idempotent"` or the exception string |

### 12.4 `resolve_person_link`

```python
async def resolve_person_link(name: str, vault_client: VaultClient) -> str
```

Calls `vault_client.stat(f"People/{name}.md")` under `try/except VaultReadError`; returns `"[[People/{name}]]"` on success, `"[[{name}]]"` otherwise. Never creates a stub — missing people stay as unresolved wikilinks and surface in Obsidian's "Unresolved links" panel as a nudge.

### 12.5 Meeting-note reference trigger

- **`CalendarMirror.attendees`** — new nullable `TEXT` column (Alembic `c9d1e3f5a7b2`), JSON-encoded `list[{name, email}]`. Populated end-to-end from Google Calendar's `items[i].attendees` via `_parse_event` (displayName preferred, email local-part fallback) and `_update_mirror` (JSON on upsert).
- **`MeetingEndPoller`** — long-running asyncio task. Per cycle:
  ```sql
  SELECT event_id, user_id, calendar_id, summary, start_time, end_time, attendees
  FROM calendar_mirror
  WHERE datetime(end_time) BETWEEN datetime('now', ?) AND datetime('now')
    AND user_id = ?
    AND event_id NOT IN (
        SELECT json_extract(metadata_json, '$.calendar_event_id')
        FROM memory_documents
        WHERE source_type = 'vault'
          AND json_extract(metadata_json, '$.type') = 'meeting'
    )
  ```
  The first parameter is `f"-{config.lookback_minutes} minutes"`. `json_extract` is used over `->>` for SQLite version tolerance. Per hit, emit `meeting_end_detected` and dispatch sequentially. Per-hit exceptions are logged as `meeting_end_dispatch_failed` and the loop continues.
- **`MeetingNoteSkill`** — composes context: parse `event.attendees` JSON; resolve each to a wikilink via `resolve_person_link`; fire three concurrent `memory_store.search` calls (`sources=["vault"]` for prior meetings, post-filtered in Python on `metadata.type=='meeting'`; `sources=["chat"]`; `sources=["task"]`), each capped by `context_limits`; compute `target_path = f"Meetings/{event.start_time:%Y-%m-%d}-{slugify(event.summary)}.md"`; set `idempotency_key = event.event_id`; delegate to `MemoryInformedWriter.run`.
- **`draft_meeting_note` task type** — routed to `reasoner` in `config/donna_models.yaml`; prompt at `prompts/skills/draft_meeting_note.md.j2`; schema at `schemas/draft_meeting_note.json` (`summary: str`, `action_item_candidates: list[str]`, `open_questions: list[str]`, `links_suggested: list[str]`).
- **Vault template** at `prompts/vault/meeting_note.md.j2` — emits `type: meeting`, `calendar_event_id`, `event_start`, `event_end`, `attendees`, `idempotency_key`, `autowritten_by: donna`, `autowritten_at` in frontmatter; body has Attendees (with resolved wikilinks), Agenda, Summary (LLM), Action Items, Decisions, Open Questions, and Related (prior meetings + open tasks + suggested links).

### 12.6 Observability

- **Invocation log:** every `draft_meeting_note` call logs via the standard router path — `task_type=draft_meeting_note`, `model_alias=reasoner`, non-zero `tokens_in` / `tokens_out` / `cost_usd`. This is a paid cloud call, in contrast to the local embedding rows described in §8.4.
- **Structlog events:** `meeting_end_detected`, `meeting_note_written`, `meeting_note_skipped_idempotent`, `vault_autowrite_failed`, plus `meeting_end_poller_start`, `meeting_end_poller_cancelled`, `meeting_end_poller_cycle_failed`, `meeting_end_dispatch_failed` for poll-loop health.
- **Grafana:** `docker/grafana/dashboards/memory.json` has a "Template writes" row — writes-by-template timeseries, idempotent-skip-rate stat, `draft_meeting_note` cost timeseries, autowrite-failure count.

### 12.7 Closed loop

Autowritten meeting notes land in `Meetings/` inside the vault, which `VaultSource` (§9) is already watching. On the next watch cycle the new note is ingested into `memory_documents` / `memory_chunks` with `metadata_json.type = "meeting"` and `metadata_json.calendar_event_id = <event_id>`, and future `memory_search` calls retrieve it as a prior meeting for related events — so Donna's own writes become memory without any slice-specific plumbing.

### 12.8 Out of scope (slice 15)

- Four other templates (weekly review, person profile, commitment log, daily reflection) — slice 16.
- Auto-creation of `People/{name}.md` stubs — slice 16 (person-profile skill).
- Audio transcription / real meeting summaries — separate future work; the scaffold is a nudge, not a transcript.
- Re-rendering an already-written meeting note when the calendar event changes — slice 16+.
- Supabase sync for the new `attendees` column or template-write metadata — slice 17.
- Cross-user meeting notes / shared vaults.
- Per-skill cost-budget gating (the global cost guard from §4 + `cost.daily_pause_threshold_usd` applies).
