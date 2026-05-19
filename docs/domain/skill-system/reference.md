# Module Reference

The `src/donna/skills/` package contains the following modules, grouped by subsystem.

### Core data model

| Module | Role |
|--------|------|
| `models.py` | Dataclasses and row mappers for `skill` and `skill_version` tables. |
| `runs.py` | Dataclasses and row mappers for `skill_run` and `skill_step_result` tables. |
| `state.py` | `StateObject` — dict-of-dicts container that holds inter-step state during a skill run. |
| `database.py` | `SkillDatabase` — skill/version CRUD, state-transition writes. |
| `run_persistence.py` | `SkillRunRepository` — skill-run and step-result persistence. |

### Execution pipeline

| Module | Role |
|--------|------|
| `executor.py` | `SkillExecutor` — multi-step skill runner. Drives LLM steps, tool dispatches, and output validation. |
| `tool_dispatch.py` | `ToolDispatcher` — resolves `ToolInvocationSpec` from step YAML and dispatches via the `ToolRegistry`. |
| `tool_registry.py` | `ToolRegistry` — name-to-callable mapping for skill tools, with allowlist enforcement. |
| `tool_schemas.py` | JSON Schema definitions that constrain tool arguments at dispatch time. |
| `dsl.py` | Flow-control DSL — `expand_for_each` primitive that fans a tool invocation across a list. |
| `_render.py` | Shared Jinja2 rendering helper for DSL expressions and tool-arg templates. Uses `StrictUndefined` and wraps dicts for dotted-path access. |
| `triage.py` | `TriageAgent` — decides whether a matched capability should be handled by the skill path or escalated to `claude_native`. |

### Capabilities and matching

| Module | Role |
|--------|------|
| `seed_capabilities.py` | `SeedCapabilityLoader` — reads `config/capabilities.yaml`, UPSERTs rows into the `capability` table at startup. Idempotent. |
| `schema_inference.py` | `json_to_schema()` — infers a structural JSON Schema from an example value, used to generate `expected_output_shape` for captured-run fixtures. |

### Lifecycle and shadow sampling

| Module | Role |
|--------|------|
| `lifecycle.py` | `SkillLifecycleManager` — automated promotion/demotion gates (sandbox to shadow_primary to trusted). |
| `shadow.py` | `ShadowSampler` — post-run sampling of trusted skills to detect drift. |
| `equivalence.py` | `EquivalenceJudge` — Claude-based comparison of skill output vs. claude_native output. |
| `divergence.py` | `SkillDivergenceRepository` — persistence for `skill_divergence` rows. |
| `degradation.py` | `DegradationDetector` — Wilson-score CI computation to flag degraded skills. |
| `correction_cluster.py` | `CorrectionClusterDetector` — fast-path scan of recent user corrections to flag skills. |

### Auto-drafting and evolution

| Module | Role |
|--------|------|
| `detector.py` | `SkillCandidateDetector` — scans `skill_run` for high-frequency `claude_native` patterns. |
| `candidate_report.py` | `SkillCandidateRepository` — CRUD for `skill_candidate_report` rows. |
| `auto_drafter.py` | `AutoDrafter` — asks Claude to generate skill YAML from candidates, validates against fixtures. |
| `evolution.py` | `Evolver` — single-skill evolution attempt orchestrator. |
| `evolution_input.py` | `EvolutionInputBuilder` — assembles divergence cases, fixtures, and current YAML for evolution. |
| `evolution_gates.py` | `EvolutionGates` — four validation gates (syntax, targeted-case pass, fixture regression, recent-success). |
| `evolution_scheduler.py` | `EvolutionScheduler` — iterates degraded skills and calls `Evolver`, respecting daily cap. |
| `evolution_log.py` | `SkillEvolutionLogRepository` — reads/writes `skill_evolution_log` rows. |

### Validation and testing infrastructure

| Module | Role |
|--------|------|
| `validation.py` | Schema and output validation utilities for skill step results. |
| `validation_executor.py` | `ValidationExecutor` — offline executor for fixture validation. Constructs a `MockToolRegistry` per fixture so no real tools are dispatched. Never writes to production tables. |
| `validation_run_sink.py` | `ValidationRunSink` — in-memory sink implementing the `SkillRunRepository` protocol. Absorbs persistence calls without writing to disk. Used by `ValidationExecutor`. |
| `mock_tool_registry.py` | `MockToolRegistry` — `ToolRegistry` subclass that dispatches from a precomputed mock map. Raises `UnmockedToolError` if a fixture lacks a mock for a dispatched tool. |
| `mock_synthesis.py` | `cache_to_mocks()` — re-keys a `skill_run.tool_result_cache` into fingerprint-keyed mocks for `MockToolRegistry`. |
| `tool_fingerprint.py` | `fingerprint()` — deterministic tool-invocation fingerprinting. Per-tool rules select identity-relevant args (e.g., `web_fetch` keys only on `url`). Fallback: canonical sorted-JSON of all args. |
| `tool_test_kit.py` | `is_inert_at_import()` — test helper for tool-build branches (slice 22). Asserts a tool module triggers no network/disk I/O at import time. |
| `fixtures.py` | Fixture loading and `validate_against_fixtures` runner. |

### Startup, wiring, and scheduling

| Module | Role |
|--------|------|
| `startup.py` | `initialize_skill_system()` — generates embeddings, loads seed skills, builds and returns a `ToolRegistry`. |
| `startup_wiring.py` | `assemble_skill_system()` — lifespan helper that constructs all Phase 3+4 components and returns a `SkillSystemBundle`. |
| `loader.py` | Skill YAML loader — reads skill definitions from disk into the DB. |
| `crons/scheduler.py` | `AsyncCronScheduler` — fires `run_nightly_tasks` daily. Runs as an `asyncio.create_task` background task. |
| `crons/nightly.py` | `run_nightly_tasks()` — orchestrates the five nightly sub-jobs (detection, evolution, drafting, degradation, correction clusters). |

### Pollers and ingestion

| Module | Role |
|--------|------|
| `manual_draft_poller.py` | `ManualDraftPoller` — polls `skill_candidate_report` for rows with `manual_draft_at` set (triggered by the `POST /admin/skill-candidates/{id}/draft-now` API) and runs `AutoDrafter.draft_one`. Clears the column on completion or failure. |
| `chat_escalation_ingestion_poller.py` | Polls `escalation_request` rows where `mode='chat' AND status='submitted'`. Reads the answer from the `result` JSON envelope, appends it to the originating task's notes, transitions the task to `done`, and marks the row `status='validated'`. Tick interval: 30s. |

### Registered skill tools

Tools live in `src/donna/skills/tools/` and are registered into the `ToolRegistry` at startup via `register_default_tools()`.

| Tool | Dependency | Always registered? |
|------|------------|--------------------|
| `web_fetch` | None | Yes |
| `rss_fetch` | None | Yes |
| `html_extract` | None | Yes |
| `browser_extract_text` | None | Yes |
| `browser_screenshot` | None | Yes |
| `clean_html` | None | Not registered by `register_default_tools` — module exists but must be registered explicitly if needed |
| `gmail_search` | `GmailClient` | Only when client provided |
| `gmail_get_message` | `GmailClient` | Only when client provided |
| `email_read` | `GmailClient` | Only when client provided |
| `calendar_read` | Calendar client | Only when client provided |
| `task_db_read` | Task DB | Only when client provided |
| `cost_summary` | Cost tracker | Only when client provided |
| `vault_read` | Vault client | Only when client provided |
| `vault_list` | Vault client | Only when client provided |
| `vault_link` | Vault client | Only when client provided |
| `vault_write` | Vault writer | Only when writer provided |
| `vault_undo_last` | Vault writer | Only when writer provided |
| `memory_search` | Memory store | Only when store provided |

---

## Manual Escalation (drafting under budget pressure)

When a `skill_auto_draft` or `skill_evolution` task would exceed the
daily API budget, AutoDrafter is replaced by user-driven Claude Code
via the manual `claude_code` mode. The user receives a Discord ping
with a dashboard link, copies the spec from `/admin/escalations/<id>`,
runs Claude Code locally in a `git worktree`, and clicks **Mark as
built**. Donna ingests the branch and runs the existing
`ValidationExecutor` pipeline. Iteration cap is 3.

### Lifecycle landing state — different from AutoDrafter

AutoDrafter ends a generated skill in `draft` and requires a separate
human approval to enter `sandbox`. **Manual `claude_code` mode lands
the skill in `sandbox`** — one hop deeper than AutoDrafter — because
the user's "Mark as built" click + passing fixtures is itself the
explicit human gate. The `claude_native → skill_candidate → draft →
sandbox` transition chain ends with `reason='human_approval'`,
`actor='user'`, `actor_id=<discord_id>`. From `sandbox`, the existing
automatic promotion gates take over.

### Boundaries

- The host repo is mounted **read-only** at the path named by
  `manual_escalation.modes.claude_code.host_repo_path_env` (default
  `DONNA_HOST_REPO_PATH`). Donna's only writes for claude_code mode
  are the spec markdown file under `${WORKSPACE}/escalations/` (off
  the source tree) and DB rows.
- Donna **never** auto-merges. After validation, the user runs
  `git checkout main && git merge --no-ff <branch> && git push`
  manually. The dashboard "Mark as merged" button is a tracking-only
  write that flips `merged_at`.
- Concurrent claude_code escalations against the same originating
  entity (skill_candidate_report or skill row) are **de-duplicated**
  at the gate — the existing notification is re-delivered instead of
  opening a parallel branch race.

See [`docs/superpowers/specs/manual-escalation.md`](../../superpowers/specs/manual-escalation.md)
(canonical) for the full protocol, data model, and failure modes. The
work lands in `slice_21_claude_code_mode.md`.

## Tool Gap Surfacing (slice 22)

When a capability requires a tool that isn't registered, Donna **does
not auto-draft it** (tools touch credentials, dependencies, image
rebuilds — security boundary too sharp for autonomy). Instead, slice
22 makes tool gaps a first-class object with two surfacing tiers and
a manual-build path that reuses the slice-21 `claude_code` protocol.

### Detection

Five sites route through `donna.cost.tool_gap_surfacer.ToolGapSurfacer`:

| Site | Severity | Trigger |
|---|---|---|
| `CapabilityToolRegistryCheck` (boot) | speculative | `pending_review` capability or `trigger_type='on_manual'` declares an unregistered tool. The active+scheduled subset is still **fail-loud** — boot raises `CapabilityToolConfigError`. |
| `AutomationDispatcher.dispatch()` | high | Skill path is about to run; required tool missing. Run is short-circuited with `outcome='blocked_missing_tool'`. |
| Discord automation creation (`MissingToolError`) | high | User tried to create a Discord automation backed by a capability that needs an unregistered tool. |
| `AutoDrafter._surface_speculative_tool_gaps` (pre-flight) | speculative | LLM-drafted skill YAML references a step `tools:` name not in the registry. AutoDrafter still proceeds; the existing `UnmockedToolError` will dismiss the candidate at fixture validation. |
| `SkillExecutor._run_tool_invocations` (runtime trip-wire) | high | Mid-run dispatch attempted against an unregistered name. Surfaces a gap before the normal `ToolNotFoundError` propagates. |

### Surfacing

- **High** — Discord ping to the configured channel (default `agents`)
  with a `[File request] [Snooze 24h]` view. Owner-ID + stale-click
  guards mirror `BudgetEscalationView`. Re-pings on dedup hits are
  rate-limited to `tool_gap.reping_cooldown_seconds` (default 4h) via
  `last_pinged_at`.
- **Speculative** — silent. Filed to `tool_request` and aggregated by
  `MorningDigest._assemble_data` under a "Tool Gaps (speculative)"
  section, excluding snoozed and resolved rows.

### Storage + dedup

`tool_request` (migration `b2c3d4e5f6a8`) — partial-unique index
`(user_id, tool_name) WHERE status='open'`. Re-emission while open
upserts: bumps `priority`, refreshes `rationale`, can promote
speculative → high. Resolved/rejected rows allow new emissions so
historical pattern isn't lost. `snoozed_until` is a column on the row
(not a separate table).

### Build path

The `[File request]` button calls `EscalationGate.open_tool_build_escalation`
(no cost gate — the click *is* the resolution), which creates an
`escalation_request` row with `task_type='tool_request_fulfillment'`
and `originating_entity=('tool_request', <id>)`, then renders
`prompts/escalation/tool_build.md` (extends `skill_draft.md` with the
proposed signature, required metadata, allowlist + inert-test
clauses). The user runs `git worktree add` per the spec, builds in
Claude Code, commits, and clicks **Mark as built**.

### Validation (`ManualValidationRouter._validate_tool`)

Tools have **no** lifecycle table — activation is manual merge +
orchestrator restart. `_validate_tool` runs:

1. Six AST/regex lint rules in `donna.cost.tool_lint/`:
   - `anthropic_import` — reject `import anthropic` outside `src/donna/llm/`
   - `import_io` — reject module-level network/disk I/O
   - `secrets` — curated regex for `sk-…`, `xoxb-…`, `ghp_…`, `AKIA…`,
     PEM headers, vault-key naming (opt-in `detect-secrets` shim)
   - `metadata` — require `requires_rebuild: bool` + `default_timeout_seconds: int`
   - `allowlist` — diff must touch a config allowlist file with the
     tool name near a `tools:` key, OR module declares `unallowlisted = True`
   - `inert_test` — branch must include
     `tests/skills/tools/test_<name>.py` calling
     `is_inert_at_import('donna.skills.tools.<name>')`
2. Subprocess import smoke against the worktree:
   `python -c "import donna.skills.tools.<name>"`.

Pass → `tool_request.status='completed'`, `tool_request_filled` audit.
The user merges and restarts manually. Lint failures keep the row
`open` for iteration (slice-21 iteration cap then governs).

Dependent-skill regression (re-running fixtures of every skill that
references the new tool) is tracked in the [open backlog](../../superpowers/followups/open-backlog.md) per spec §10.4 row 4.

See [`docs/superpowers/specs/manual-escalation.md`](../../superpowers/specs/manual-escalation.md)
§7, §8, §10.5, §10.10 for the canonical protocol. The work lands in
`slice_22_tool_gap_surfacing.md`.
