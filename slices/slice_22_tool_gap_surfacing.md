# Slice 22: Tool Gap Surfacing

> **Goal:** When `capability_tool_check` finds a missing tool, route it to a `tool_request` row. High-blocking gaps (capability is active and cannot run) ping the user in real time. Speculative gaps (registered-but-not-scheduled, or proposed by a skill draft) file silently and surface in the morning digest. Tool *builds* — when the user decides to fulfill a request — reuse slice 21's claude_code mode protocol, plus extra security lints from §10.5.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §7 (tool gap protocol — full), §8 (`tool_request` schema), §10.5 (tool-build-specific failures — all rows), §10.10 (`tool_gap_detected`, `tool_request_filed` audit logs).
**Related upstream specs:** `spec_v3.md §3.2` (Tool Integration Architecture), `spec_v3.md §23.3` (Skill Executor and Tool Registry).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §7 — Tool gap protocol

Tools cannot be auto-drafted (security, dependencies, credentials, image rebuild). When `capability_tool_check` finds a missing tool, Donna takes one of two paths based on **blocking severity**:

| Blocking severity | Trigger | Surfacing |
|---|---|---|
| **High** | Capability is active (scheduled or user-invoked) and cannot run | Real-time Discord ping with `[File request] [Snooze 24h]` |
| **Speculative** | Capability is registered but not yet scheduled, OR a skill draft proposed using a not-yet-existing tool | Filed silently to `tool_request` table; surfaces in morning digest |

Both paths write a `tool_request` row. Tool *builds* use the same claude_code protocol as §5.3 (slice 21) but with extra checks (§10.5).

Crucially: the **decision to start a tool build is always the user's**. A real-time ping is a notification, not an escalation request — there are no `[Approve $X extension]` buttons because no API spend will fix the gap.

### §8 — `tool_request` schema

```sql
CREATE TABLE tool_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  proposed_signature JSON,
  rationale TEXT,
  blocking_capability_id INTEGER,    -- NULL = speculative
  priority INTEGER DEFAULT 3,
  status TEXT DEFAULT 'open',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,
  resolved_branch TEXT
);
```

### §10.5 — Tool-build-specific failures (all rows wire here)

| Failure | Mitigation |
|---|---|
| Tool needs new dependency, image not rebuilt | Tool-build template requires `requires_rebuild: bool` in tool metadata. If true after merge: registry refuses to mark tool active until orchestrator restart with new build SHA. Discord nag posted hourly until rebuild. |
| Tool hardcodes a credential value | Pre-commit secret scanner runs on the branch before validation. Common patterns blocked. Vault-key naming convention flagged. |
| Tool calls Anthropic API directly | Lint check: `import anthropic` outside `src/donna/llm/` is hard fail. |
| Tool not added to any agent allowlist | Lint check. Submission requires at least one allowlist update or explicit `unallowlisted=true` flag. |
| Tool does I/O at import time | Lint check: tool module's top-level scope must not invoke network/disk APIs. Heuristic + explicit `is_inert_at_import` test fixture. |
| Tool unbounded latency | Per-tool `default_timeout_seconds` declared in metadata; dispatcher enforces. Default 5s. |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §7, §8, §10.5
- Slice 21 — depends on it (tool builds reuse claude_code protocol)
- `src/donna/capabilities/capability_tool_check.py`, `tool_requirements.py` — emission point
- `src/donna/skills/mock_tool_registry.py` — mock entry verification
- `prompts/escalation/tool_build.md` — new Jinja template (canonical §9)
- `src/donna/skills/morning_digest.py` (or equivalent) — speculative gap aggregation

## What to Build

1. **Schema:** `tool_request` table with severity / detection_point /
   snooze / dedup-on-open partial-unique index (alembic
   `b2c3d4e5f6a8`).
2. **Data layer:** `donna.cost.tool_gap.ToolGap`,
   `donna.cost.tool_request_repository.ToolRequestRepository` with
   upsert-on-open dedup (priority bump, severity promotion,
   rationale refresh).
3. **Surfacer:** `donna.cost.tool_gap_surfacer.ToolGapSurfacer` —
   single sink for every detection point; routes high vs speculative;
   audits via `donna.cost.tool_gap_audit.write_tool_gap_event`;
   rate-limits Discord re-pings via `last_pinged_at`.
4. **Detection sites:**
   - Boot: `CapabilityToolRegistryCheck` — partition by
     status/trigger_type; speculative for inactive/manual, fatal
     raise preserved for active+scheduled.
   - Pre-execution: `AutomationDispatcher.dispatch()` short-circuits
     skill paths with `outcome='blocked_missing_tool'`.
   - Automation creation: `discord_bot` `MissingToolError` catch
     surfaces high gaps in addition to the existing reply.
   - Skill draft pre-flight: `AutoDrafter._surface_speculative_tool_gaps`
     after `_extract_draft_payload`.
   - Defensive runtime: `SkillExecutor._run_tool_invocations` emits
     a high gap before the normal `ToolNotFoundError` path runs.
5. **Discord view:**
   `donna.integrations.discord_views.ToolGapPingView` with
   `[File request]` + `[Snooze 24h]` mirroring `BudgetEscalationView`'s
   owner-ID + stale-click pattern.
6. **Build path:** `EscalationGate.open_tool_build_escalation()`
   creates the `escalation_request` row with
   `originating_entity=('tool_request', <id>)` and immediately renders
   `prompts/escalation/tool_build.md` (extends `skill_draft.md` with
   §10.5 clauses + proposed signature). Validation hops the existing
   slice-21 poller → `ManualValidationRouter._validate_tool` →
   `donna.cost.tool_lint.lint_tool_branch` (six rule modules) +
   subprocess import smoke. Pass marks `tool_request.status='completed'`.
7. **Digest:** `MorningDigest._assemble_data` queries
   `list_open_speculative(exclude_snoozed=True)`, surfaces under a
   new `tool_gaps` template variable + degraded-mode section.
8. **Config:** `tool_gap` block in `config/manual_escalation.yaml`
   (`realtime_channel`, `snooze_seconds`, `reping_cooldown_seconds`,
   `lint.{requires_rebuild_default,default_timeout_seconds,detect_secrets_enabled}`)
   plus `tool_request_fulfillment` task type entry in
   `config/task_types.yaml`.
9. **Wiring:** cli_wiring constructs the repo + surfacer at boot,
   bolts on the bot-aware ping poster after the bot is alive, and
   threads the surfacer through every detection site.

## Implementation Notes

- **Brainstorm gap resolutions** (final):
  - **Severity data shape:** `ToolGap` dataclass with `severity ∈ {high, speculative}`, `detection_point` literal, `blocking_capability_id` optional.
  - **Digest integration:** Extend morning digest, no standalone post.
  - **Secret scanner:** Curated regex (default) + `detect-secrets` shim opt-in.
  - **Lint AST vs grep:** AST (one rule per file under `tool_lint/`).
  - **`is_inert_at_import` location:** Helper in `donna.skills.tool_test_kit`; the lint check enforces presence of `tests/skills/tools/test_<name>.py` calling it.
  - **Snooze:** Column on `tool_request` (`snoozed_until`), not separate table.
  - **Dedup key:** Partial-unique `(user_id, tool_name) WHERE status='open'`. Re-emission upserts; once resolved, fresh row.
  - **`proposed_signature`:** Loose Python-type-hint shape (name / params / returns / summary / errors_raised). Plumbed through `claude_code_spec.render(extra_context=…)` into `tool_build.md`.

- **No tool lifecycle table.** Source code + manual merge + restart is the lifecycle. Dependent-skill regression deferred to slice 24.
- **Re-ping cooldown** (4h default) prevents spam on dedup hits.

## Test Plan

Unit + component coverage shipping in this slice (91 tests in total):

- `tests/cost/test_tool_request_repository.py` — dedup, snooze idempotency, status transitions, list filtering.
- `tests/cost/test_tool_gap_surfacer.py` — high vs speculative routing, audit trail, ping rate-limit, poster failure isolation.
- `tests/cost/tool_lint/test_anthropic_import.py` — AST positive/negative; allows under `src/donna/llm/`.
- `tests/cost/tool_lint/test_import_io.py` — module-level I/O rejected; intra-function I/O allowed; pathlib pattern; `If` block descent.
- `tests/cost/tool_lint/test_secrets.py` — provider patterns, vault naming, vault.read/environ pass-through.
- `tests/cost/tool_lint/test_metadata.py` — required fields, type checks, `requires_rebuild=True` warning.
- `tests/cost/tool_lint/test_allowlist.py` — allowlist diff, `unallowlisted=True` marker, missing-mention rejection.
- `tests/cost/tool_lint/test_inert_test.py` — file presence + AST call check.
- `tests/cost/tool_lint/test_pipeline.py` — clean / anthropic / secret / requires_rebuild warning / unallowlisted paths.
- `tests/cost/test_manual_validation_router_tool.py` — pass / lint fail / unknown request / wrong entity type.
- `tests/cost/test_runtime_tool_check.py` — missing tools, all registered, no requirements, lookup failure.
- `tests/integrations/test_tool_gap_ping_view.py` — file/snooze callbacks, owner mismatch, stale click.
- `tests/notifications/test_digest_tool_gaps.py` — speculative inclusion, high exclusion, snooze exclusion, resolved exclusion, expired snooze inclusion.
- `tests/skills/test_tool_test_kit.py` — inert pass; import-time `open()` raises.
- `tests/unit/test_capability_tool_registry_check.py` — extended for slice 22 partition logic.

Run end-to-end on a real branch:

```bash
DONNA_HOST_REPO_PATH=/path/to/repo donna  # boots, files speculative gaps for any pending_review capability
# trigger an automation requiring an unregistered tool → ping arrives in #agents
# click [File request] → tool_build.md spec lands in workspace
# build branch in worktree, commit, /donna submit <correlation_id> --branch <name>
# poller validates, tool_request → completed
```

## Open Questions

- Does the morning digest already exist? If yes: extend it to include speculative tool gaps. If no: speculative gaps wait for a future digest slice — fall back to a "Daily tool-gap summary" Discord post for now.

## Not in Scope

- Building any specific tool (browser, etc.).
- Auto-drafting tools — explicitly forbidden by §7 / §10.5.
- Multi-user tool-request dedup (Phase 2).

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, slice 21 outputs, `capability_tool_check.py`, `tool_requirements.py`, `mock_tool_registry.py`, the new `prompts/escalation/tool_build.md`.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Define "blocking severity" precisely — what's the data shape passed from `capability_tool_check` to the surfacing layer?
- [ ] Decide morning-digest integration vs standalone "tool gap" Discord post (per open question).
- [ ] Choose the secret scanner — reuse pre-commit hook config? Add `detect-secrets` library? Custom regex?
- [ ] Lint check implementation: AST-based (proper) or grep-based (cheaper)? Both should reject `import anthropic` outside `src/donna/llm/`.
- [ ] Where does `is_inert_at_import` test fixture live — `tests/tools/`? A shared `tools/conftest.py`?
- [ ] Snooze mechanic: store snooze state on `tool_request` row, or a separate `tool_request_snooze` table?
- [ ] Dedup: a re-emission of the same tool gap should bump priority + update rationale, not create a second row. Define the dedup key (`tool_name + user_id`?).
- [ ] What does `proposed_signature` JSON look like — roughly mirror Python type hints, OpenAPI shape, or something custom?

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the tool-gap protocol differ from §7? Update §7.
- [ ] Did the schema differ from §8? Update §8.
- [ ] Did the failure mitigations differ from §10.5? Update §10.5.
- [ ] Did the new prompt template differ from §9? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did `spec_v3.md §3.2` / §23.3 stubs need updating to reflect what shipped? Update them.
