# Slice 17: Escalation Core

> **Goal:** Land the foundational data model and Discord plumbing for the over-budget decision tree. Cost router emits `escalation_offered`; user sees a four-button Discord message; `pause` and `cancel` resolve correctly. `api_extended` and manual modes are stubbed (buttons not rendered) — they ship in slices 18–21.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §4 (decision tree), §6.1 (YAML defaults — kill switch + triggers), §8 (`escalation_request`, `dashboard_setting` schemas — `daily_budget_extension` lands with slice 18 but the table is created here for FK targets), §10.1 (Discord channel failures), §10.10 (audit logging for `escalation_offered`, `escalation_resolved`, `escalation_timed_out`).
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules — pause-only terminal extended here), `spec_v3.md §16.1` (Database Strategy — new tables added).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §4 — Decision tree (this slice ships only Pause + Cancel)

```
Task: <task_description>
Estimate: $<estimate>  |  Daily remaining: $<remaining>  |  Type: <task_type>

[Approve $X extension]   [Manual handoff]   [Pause]   [Cancel]
```

| Button | Effect (this slice) |
|---|---|
| **Approve $X extension** | NOT RENDERED in slice 17. Land in slice 18. |
| **Manual handoff** | NOT RENDERED in slice 17. Land in slices 20/21. |
| **Pause** | Task moves to `paused` state. Will be reconsidered tomorrow when budget refreshes. |
| **Cancel** | Task is closed without action. |

Decision flow rules:
- Only buttons whose modes are *enabled* render. Disabled "Manual handoff" never shows; user must Approve, Pause, or Cancel.
- If **all** modes are disabled by config/dashboard, only `Pause` and `Cancel` show.
- Buttons time out after `escalation_timeout_minutes` (default 60). On timeout: task moves to `paused`, log entry `escalation_timed_out`, next escalation tier (SMS via slice 7) fires if priority ≥ 4.

### §6.1 — Bootstrap YAML (`config/manual_escalation.yaml` — new file lands here)

```yaml
enabled: true                          # global kill switch

modes:
  chat:
    enabled: true
  claude_code:
    enabled: true

triggers:
  task_approval_threshold_usd: 5.0     # moved from DonnaConfig
  escalation_timeout_minutes: 60
  manual_iteration_limit: 3
```

(`budget_extension` and `prompt_delivery` blocks land with their respective slices.)

### §8 — Schemas added in this slice

```sql
CREATE TABLE escalation_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  correlation_id TEXT UNIQUE NOT NULL,
  task_id INTEGER,
  task_type TEXT NOT NULL,
  estimate_usd REAL NOT NULL,
  daily_remaining_usd REAL NOT NULL,
  offered_modes JSON NOT NULL,
  resolution TEXT,
  resolved_by TEXT,
  resolved_at TIMESTAMP,
  prompt_path TEXT,
  branch_name TEXT,
  iteration INTEGER DEFAULT 1,
  status TEXT DEFAULT 'open',
  submitted_at TIMESTAMP,
  validated_at TIMESTAMP,
  parent_escalation_id INTEGER,
  FOREIGN KEY (parent_escalation_id) REFERENCES escalation_request(id)
);

CREATE TABLE dashboard_setting (
  key TEXT PRIMARY KEY,
  value JSON NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_by TEXT NOT NULL
);

ALTER TABLE invocation_log ADD COLUMN escalation_request_id INTEGER
  REFERENCES escalation_request(id);
```

### §10.1 — Discord channel failures (mitigations to wire in this slice)

| Failure | Mitigation |
|---|---|
| Discord API down / network partition during escalation | Escalation request created in DB regardless. Cron retries delivery every 60s for up to `escalation_timeout_minutes`. SMS tier-2 fallback (slice 7) for priority ≥ 3. |
| User on mobile, didn't see ping | `escalation_timeout_minutes` default 60. Tasks below daily-pause-threshold survive timeout into `paused` state. |
| Stale button click (escalation already resolved) | Buttons carry the `correlation_id`. Click handler checks `status='open'` before mutating. |
| Replay attack (old message clicked) | Same correlation_id check + buttons disabled (component re-render) on resolution. |
| Wrong-account approval | Discord interaction `user.id` must match the configured `OWNER_DISCORD_ID`. Reject + log otherwise. |

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/superpowers/specs/manual-escalation.md` (canonical)
- `spec_v3.md §13.1`, `§16.1`, `§4.3` (invocation_log)
- `slices/slice_07_sms_escalation.md` — tier-2 fallback this slice integrates with
- `src/donna/integrations/discord_views.py` — `AgentActionView` is the pattern for the four-button view (canonical spec §14)
- `src/donna/cost/budget.py`, `tracker.py` — where the gating check goes

## What to Build

A self-contained vertical slice that lets the cost router stop a too-expensive task, ask the user "what now?" via Discord, and act on the answer. Only `Pause` and `Cancel` resolve in this slice; `Approve $X` and `Manual handoff` are deliberately not rendered (slices 18, 20, 21).

1. **Schema (one Alembic revision, three tables + one ALTER)** — `escalation_request`, `daily_budget_extension`, `dashboard_setting` per spec §8, plus `invocation_log.escalation_request_id`. Even though `daily_budget_extension` is unused until slice 18, the table lands here because (a) it carries an FK to `escalation_request`, (b) shipping it now keeps slice 18 to behavior-only changes, (c) the historical migration pattern (`alembic/versions/add_automation_tables_phase_5.py`) groups related FK tables in a single revision.
2. **`paused` task state** — add to `config/task_states.yaml` and the `TaskStatus` enum in `src/donna/tasks/db_models.py`, with two new transitions: `scheduled|in_progress → paused` (trigger: `escalation_pause` / `escalation_timed_out`) and `paused → backlog` (trigger: `daily_budget_refresh`, fired by the existing morning digest tick when a new UTC day starts). Cancelled is reached via the existing `* → cancelled` rule.
3. **`config/manual_escalation.yaml`** — exactly the §6.1 keys this slice consumes (the global kill switch + `triggers` block). `budget_extension` and `prompt_delivery` blocks are NOT added here; slices 18 and 20 own them.
4. **`EscalationGate`** — new module at `src/donna/cost/escalation_gate.py`. Sole entry point that the dispatch path calls _instead of_ `BudgetGuard.check_pre_call` for tasks that go through the cost router with an `estimate_usd`. It (a) reads `manual_escalation.enabled` and the per-task-type `manual_escalation` block, (b) computes `daily_remaining = daily_pause_threshold_usd - already_spent_today`, (c) decides whether to fire, (d) writes the `escalation_request` row, (e) posts the Discord view, (f) awaits resolution via an asyncio `Event` keyed on `correlation_id`, (g) returns the resolution to the caller. `BudgetGuard.check_pre_call` continues to exist and continues to raise `BudgetPausedError` for un-estimated paths and as a backstop.
5. **`BudgetEscalationView`** — new four-button discord.py view modelled on `AgentApprovalView` (`src/donna/integrations/discord_views.py:453`). Buttons rendered conditionally from the `offered_modes` list passed at construction. For slice 17 only `Pause` and `Cancel` ever appear; the other two button slots have placeholders disabled at the call site (NOT rendered). Each button's interaction handler:
   - rejects if `interaction.user.id != OWNER_DISCORD_ID` (logs `escalation_owner_mismatch`),
   - re-reads the row and aborts ephemerally if `status != 'open'` (stale-click guard),
   - mutates the row, writes the `escalation_resolved` audit entry, sets the `Event`, and disables the view.
6. **`OWNER_DISCORD_ID` env var** — sourced from `os.getenv("DONNA_OWNER_DISCORD_ID")`, loaded once at bot startup and threaded into the view factory. Crash on boot if the env var is missing AND `manual_escalation.enabled=true`. Documented in `docker/.env.example`.
7. **Discord delivery retry loop** — a single background coroutine (`escalation_delivery_loop`) launched from the orchestrator alongside `EscalationManager.check_and_advance` (existing pattern in `src/donna/notifications/escalation.py:158`). Polls `escalation_request WHERE status='open' AND delivery_status IN (NULL,'failed') AND submitted_at IS NULL` every 60s, retries the post, gives up at `escalation_timeout_minutes`. **Schema addendum** vs spec §8: add `delivery_status TEXT`, `delivery_attempts INTEGER DEFAULT 0`, `last_delivery_attempt_at TIMESTAMP` to `escalation_request` so the loop is queryable. Call this out in the Spec Drift section.
8. **Timeout handler** — same loop also processes `status='open' AND now > created_at + escalation_timeout_minutes`. Sets `resolution='pause'`, `resolved_by='timeout'`, writes `escalation_timed_out` audit entry, transitions the task to `paused`, and — if `task.priority >= 4` — calls the existing `EscalationManager.escalate(..., start_at_tier=2)` (`src/donna/notifications/escalation.py:99`) to fan out via SMS. The `≥ 4` threshold is the §4 timeout-case rule; the spec's separate `≥ 3` rule (§10.1 row 1) only applies when Discord *delivery* fails and is intentionally out of scope here.
9. **`DashboardSettingResolver` (read-only)** — small async helper, ~30 lines. `get(key, default)` reads from `dashboard_setting` first, falls back to YAML/`ManualEscalationConfig`. No write path, no UI; both ship in slice 23. Wired into `EscalationGate` so toggle flips during slice-18–22 testing don't require a restart.
10. **Audit entries** — every state transition writes an `invocation_log` row with `task_type='escalation_lifecycle'`, `model_alias='audit'`, `cost_usd=0`, `tokens_in=tokens_out=0`, and a JSON `output` payload. Events: `escalation_offered`, `escalation_resolved`, `escalation_timed_out`. The new `escalation_request_id` FK column is populated on each.

Out of scope for this slice (deferred to numbered slices): `api_extended` button + grant flow, `chat`/`claude_code` mode rendering, dashboard surfaces, tool-gap detection, dashboard write path, prompt body persistence, Ollama summariser.

## Implementation Notes

**Resolved brainstorm gaps**

| Gap | Decision |
|---|---|
| 1. `OWNER_DISCORD_ID` source | Env var `DONNA_OWNER_DISCORD_ID`. Matches existing `DONNA_BOOTSTRAP_ADMIN_EMAIL` / `TWILIO_*` env-var convention (`config/auth.yaml`, `config/sms.yaml`). `config/auth.yaml` has no Discord block today; not adding one in this slice. The `vault.py` module is for markdown notes, not secrets — wrong tool. Boot fails loudly if the var is unset while `manual_escalation.enabled=true`. |
| 2. Correlation ID format | UUIDv7 via `uuid6.uuid7()`. Already a project dependency and used at `src/donna/notifications/escalation.py:381`. Sortable, no new dep. |
| 3. Where the gating check lives | New `EscalationGate` module, not a `BudgetGuard` extension. `BudgetGuard.check_pre_call` is post-hoc (already-spent vs threshold) and raises `BudgetPausedError`; the new gate is estimate-driven (`estimate_usd > min(remaining, approval_threshold)`) and blocks asynchronously on user input. Different inputs, different control flow, different failure mode — separate class. Both stay wired; `BudgetGuard` remains the backstop for paths without an estimate. |
| 4. 60s Discord retry mechanism | Single polling coroutine, not per-request `asyncio.create_task`. Mirrors `EscalationManager.check_and_advance` (`src/donna/notifications/escalation.py:158`) which is the established pattern. Survives bot restarts because state lives in the row, not in a coroutine. `AsyncCronScheduler` (`src/donna/skills/crons/scheduler.py:27`) is wrong granularity — it's day-level. |
| 5. Dashboard setting read layer | Ships in this slice as **read-only**. The slice brief recommended this and the explore confirmed no resolver exists. Slices 18–22 need a way to flip toggles without rebuilding YAML; SQL inserts into `dashboard_setting` is the cheapest way. Write path + UI defer to slice 23. |
| 6. SMS tier-2 wiring | Reuse `EscalationManager.escalate(..., start_at_tier=2)` — exact API already supported. Threshold `priority >= 4` per spec §4. Note the spec internal inconsistency: §4 says ≥ 4 (timeout case), §10.1 row 1 says ≥ 3 (delivery-failure case). They are different triggers. Slice 17 implements the timeout path (≥ 4) only; the delivery-failure path is not yet wired (the retry loop handles redelivery within the 60-min window; if Discord stays down past that, the timeout handler picks it up and the ≥ 4 rule applies). Flag in PR for spec author to confirm. |
| 7. `paused` state semantics | New state. Not present in `config/task_states.yaml` today (states: backlog, scheduled, in_progress, blocked, waiting_input, done, cancelled). Add to YAML + `TaskStatus` enum + state-machine transitions (`scheduled→paused`, `in_progress→paused` on escalation events; `paused→backlog` on the daily refresh tick; existing `*→cancelled` rule covers cancellation). Reschedule count is NOT incremented on pause (this isn't a user reschedule). |
| 8. Alembic revision granularity | One revision for slice 17 covering all three new tables + the `invocation_log` ALTER. Historical pattern (`add_automation_tables_phase_5.py` ships `automation` + `automation_run` together) supports grouped FK tables. Spec §8 says "one per table" but that's contradicted by historical practice; flag for spec update in the drift checklist. |

**Module placement**

| File | Purpose |
|---|---|
| `src/donna/cost/escalation_gate.py` | New. `EscalationGate` class. |
| `src/donna/cost/escalation_models.py` | New. SQLAlchemy models for `escalation_request`, `daily_budget_extension`, `dashboard_setting`. |
| `src/donna/cost/escalation_repository.py` | New. Async aiosqlite CRUD (mirrors the `Database` pattern in `src/donna/tasks/database.py`). |
| `src/donna/cost/dashboard_setting.py` | New. `DashboardSettingResolver` (read-only). |
| `src/donna/integrations/discord_views.py` | Modify. Add `BudgetEscalationView`. Reuse the `AgentApprovalView` style. |
| `src/donna/integrations/discord_bot.py` | Modify. Plumb `OWNER_DISCORD_ID` from env into the bot; expose a `send_budget_escalation(view, content)` helper. |
| `src/donna/notifications/escalation_delivery_loop.py` | New. The 60s polling coroutine. Lives next to existing `escalation.py` for cohesion. |
| `src/donna/orchestrator/runtime.py` (or wherever existing background tasks register) | Modify. Launch `escalation_delivery_loop` alongside `EscalationManager.check_and_advance`. |
| `src/donna/tasks/db_models.py` | Modify. Add `paused` to `TaskStatus`, add `escalation_request_id` to `InvocationLog` mapped column. |
| `config/task_states.yaml` | Modify. Add `paused` state and its two transitions. |
| `config/manual_escalation.yaml` | New. §6.1 subset: `enabled` + `triggers`. |
| `alembic/versions/<rev>_escalation_core.py` | New. Single revision. |

**Locking and concurrency**

- `BudgetEscalationView` button handlers and the timeout coroutine race for the same row. Resolution uses `UPDATE escalation_request SET status='resolved', resolution=?, resolved_by=? WHERE id=? AND status='open'` and treats `rowcount=0` as "lost the race"; the loser surfaces an ephemeral "already resolved" reply on the click path or a no-op log on the timeout path.
- `EscalationGate.fire_and_wait()` returns a `Resolution` dataclass `{mode, resolved_by, escalation_request_id}`. The caller turns `pause` / `cancel` into the right task transition. `mode='api_extended'`, `'chat'`, `'claude_code'` are not yet returnable from this slice — the gate raises `NotImplementedError` if the row is somehow resolved with those (defensive; UI can't render the buttons).

**Audit log shape**

```python
await invocation_log.write(
    id=str(uuid6.uuid7()),
    task_type="escalation_lifecycle",
    task_id=task_id,                      # may be None
    model_alias="audit",
    model_actual="audit",
    input_hash=correlation_id,            # 16-char prefix; correlation_id is the canonical key
    latency_ms=0,
    tokens_in=0, tokens_out=0, cost_usd=0.0,
    output={"event": "escalation_offered", "modes": ["pause","cancel"], "estimate_usd": ..., "remaining_usd": ...},
    user_id=user_id,
    escalation_request_id=row.id,
)
```

The `task_type='escalation_lifecycle'` rows are excluded from cost aggregation today by `CostTracker.get_daily_cost(exclude_task_types=...)` — pass `'escalation_lifecycle'` alongside the existing `'external_llm_call'` exclusion in `BudgetGuard.check_pre_call`.

**Spec drift to flag in the PR**

- Spec §8 says "one revision per table"; we ship one revision for three tables. Update §8.
- `escalation_request` gains three new columns (`delivery_status`, `delivery_attempts`, `last_delivery_attempt_at`) needed to make the retry loop queryable. Update §8.
- Spec §6.1 puts `task_approval_threshold_usd` under `triggers:` in `manual_escalation.yaml`; the value also exists today in `donna_models.yaml` via `CostConfig.task_approval_threshold_usd`. Slice 17 reads it from `manual_escalation.yaml` (new) and treats `CostConfig.task_approval_threshold_usd` as deprecated-but-still-loaded for backward compat. Slice 23 will delete the `CostConfig` field. Note in §6.1.
- Spec §10.1 row 1 (≥ 3) vs §4 (≥ 4) priority threshold for the SMS fallback — flag for the spec author. Slice 17 implements ≥ 4 for the timeout path.

## Test Plan

**Unit tests** (`tests/cost/`)

- `test_escalation_gate.py`
  - `fires_when_estimate_exceeds_remaining` — daily_remaining=$2, estimate=$3, threshold=$5 → row written, view posted, awaits.
  - `fires_when_estimate_exceeds_threshold` — remaining=$50, estimate=$8, threshold=$5 → fires.
  - `does_not_fire_when_under_both` — remaining=$50, estimate=$1, threshold=$5 → returns immediately, no row.
  - `does_not_fire_when_kill_switch_off` — `manual_escalation.enabled=false` → falls through to `BudgetGuard` semantics.
  - `task_type_without_manual_block_offers_only_pause_cancel` — confirms button list = `['pause','cancel']`.
  - `pause_resolution_returns_paused_mode_and_writes_audit`
  - `cancel_resolution_returns_cancelled_mode_and_writes_audit`
  - `dashboard_override_disables_kill_switch` — `dashboard_setting('manual_escalation.enabled', false)` short-circuits even when YAML says true.
- `test_escalation_repository.py`
  - CRUD round-trip for `escalation_request`, including `offered_modes` JSON serialization.
  - `resolve` is idempotent: calling twice with the same `(id, resolution)` returns the same `(mode, resolved_by)` and emits exactly one audit row.
- `test_dashboard_setting_resolver.py`
  - falls back to YAML when key missing.
  - prefers row when present.
  - bool / int / JSON-list value coercion.

**Integration tests** (`tests/integration/`)

- `test_escalation_view_buttons.py` — pytest-discord fixture (or in-process discord.py test harness used by existing view tests).
  - `pause_button_resolves_row` — synthetic interaction → row mutated, view stops, ephemeral reply sent.
  - `cancel_button_resolves_row` — same.
  - `wrong_user_id_rejected` — `interaction.user.id != OWNER_DISCORD_ID` → ephemeral "not authorized", row stays `open`, audit entry `escalation_owner_mismatch` written.
  - `stale_click_returns_already_resolved` — second click after pause → ephemeral "already resolved", no second audit.
- `test_escalation_delivery_loop.py`
  - `retries_on_post_failure` — Discord client raises HTTP 500 first, succeeds second; row's `delivery_attempts == 2`, `delivery_status == 'sent'`.
  - `times_out_after_minutes` — `escalation_timeout_minutes=1`, no click → row resolved as `pause`/`timeout`, task transitions to `paused`.
  - `timeout_with_priority_4_fires_sms` — task `priority=4` → `EscalationManager.escalate(..., start_at_tier=2)` is invoked (mock).
  - `timeout_with_priority_3_does_not_fire_sms` — task `priority=3` → SMS tier not invoked.
- `test_paused_state_machine.py`
  - `scheduled_to_paused_transition_valid`
  - `in_progress_to_paused_transition_valid`
  - `paused_to_backlog_via_daily_refresh_valid`
  - `done_to_paused_rejected` (invariant: terminal states don't escalate)
- `test_invocation_log_escalation_fk.py`
  - migration upgrades cleanly on a fixture DB containing pre-existing `invocation_log` rows; column is nullable and old rows survive.

**Migration test** (`tests/migrations/`)

- `test_slice_17_migration.py` — apply revision against an empty DB, against a copy of the latest dev DB; downgrade restores prior state. Uses the established Alembic test pattern in the existing migration test files.

**E2E smoke** (`tests/e2e/`)

- `test_escalation_pause_e2e.py` — stand up the orchestrator with a stub Discord, queue a task whose router estimate > remaining, simulate the user clicking `Pause`, assert: (a) escalation row resolved, (b) task transitioned to `paused`, (c) `invocation_log` carries `escalation_offered` and `escalation_resolved` entries with the same `escalation_request_id`, (d) no SMS fired (task priority below threshold).

**Acceptance gates** (must hold before merging)

- `pytest tests/cost tests/integration/test_escalation_view_buttons.py tests/integration/test_escalation_delivery_loop.py tests/integration/test_paused_state_machine.py` green.
- `alembic upgrade head` and `alembic downgrade -1 && alembic upgrade head` both succeed against `donna_tasks.db`.
- Manual QA: real Discord channel, real bot, real over-budget task → see four-button message (with two buttons shown), click `Pause`, see ephemeral confirmation, see task move to `paused` in the dashboard task list.
- `ruff check .` and `mypy src/donna` clean.

## Open Questions

- See spec §12 — open questions 1, 2, 5 are most relevant to this slice.

## Not in Scope

- `api_extended` button + grant logic (slice 18).
- Dashboard rendering of escalations (slice 19).
- `chat` and `claude_code` modes (slices 20, 21).
- Tool gap surfacing (slice 22).
- Dashboard runtime overrides UI (slice 23).

## Session Context

Load only: `CLAUDE.md`, this slice brief, the canonical spec (`docs/superpowers/specs/manual-escalation.md`), `spec_v3.md §13.1 + §16.1`, `slices/slice_07_sms_escalation.md`, the existing `discord_views.py`, `cost/budget.py`, `cost/tracker.py`.

## Brainstorm Gaps (resolved 2026-05-05)

> Brainstorm pass complete. See "Implementation Notes → Resolved brainstorm gaps" above for the rationale matrix. Summary below.

- [x] `OWNER_DISCORD_ID` source — **env var `DONNA_OWNER_DISCORD_ID`** (matches existing env-var convention; `auth.yaml` not extended in this slice).
- [x] Correlation ID format — **UUIDv7 via `uuid6.uuid7()`** (already in use at `src/donna/notifications/escalation.py:381`).
- [x] Cost-router gating location — **new `EscalationGate` module**; `BudgetGuard` retained as backstop for un-estimated paths.
- [x] 60s Discord retry mechanism — **single polling coroutine** (`escalation_delivery_loop`) modeled on `EscalationManager.check_and_advance`. Per-request `asyncio.create_task` rejected (lost on bot restart). `AsyncCronScheduler` rejected (wrong granularity).
- [x] `dashboard_setting` resolution layer — **ships here as read-only**. Write path + UI defer to slice 23.
- [x] Timeout → SMS tier-2 wiring — **reuse `EscalationManager.escalate(start_at_tier=2)`**, gated on `priority >= 4` per spec §4. Spec internal inconsistency between §4 (≥ 4) and §10.1 row 1 (≥ 3) flagged for the spec author.
- [x] `paused` task semantics — **new state**. Adds two transitions: `scheduled|in_progress → paused` (escalation pause/timeout), `paused → backlog` (daily budget refresh tick).
- [x] Alembic revision granularity — **one revision for all three tables + ALTER**. Matches historical pattern (`add_automation_tables_phase_5.py`); spec §8 wording updated in the drift checklist.

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR. If the drift is intentional but the spec update is out of scope, call it out explicitly in the PR description so it can be reconciled later rather than silently drifting."*

Drift checklist for this slice:

- [x] Schema differs from §8 — three columns added (`delivery_status`, `delivery_attempts`, `last_delivery_attempt_at`), `created_at` and `priority` added, multi-table revision rule clarified. **§8 updated.**
- [x] Config keys match §6.1 (slice 17 subset). No drift.
- [x] §10.1 priority threshold for SMS fan-out is internally inconsistent with §4. **§15 entry added** documenting that slice 17 implements §4's ≥ 4; reconciliation deferred to slice 24.
- [ ] Acceptance criteria — no adjustment needed for slice 17 scope.
- [x] §15 decisions — three new entries dated 2026-05-05 (correlation ID format, retry loop pattern, SMS threshold, owner-ID source).
- [ ] `spec_v3.md §13.1 / §16.1` stubs — no update needed; they already forward-link to this spec.
