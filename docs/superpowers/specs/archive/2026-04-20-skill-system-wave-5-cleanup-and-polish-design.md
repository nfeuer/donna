# Skill System Wave 5 — Cleanup & Polish

**Date:** 2026-04-20
**Status:** Spec
**Predecessor waves:** Wave 1 (production enablement), Wave 2 (first capability), Wave 3 (Discord NL), Wave 4 (news + email capabilities)
**Driving inventory:** `docs/superpowers/followups/2026-04-16-skill-system-followups.md`

## Scope

Sixteen follow-up items pulled from the post-Wave-4 backlog. No new feature surface. The wave:

- Unblocks `email_triage` in production (GmailClient boot wiring).
- Hardens capability correctness before the next capability lands (multi-feed, optional-input defaulting).
- Seeds four Claude-native task types as capabilities so the drafting pipeline can surface them.
- Pays down tool-registry, fixture, and mock-registry debt.
- Adds pagination to existing tools and a new HTML extraction tool.
- Lays groundwork for per-automation state carryover (schema-only; no consumer yet).
- Moves the first-run digest cap to the notification layer.
- Makes the baseline-reset window configurable.

**Explicitly out of scope.** Dashboard UI (F-4) and anything that depends on it (F-W4-E, F-8, F-12). F-W3-E (30-min approval timeout) — deferred per user selection.

## Theme 1 — Production email enablement (F-W4-I)

**Problem.** `src/donna/cli.py:189` passes `gmail_client=None` to `wire_skill_system`. `email_triage` automations hit the capability-availability guard at approval time because `gmail_search` / `gmail_get_message` never register.

**Design.** In `cli_wiring.py` (inside `wire_skill_system` or a helper called from it), attempt to construct a `GmailClient` when `config/email.yaml` resolves and its credential files exist on disk. On any construction failure (missing file, OAuth error, network), log a structured warning (`gmail_client_unavailable`, with reason) and proceed with `gmail_client=None`. Pass the result into `register_default_tools(gmail_client=...)`.

Rationale for non-fatal failure: orchestrator boot already starts the HTTP server before all wiring settles, so subsystem failures must not crash the process. The capability-availability guard surfaces the missing-tool state at automation-approval time with an actionable DM.

**Changes:**
- `src/donna/cli_wiring.py` — add `_try_build_gmail_client(config_dir)` helper; invoke before `register_default_tools`.
- `src/donna/cli.py` — update the `gmail_client=None` call site and its TODO comment.

**Tests.**
- Unit: helper returns `None` when email.yaml missing, when creds file missing, when `GmailClient.__init__` raises.
- Integration: boot orchestrator with a valid email config stub; assert `gmail_search` and `gmail_get_message` are in `DEFAULT_TOOL_REGISTRY.list_tool_names()`.

---

## Theme 2 — Capability correctness (F-W4-L, F-W4-K)

### F-W4-L — news_check multi-feed

**Problem.** `skills/news_check/skill.yaml` hardcodes `inputs.feed_urls[0]`. Additional feeds silently ignored.

**Design.** Rewrite the `tool_invocations` block to `for_each` over `inputs.feed_urls`, collecting all entries into a single list that subsequent steps iterate. The Explorer agent did not find a precedent for `for_each` over an `inputs.*` array; as a first check the implementer must confirm the DSL supports it (inspect `src/donna/skills/executor.py` `for_each` dispatch logic). If it already iterates any list expression, no executor change is needed. If it's limited to step-output lists, extend it.

The classify/render steps operate on the aggregated entries unchanged — the output shape stays `{ok, triggers_alert, message, meta}`.

**Changes:**
- `skills/news_check/skill.yaml` — replace `feed_urls[0]` invocation with `for_each: inputs.feed_urls`.
- Potentially `src/donna/skills/executor.py` if DSL extension needed.
- `config/capabilities.yaml` — update `feed_urls` description to drop the "v1 only first URL" disclaimer.
- New fixture: `skills/news_check/fixtures/multi_feed_match.json` — two URLs, one match each.

### F-W4-K — Jinja StrictUndefined optional-input defaulting

**Problem.** Skill authors must remember to write `{% if inputs.X is defined and inputs.X %}` because `StrictUndefined` raises on missing optional keys. Root cause: `AutomationCreationPath` doesn't inject `None` for optional schema fields when the challenger/novelty-judge parse omits them.

**Design.** Two-part fix.

1. **Draft-time defaulting.** In `src/donna/automations/creation_flow.py::AutomationCreationPath.approve` (or a new helper called before persistence), walk the capability's `input_schema.properties`, and for each key NOT in `required` that's missing from `draft.inputs`, set it to `None`. This makes Jinja's `is defined` check always true; authors can write `{% if inputs.X %}` safely.

2. **Skill-yaml lint test.** New test in `tests/test_skill_yaml_lint.py` that parses every `skills/*/skill.yaml`, walks Jinja templates, flags bare `{% if inputs.X %}` (without `is defined and`) where `X` is declared optional in the capability's `input_schema`. Fails fast in CI.

**Changes:**
- `src/donna/automations/creation_flow.py` — defaulting logic.
- `tests/test_skill_yaml_lint.py` — new lint test.
- `skills/email_triage/skill.yaml:19` — revert to the simpler `{% if inputs.query_extras %}` once defaulting is in (optional cleanup; the stricter pattern still works).

---

## Theme 3 — Claude-native task-type migration (F-13)

**Problem.** `generate_digest`, `prep_research`, `task_decompose`, `extract_preferences` run as Claude-native task types (`task_types.yaml`). No `capability` row exists, so the drafting pipeline can't surface them.

**Design.** Seed one `capability` row per task type. No skill created — capability stays `claude_native` until `SkillCandidateDetector` flags it and `AutoDrafter` produces a draft through the normal flow.

Each capability row carries:
- `name` = task type name (matches the existing `capability_name = task_type` convention at `detector.py:105`)
- `description` copied from `task_types.yaml`
- `input_schema` derived from the prompt template's `{{ placeholders }}`
- `default_output_shape` = the task type's `output_schema` (JSON-loaded)
- `trigger_type = "ad_hoc"` (these aren't scheduled; they fire from task flows)
- `status = "active"`, `created_by = "seed"`

**Input schema derivation.** For each task type, the implementer reads `prompts/<type>.md`, extracts `{{ placeholder }}` names, and constructs a JSON schema with each placeholder as an optional string property. Literal date/time placeholders (`current_date`, `current_time`) are excluded — those are dispatcher-injected.

Expected placeholder sets (to be verified during implementation):
- `generate_digest`: `calendar_events`, `tasks_due_today`, `current_date`, `current_time`
- `prep_research`: task context fields (`title`, `description`, `domain`, `scheduled_start`)
- `task_decompose`: task context (`title`, `description`, optional constraints)
- `extract_preferences`: `correction_batch`

**Delivery.**
- New Alembic migration `seed_claude_native_capabilities.py` — inserts all four `capability` rows (`INSERT OR IGNORE` for idempotency).
- `config/capabilities.yaml` — add four entries so `SeedCapabilityLoader` keeps them current (idempotent upsert on boot).

**Tool handling.** `task_types.yaml` lists tools (`calendar_read`, `task_db_read`, `cost_summary`, `web_search`, etc.) that are NOT registered in `DEFAULT_TOOL_REGISTRY`. This wave does NOT register them. When a skill eventually drafts for one of these capabilities, it either restricts itself to LLM-only steps or the wave that introduces that skill also registers the needed tool. Explicitly noted in the migration comment so future waves don't miss it.

**Tests.**
- Migration test: `pytest` runs the migration, queries `capability` table, asserts each of the four rows exists with correct name/schema.
- Detector test: seed an `invocation_log` row with `task_type='generate_digest'`, run `SkillCandidateDetector.run()`, assert a `skill_candidate_report` with `capability_name='generate_digest'` is created (or — if one already exists due to the capability row preseeding — that the flow still converges).

---

## Theme 4 — Tool registry & fixture hygiene

### F-W2-A — SeedCapabilityLoader drift logging

**Design.** Inside `SeedCapabilityLoader.load_and_upsert`, before the UPDATE branch, SELECT the current row's `description`, `input_schema`, `default_output_shape`. Compare against the YAML-derived values. If any differ, emit a structlog event `seed_capability_drift` with `capability_name`, list of differing fields, before/after values. Proceed with the UPDATE unchanged — the log is diagnostic only.

**Changes:** `src/donna/skills/seed_capabilities.py`.

### F-W2-B + F-W4-F — ToolRegistry.clear() + pytest fixture

**Design.**
- Add `ToolRegistry.clear()` that resets `self._tools = {}`.
- Add module-level docstring to `src/donna/skills/tools/__init__.py`: `DEFAULT_TOOL_REGISTRY` is boot-time-only; thread-safety is not a design goal because registration is expected to complete before dispatch begins. Tests may call `clear()` for isolation.
- Add autouse fixture in `tests/conftest.py`: `@pytest.fixture(autouse=True)\ndef _reset_default_tool_registry(): yield; DEFAULT_TOOL_REGISTRY.clear()`.

### F-W2-F — url_404 fixture tightening

**Design.** Update `skills/product_watch/fixtures/url_404.json`:
- `expected_output_shape.required`: `["ok", "in_stock", "triggers_alert"]`
- `expected_output_shape.properties.triggers_alert`: `{"type": "boolean", "enum": [false]}`

### F-W4-J — MockToolRegistry exception-raising shape

**Design.** Extend `MockToolRegistry.dispatch` to recognize a mock value matching `{"__error__": "<exception_class>", "__message__": "..."}`. When matched, raise the named exception with the message. Whitelist of allowed classes (safe to instantiate, no import-time side effects): `TimeoutError`, `ConnectionError`, `ValueError`, `RuntimeError`, `OSError`. Anything else falls back to `RuntimeError`.

Migrate the two error-path fixtures:
- `skills/email_triage/fixtures/email_gmail_error.json` — change `gmail_search` mock to `{"__error__": "ConnectionError", "__message__": "token expired"}`. Update `expected_output_shape` to require `ok: false` (was `null`).
- `skills/news_check/fixtures/news_feed_unreachable.json` — change `rss_fetch` mock to `{"__error__": "ConnectionError", "__message__": "feed unreachable"}`. Update shape similarly.

**Tests.** Unit: mock raises the exception; skill's `on_failure: escalate` path handles it correctly.

---

## Theme 5 — Tool pagination (F-W4-B)

### gmail_search

**Design.** Add optional `page_token: str | None = None` kwarg. Pass to Gmail API's `users.messages.list(pageToken=page_token)`. Return `{"messages": [...], "next_page_token": <str or None>}`. Clamp `max_results` at 100 as today.

### rss_fetch

**Design.** Add optional `offset: int = 0` kwarg. `feedparser` already loads the whole feed; slice `parsed.entries[offset : offset + max_items]` after the `since` filter. Return `has_more: bool` based on whether the sliced entries exhausted the filtered list.

**Backward compatibility.** Both changes are additive — existing callers work unchanged. New output fields (`next_page_token`, `has_more`) are optional in consumers.

**Tests.** Unit: call each tool with pagination params, assert correct slicing + output.

---

## Theme 6 — html_extract tool (F-W4-C)

**Design.** New tool `html_extract(html: str, base_url: str | None = None) -> dict`.

The tool does NOT fetch. Callers chain `web_fetch` → `html_extract`, passing the fetched body as `html` and optionally the source URL as `base_url` so trafilatura can resolve relative links. Keeping fetch and extract separate preserves testability — `html_extract` is deterministic given fixed input.

Output shape:
```json
{
  "ok": true,
  "title": "Article title",
  "text": "Main extracted article text",
  "excerpt": "First N chars of text",
  "links": [{"text": "anchor", "href": "url"}],
  "length": 1234
}
```

On extraction failure (no main content detected): `{"ok": false, "reason": "no_content"}`.

**Library.** `trafilatura` — best accuracy on news/article content, active maintenance. Added to `pyproject.toml` dependencies.

**Registration.** Unconditionally registered in `register_default_tools` (no external service dependencies).

**Tests.** Unit: happy-path article extraction, empty-body case, non-article HTML (returns ok=false).

**Rejected alternatives.**
- `readability-lxml` — less accurate on modern JS-heavy sites.
- `BeautifulSoup` only — too low-level; requires heuristics we'd maintain.

---

## Theme 7 — Per-automation state blob (F-W4-D)

**Design.** Schema + dispatcher plumbing only. No skill consumer in this wave.

**Migration.** Alembic migration adds `automation.state_blob JSON NULL` column, default NULL.

**Skill.yaml surface.** New optional top-level key `state_write: [<output_key>, ...]`. The dispatcher after a successful run reads each named key from the skill's output dict and merges them into `state_blob` (existing keys in `state_blob` not mentioned by this run's `state_write` are preserved). If a key listed in `state_write` is missing from the output, it's silently skipped — no-op for that key this run. Skills can explicitly null a stored key by returning `{key: null}` from output; `null` is a valid value and overwrites. Existing skills (which don't declare `state_write`) are unaffected; `state_blob` stays NULL.

**Injection.** Before dispatch, if `automation.state_blob` is non-null, inject it as `inputs.state` (dict). Skills can reference `{{ inputs.state.xxx }}`. Coexists with `prior_run_end` (which stays injected via `_build_prompt` unchanged).

**Changes:**
- New Alembic migration `add_automation_state_blob.py`.
- `src/donna/automations/dispatcher.py` — read `state_blob` and inject as `inputs.state`; after success, update `state_blob` with `state_write` keys from output.
- `src/donna/skills/models.py` (skill YAML parser) — accept optional `state_write` list.

**Tests.**
- Migration test: column exists and defaults to NULL.
- Dispatcher test: skill declares `state_write: [counter]`, returns `{counter: 5}`, next run sees `inputs.state == {"counter": 5}`.
- Backward-compat test: skill without `state_write` runs unchanged; `state_blob` stays NULL.

**Explicit non-goal.** No production skill uses this in Wave 5. Speculative per the follow-up doc's own classification; included per user's "C and D" instruction.

---

## Theme 8 — NotificationService first-run digest cap (F-W4-G)

**Problem.** First-run digests for `email_triage` / `news_check` have no upstream backlog bound — `prior_run_end` is null, and the skill may pull hundreds of items. Today the skill's `render_digest` prompt is supposed to self-cap; this isn't robust.

**Design.** Add `_truncate_for_channel(content: str, max_chars: int = 1900) -> str` helper in `NotificationService`. When `notification_type in {"digest", "automation_alert"}`, dispatch calls this helper on the content before sending. If truncated, append `\n\n…(truncated, N more chars)` footer. Discord's hard limit is 2000 characters; the 1900 default leaves headroom for the footer.

Exposes `max_chars` as an overridable param on `NotificationService.__init__` so tests / future callers can tune it.

**Changes:** `src/donna/notifications/service.py`.

**Tests.**
- Unit: content ≤ limit passes through unchanged.
- Unit: content > limit gets truncated with footer; total length ≤ 2000.
- Integration: dispatch a digest with long content; Discord bot sees the truncated form.

---

## Theme 9 — Configurable baseline reset window (F-9)

**Design.** Add `SkillSystemConfig.baseline_reset_window: int = 100`. Replace the hardcoded `LIMIT 100` at `src/donna/api/routes/skills.py:166` with the config value. Kept separate from `shadow_primary_promotion_min_runs` — different semantics.

**Changes:**
- `src/donna/config.py` — new field.
- `src/donna/api/routes/skills.py` — read config, use in the LIMIT clause (parameterized to avoid SQL injection risk).

---

## Cross-cutting concerns

### Alembic migrations

Two new migrations land in this wave:
1. `seed_claude_native_capabilities.py` (Theme 3).
2. `add_automation_state_blob.py` (Theme 7).

Both are up-and-down revertable. Both land in a single PR to keep boot coherent.

### Backward compatibility

All changes are additive or internal:
- Tool new params are keyword-only with defaults.
- Skill YAML new keys (`state_write`) are optional.
- Config new fields (`baseline_reset_window`) have sensible defaults.
- Fixture shape changes (MockToolRegistry `__error__`) only affect the two migrated fixtures; current shape still works for non-error mocks.

### Observability

- New structlog events: `gmail_client_unavailable`, `seed_capability_drift`.
- No new task_types added to `task_types.yaml` — all changes target the skill-system plumbing.

### Test posture

Every theme lands with unit tests. Three themes get integration tests: F-W4-I (boot with/without creds), F-13 (migration + detector), F-W4-D (dispatcher state carryover). No new E2E test in this wave — the Wave 4 E2E covers all three capability flows and won't regress.

### Rollout

Single PR, single deploy. No feature flags. Order of operations inside the PR:
1. Migrations (schema changes).
2. Tool-registry hygiene (F-W2-B) — unblocks tests for everything downstream.
3. GmailClient wiring (F-W4-I).
4. Per-theme code changes in any order.
5. Fixture updates (F-W2-F, F-W4-J) last, after MockToolRegistry change lands.

---

## Item → theme mapping

| Item | Theme | Priority | Effort |
|---|---|---|---|
| F-W4-I | 1 — email enablement | P1 | S |
| F-W4-L | 2 — capability correctness | P2 | S |
| F-W4-K | 2 — capability correctness | P2 | S-M |
| F-13 (×4) | 3 — task-type migration | P2 | S-per-type |
| F-W2-A | 4 — registry hygiene | P3 | S |
| F-W2-B + F-W4-F | 4 — registry hygiene | P3 | S |
| F-W2-F | 4 — registry hygiene | P3 | XS |
| F-W4-J | 4 — registry hygiene | P3 | S |
| F-W4-B | 5 — tool pagination | P3 | S |
| F-W4-C | 6 — html_extract | P3 | M |
| F-W4-D | 7 — state blob | P3 | M |
| F-W4-G | 8 — digest cap | P3 | S |
| F-9 | 9 — config polish | P3 | XS |

**Total estimated effort:** ~5-6 days for a single implementer, faster with parallel task dispatch.

---

## Follow-ups explicitly deferred

- **F-4 Dashboard UI** — separate brainstorm.
- **F-W4-E** — folds into F-4.
- **F-W4-A** (email_triage unbounded-sender) — wait for user ask.
- **F-W1-A** (DegradationDetector graded agreement) — P2, own design work; not bundled.
- **F-W3-E** (approval timeout) — scale-triggered, not needed single-user.
- **F-8** — subsumed by F-4.
- **F-12** Grafana — first-incident triggered.
- All **OOS-1..12** — trigger-gated per original spec §2.
