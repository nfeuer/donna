# Manual Assistance & Budget Extension

> **Canonical spec.** This document is the authoritative design for
> Donna's over-budget decision tree, manual assistance modes (chat /
> claude_code), and tool-gap surfacing protocol. All implementation
> slices realize specific sections here. Drift in code from this spec
> must be reconciled in the same PR — see the slice briefs in
> `slices/slice_17_*` through `slices/slice_24_*` for the
> per-slice drift checklist.
>
> **Upstream cross-references** — sections in `spec_v3.md` that this
> spec extends or replaces:
> - `§13.1` Budget Rules — pause-only terminal replaced by §4 below.
> - `§16.1` Database Strategy / Schemas — adds `escalation_request`,
>   `daily_budget_extension`, `dashboard_setting`, `tool_request`
>   tables (§8 below).
> - `§3.2` / `§23.3` Tool Registry — tool-gap surfacing protocol (§7).
>
> **Implementation:** broken into 8 slices (§13). Each slice gets a
> skeleton brief in `slices/` pre-populated with the relevant
> excerpts here, with the implementation specifics worked out via
> the **superpowers brainstorm skill** slice-by-slice before any code
> is written.

---

## 1. Context

Donna's $100/month Claude API cap and $20/day pause threshold (`spec_v3.md §13.1`)
mean expensive workloads — primarily **skill drafting**, **skill
evolution**, and **chat escalation** for hard questions — can exhaust
the daily envelope before noon. Today the only over-budget behavior is
"pause." That's a hard stop: tasks queue up, Donna can't make progress,
and the user finds out only when nothing happens.

Two parallel mechanisms can keep work moving without raising the API
cap:

1. **Budget extension** — user explicitly approves a one-shot spend
   above the daily cap from Discord. Money goes through API as normal.
2. **Manual assistance** — Donna packages the work as a prompt; the
   user runs it themselves through Claude Code (subscription-tier,
   ToS-clean) or pastes into chat. Result flows back to Donna via git
   or Discord.

This spec defines both as a **unified over-budget decision tree**, the
data model and protocols supporting it, the toggles that gate it, and
the failure modes it must handle. It also handles one related case:
**tool gaps** — when a capability requires a tool that doesn't exist —
which always escalate (Donna cannot autonomously build tools) but use
a different surfacing protocol.

---

## 2. Goals / Non-Goals

### Goals
- Single decision tree triggered when `estimate > daily_budget_remaining`
  OR `estimate > task_approval_threshold_usd`.
- Three terminal modes per task: `api` (extension approved), `chat`,
  `claude_code`. Plus `paused` as the always-available fallback.
- Per-task-type configuration of which manual mode applies.
- All toggles surfaced in the admin dashboard; YAML files hold
  bootstrap defaults only.
- Every escalation, approval, and outcome logged to `invocation_log`
  with a stable correlation ID.
- ToS compliance: Donna never drives Claude Code programmatically.
  User is always the operator.
- Multi-user-ready: escalations are scoped to `user_id`, even though
  Phase 1 has only Nick.

### Non-Goals
- Building the browser tool or any specific tool (out of scope).
- Replacing AutoDrafter — manual `claude_code` mode is an *alternate*
  drafter, selected when over budget.
- Multi-approver workflows. One user, one decision.
- Programmatic OAuth-token reuse, tmux injection into Claude Code, or
  any approach Anthropic's terms forbid.

---

## 3. Where this fits in the docs

| Doc | Role |
|---|---|
| `docs/superpowers/specs/manual-escalation.md` (this file) | Canonical spec. |
| `spec_v3.md §13.1` (Budget Rules) | Forward-link to this spec; pause-only terminal will be replaced when slice 17 lands. |
| `spec_v3.md §3.2` / `§23.3` (Tool registry) | Forward-link to §7 of this spec. |
| `spec_v3.md §16.1` (Schemas) | Forward-link to §8 of this spec for the new tables. |
| `docs/workflows/handle-budget-breach.md` | Forward-link to the new decision tree. |
| `docs/domain/skill-system.md` | "Manual escalation" subsection — references this spec. |
| `docs/domain/task-system.md` | Reference to escalation as a task lifecycle terminal. |
| `docs/domain/management-gui.md` | Description of new dashboard surfaces (toggle panel + escalation workspace). |

PR descriptions for any implementation slice MUST cite the relevant `§` of this spec.

---

## 4. The over-budget decision tree

When a task is about to be dispatched, the cost router computes
`estimate_usd`. If `estimate_usd > min(daily_budget_remaining,
task_approval_threshold_usd)` AND escalation is enabled (see §6),
Donna posts a single Discord message with up to four buttons:

```
Task: <task_description>
Estimate: $<estimate>  |  Daily remaining: $<remaining>  |  Type: <task_type>

[Approve $X extension]   [Manual handoff]   [Pause]   [Cancel]
```

| Button | Effect |
|---|---|
| **Approve $X extension** | Adds `estimate_usd` (rounded up) as a one-shot increase to today's budget envelope. Task runs on API. Log entry: `escalation_resolved` with `mode=api_extended`. |
| **Manual handoff** | Branches by task_type's `manual_escalation_mode` (chat or claude_code). Task is parked in `escalation_request` table; protocol §5.2 or §5.3 begins. |
| **Pause** | Task moves to `paused` state. Will be reconsidered tomorrow when budget refreshes. |
| **Cancel** | Task is closed without action. |

**Decision flow rules:**
- Only buttons whose modes are *enabled* render. Disabled "Manual
  handoff" never shows; user must Approve, Pause, or Cancel.
- If **all** modes are disabled by config/dashboard, only `Pause` and
  `Cancel` show — this is the documented "API-only" lockdown state.
- Buttons time out after `escalation_timeout_minutes` (default 60). On
  timeout: task moves to `paused`, log entry `escalation_timed_out`,
  next escalation tier (SMS via `slice_07_sms_escalation.md`) fires
  if priority ≥ 4.

---

## 5. Modes

### 5.1 `api_extended` (existing path + spend approval)

- Recipient: existing `complete()` gateway.
- Side effect: `daily_budget_extension` entry inserted, today's
  effective cap = base cap + sum(extensions).
- Hard ceiling: cumulative extensions per day cannot exceed
  `max_daily_extension_usd` (default $10). Extensions over ceiling
  surface a "Pause / Cancel" only choice.
- Audit: `escalation_request.resolution = 'api_extended'`; the
  resulting API call's `invocation_log` row carries the
  `escalation_request_id` foreign key.

### 5.2 `chat` mode (text-only round trip)

Used for `task_types` whose output is pure text: `chat_escalation`,
high-context Q&A, advice, summarization.

**Surface split:** Discord is the *alert*, the **admin dashboard is
the canonical workspace** for chat-mode escalations. This solves
Discord's 2000-char message limit definitively (full prompts can be
arbitrarily long) and gives a structured submit path instead of
paste-into-thread.

**Donna → user:**
1. Render the prompt template; store full prompt as a row in
   `escalation_request.prompt_body` (TEXT) and on disk at
   `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md` for
   off-dashboard access.
2. Generate a short summary (1–3 sentences) via local Ollama
   (no API cost) — title, gist, estimate, daily remaining.
3. Discord notification message contains:
   - The summary
   - Correlation ID
   - Direct link to dashboard escalation page:
     `https://<host>/admin/escalations/<correlation_id>`
   - The `[Approve $X / Manual / Pause / Cancel]` buttons (§4)
   - **Optional attachment** of the full prompt as
     `<correlation_id>.md` — controlled by config flag
     `discord.attach_full_prompt` (default `true`). Discord free-tier
     allows up to 25 MB attachments; markdown is kilobytes, so always
     fits in practice. Useful when the user is on mobile and wants to
     read without opening the dashboard.

**User → Donna (dashboard primary path):**
1. Click Discord link or open dashboard `/admin/escalations`.
2. Dashboard escalation detail page shows:
   - Full prompt (syntax-highlighted markdown render)
   - One-click **Copy prompt** button
   - Estimate, daily remaining, task_type, age
   - Status timeline (offered → submitted → validated)
   - Large textarea for pasting the answer back
   - **Submit** button (disabled until textarea non-empty)
3. User pastes the prompt into claude.ai (or wherever) externally,
   pastes the answer into the textarea, clicks Submit.
4. Dashboard POSTs to `/admin/escalations/<correlation_id>/submit`
   with the answer payload. Server validates, writes
   `escalation_request.result`, marks `status='submitted'`, then
   ingestion poller (§5.3 pattern) picks up.

**User → Donna (Discord fallback path):**
- `/donna_submit <correlation_id> <answer>` slash command (the
  on-the-wire name has no space). Discord's per-option limit is
  ~6000 chars, but the server enforces a tighter
  ``prompt_delivery.slash_command_max_chars`` (default 3000) so
  there is headroom for metadata and so the user gets a clear "use
  dashboard for long answers" instead of Discord's generic truncation.
  Useful for mobile when desktop isn't available.
- Min length is ``prompt_delivery.chat_min_answer_chars`` (default
  50), matching the JSON schema's ``minLength``.
- The slash command goes through the same shared
  :func:`donna.cost.escalation_submit_service.apply_submission`
  helper as the dashboard endpoint so validation, optimistic locking,
  and audit log writes are identical.

**Result ingestion (slice 20):** `donna.skills.chat_escalation_ingestion_poller.ChatEscalationIngestionPoller`
polls every 30 seconds for ``mode='chat' AND status='submitted' AND task_id IS NOT NULL``
rows. For each row it appends ``[escalation:<correlation_id>] <answer>``
to the originating task's notes, transitions the task to ``done``,
flips the escalation row to ``status='validated'``, and writes an
``escalation_validated`` audit row. Failures leave the row in
``submitted`` so the next tick retries.

**Failure handling:** see §10.

### 5.3 `claude_code` mode (file artifact)

Used for `task_types` whose output is code or files. Slice 21 ships
the **skill** path (`skill_auto_draft`, `skill_evolution`); slice 22
adds `tool_request_fulfillment` using the same protocol with extra
lint gates. (Note: the literal task type names match
`config/task_types.yaml`: `skill_auto_draft` and `skill_evolution` —
the brief earlier referred to `skill_draft`, which is the same task
under its old name.)

**Surface split:** same as §5.2 — dashboard is canonical workspace,
Discord is alert. Spec file is also written to disk because the user
needs filesystem access to do the work anyway.

**Preconditions for the `claude_code` button to render:**
1. `manual_escalation.modes.claude_code.enabled` (dashboard → YAML).
2. The host repo is mounted read-only at the path named by
   `manual_escalation.modes.claude_code.host_repo_path_env` (default
   `DONNA_HOST_REPO_PATH`). If unset / not a git repo: button is not
   offered (logged once at boot, fail-soft).
3. The per-task-type `manual_escalation.mode == "claude_code"` block
   is set in `config/task_types.yaml` (with `target_paths` +
   `reference_module`).

When the gate fires, it captures `base_sha = git rev-parse refs/heads/main`
and the rendered (un-substituted) `target_paths` onto the row so a
mid-flight config change can't widen scope retroactively (§10.7 row 2).

**De-dup:** before creating a new `claude_code` escalation row, the
gate looks for an existing row with the same
`(originating_entity_type, originating_entity_id)` whose status is in
`{open, resolved, submitted, failed}`. If one exists, the gate
re-delivers the existing notification and refuses to open a parallel
race. Prevents two worktrees editing the same skill file.

**Donna → user:**
1. Write spec file to
   `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md`. Contents
   (rendered from `prompts/escalation/skill_draft.md`):
   - Task summary
   - Acceptance criteria
   - Target file paths (`{name}`-substituted from
     `originating_entity_id` → capability_name lookup)
   - Reference module path (existing skill/tool to mimic)
   - Exact `git worktree add -b <branch> <worktree_path> <base_sha>`
     command (pinned to a specific main SHA so subsequent merges
     don't move the floor)
   - Forbidden patterns (e.g., "do not embed secret values; use
     `vault.read('<name>')`")
2. Mirror the spec into `escalation_request.prompt_body`. Snapshot the
   substituted `target_paths` and `base_sha` onto the row.
3. Discord notification:
   - Short summary
   - Correlation ID + dashboard link
   - Optional MD attachment of the spec
   - `[Approve $X / Claude Code / Pause / Cancel]` buttons (§4) — the
     Claude Code button replaces the legacy "Manual handoff" label
     when claude_code is the offered manual mode.

Dashboard escalation detail page for `claude_code` mode shows:
   - Full spec (rendered)
   - **Copy prompt** button — paste straight into Claude Code
   - Pre-filled `git worktree add` command + branch + base SHA + each
     target path glob in a copy-on-click grid
   - **Mark as built** button — opens a modal accepting branch name
     (required) and SHA (optional)
   - Validation result panel (populated post-submission with pass/fail
     per fixture, lint outcomes)
   - **Ready to merge** panel after `status='validated'`: copyable
     `git checkout main && git merge --no-ff <branch> && git push`
     line and a **Mark as merged** button (pure tracking write — Donna
     never auto-merges)
   - **Needs human review** banner when iteration cap is reached and
     `human_review=1`.

**User → Donna:**
1. User opens dashboard escalation page, copies the worktree command.
2. User runs `git worktree add` per the spec.
3. User opens Claude Code in the worktree, pastes spec into the
   prompt. Claude Code reads the reference module, writes skill files
   under `skills/<name>/` (skill.yaml + steps + schemas) AND fixture
   cases under `fixtures/<name>/case*.json`, runs `pytest`.
4. User commits on the branch (push optional — orchestrator reads
   the host repo via the read-only mount).
5. User clicks **Mark as built** in the dashboard (or
   `/donna submit <correlation_id> --branch <name> [--sha <sha>]` from
   Discord as fallback). The slash command and the dashboard route
   share `donna.cost.escalation_submit.submit_escalation_core` so
   schema, mode-mismatch, iteration-cap and concurrent-submission
   guards are identical across surfaces.

**Donna ingestion (`ClaudeCodePoller`, mirrors `EscalationDeliveryLoop`):**
1. Polls `escalation_request` rows where
   `mode='claude_code' AND status='submitted'` every 60 s (configurable
   via `manual_escalation.modes.claude_code.poll_tick_seconds`).
2. Verifies branch exists (`git rev-parse`) in the host repo. If
   absent: posts "branch not found, did you push?" feedback to
   Discord; status stays `submitted` so a later push triggers
   re-ingestion (no iteration burn).
3. If a SHA was supplied at submit time, verifies it matches the
   current branch tip (force-push protection, §10.3 row 4).
4. Diffs `base_sha..tip` (committed only — working tree ignored,
   §10.3 row 5) and rejects out-of-scope paths via `DiffValidator`.
   Globs ending in `/**` are treated as recursive prefixes; dotfile
   additions are always rejected.
5. Hands off to `ManualValidationRouter`:
   - Skills: read skill.yaml + steps + schemas + fixtures from the
     branch via `git show` (read-only — never checks out into the
     host repo). Persist as a fresh `skill_version` (claude_native).
     Run `ValidationExecutor` against the committed fixtures via
     `validate_against_fixtures` (same call shape AutoDrafter uses).
     Threshold: `config.auto_draft_fixture_pass_rate`.
   - Tools: slice 22 — currently raises `NotImplementedError`.
6. On pass: lifecycle hops `claude_native → skill_candidate → draft
   → sandbox`. The final hop carries `reason='human_approval'`,
   `actor='user'`, `actor_id=<discord_id>` because the user's
   "Mark as built" click + green fixtures *is* the human approval —
   manual mode lands one hop deeper than AutoDrafter (which ends in
   `draft` and needs a separate approval). Audit:
   `escalation_validated`. Discord feedback names the skill_id and
   pass rate, plus the merge-when-ready hint.
7. On failure: status `submitted → failed`, Discord feedback (short
   summary: skill_id, pass rate, ≤ 3 failing case names + dashboard
   link — full per-fixture output stays in the row's
   `validation_result` JSON for the dashboard panel). User iterates
   in the same worktree, resubmits via dashboard or `/donna submit`.
   Iteration counter increments only on the `failed → submitted`
   resubmit path (a clean first submission stays at 1).
8. Iteration cap sweep (separate routine inside the same poller
   tick): rows with `status='failed' AND iteration >= manual_iteration_limit
   AND human_review = 0` get promoted to `status='cancelled'` with
   `human_review=1`. Audit: `iteration_limit_reached`. Dashboard
   shows the **Needs human review** banner.

**Boundaries (host repo / merging):**
- Donna **never writes** to the host repo. The mount is read-only.
  Donna's only writes for claude_code mode are the spec markdown file
  under `${WORKSPACE}/escalations/` (off the source tree) and DB rows.
- After validation, the user merges manually
  (`git checkout main && git merge --no-ff <branch> && git push`).
  The dashboard "Mark as merged" button is a tracking-only write that
  flips `merged_at`; it does not invoke git.
- Re-escalations against the same `originating_entity_id` while a
  prior row is still in-flight are de-duped at the gate (see
  Preconditions above).

---

## 6. Toggle architecture

Two layers: **YAML defaults** (bootstrap) and **dashboard runtime
overrides** (canonical at runtime).

### 6.1 YAML defaults (`config/manual_escalation.yaml` — new)

```yaml
enabled: true                          # global kill switch

modes:
  chat:
    enabled: true
  claude_code:
    enabled: true
    # Slice 21 — claude_code mode runtime configuration. Sibling-of-
    # workspace path layout. The host repo is mounted via the env var
    # named here; the orchestrator never writes to it.
    worktree_root: "${DONNA_WORKSPACE_PATH}/worktrees"
    host_repo_path_env: "DONNA_HOST_REPO_PATH"
    base_ref: "main"
    feedback_max_failing_cases: 3
    poll_tick_seconds: 60

budget_extension:
  enabled: true
  max_daily_extension_usd: 10.0
  hard_monthly_ceiling_usd: 150.0      # absolute cap, dashboard cannot exceed

triggers:
  task_approval_threshold_usd: 5.0     # moved from DonnaConfig
  escalation_timeout_minutes: 60
  manual_iteration_limit: 3

prompt_delivery:
  attach_full_prompt_to_discord: true   # Discord MD attachment alongside summary
  discord_summary_max_chars: 1500       # safety margin under 2000 char message limit
  attachment_size_limit_mb: 25          # Discord free-tier ceiling; MD never approaches this
  workspace_subdir: escalations         # slice 20: subdir under ${DONNA_WORKSPACE_PATH}
  slash_command_max_chars: 3000         # slice 20: /donna submit hard cap (§10.3)
  chat_min_answer_chars: 50             # mirrors schemas/escalation_submission.json
```

The slice 20 build adds three keys to the original block. ``workspace_subdir``
was implicit in §5.2 (`${DONNA_WORKSPACE_PATH}/escalations/`); it is exposed so
future deployments can change the workspace layout without code edits.
``slash_command_max_chars`` is the server-side ceiling for the
``/donna submit`` slash command — anything over that redirects to the
dashboard. ``chat_min_answer_chars`` mirrors the JSON schema's ``minLength``
so the dashboard, the slash command, and the schema layer all enforce the
same minimum.

The full prompt **always** lives in `escalation_request.prompt_body`
and on disk under `${DONNA_WORKSPACE_PATH}/escalations/`. The
dashboard renders from the DB row. Discord only ever carries the
*summary* (text) plus an *optional attachment* (the full prompt as
.md). This eliminates the 2000-char Discord problem entirely — there
is no scenario where Donna tries to inline a long prompt into a
Discord message body.

### 6.2 Per-task-type config (`config/task_types.yaml` extension)

```yaml
# Slice 21 actual layout: skills are filesystem-rooted directories
# under skills/<name>/ (skill.yaml + steps/*.md + schemas/*.json) per
# src/donna/skills/loader.py — fixture cases live under
# fixtures/<name>/case*.json. Globs ending in /** are recursive
# prefix matches enforced by DiffValidator.
task_types:
  skill_auto_draft:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill:    "skills/{name}/**"
        fixtures: "fixtures/{name}/**"
      reference_module: "skills/parse_task/skill.yaml"
      forbidden_patterns:
        - "import anthropic"
        - "DONNA_API_KEY"
  chat_escalation:
    manual_escalation:
      mode: chat
  skill_evolution:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill:    "skills/{name}/**"
        fixtures: "fixtures/{name}/**"
      reference_module: "skills/{name}/skill.yaml"        # in-place edit
      forbidden_patterns:
        - "import anthropic"
```

Task types without a `manual_escalation` block are **never** offered
manual mode — only `Approve / Pause / Cancel`. Slice 23 adds a
**dashboard runtime override** per task type — see §6.3(a) — that filters
which buttons render at gate-fire time without editing the YAML.

### 6.3 Dashboard runtime overrides + escalation workspace

The dashboard plays two roles for this subsystem: **(a) toggle
control panel** and **(b) escalation workspace** (the canonical place
to view/submit chat answers and mark claude_code work as built).

**(a) Toggle control panel**

New table `dashboard_setting (key TEXT PK, value JSON, updated_at,
updated_by)`. Resolution order: dashboard_setting → YAML default. Slice
23 ships the write side (slice 17 only had read), with optimistic
locking on `updated_at` (§10.7 row 1).

The page lives at SPA route `/escalation-settings` and calls
`/admin/escalation-settings*` on the API. Both routes are gated by the
existing admin auth dependency.

**Canonical key namespace.** Every dashboard-mutable setting lives at a
dot-path under `manual_escalation.*` matching the YAML structure so the
resolver, write API, and YAML parser share one shape:

| Key | Type | YAML default source | UI control |
|---|---|---|---|
| `manual_escalation.enabled` | bool | `manual_escalation.enabled` | Master kill switch |
| `manual_escalation.modes.chat.enabled` | bool | `manual_escalation.modes.chat.enabled` | Per-mode toggle |
| `manual_escalation.modes.claude_code.enabled` | bool | `manual_escalation.modes.claude_code.enabled` | Per-mode toggle |
| `manual_escalation.budget_extension.enabled` | bool | `manual_escalation.budget_extension.enabled` | Allow extensions |
| `manual_escalation.budget_extension.max_daily_extension_usd` | float | `manual_escalation.budget_extension.max_daily_extension_usd` | Max daily extension slider |
| `manual_escalation.task_types.<task_type>.override` | string | n/a (default `auto`) | Per-task-type override grid |

The `dashboard_settings_catalog` module (`src/donna/cost/dashboard_settings_catalog.py`)
is the single source of truth: it defines the catalog, type coercion,
and **legacy aliases**. Two keys (`modes.claude_code.enabled` and
`budget_extension.enabled`) shipped in slice 17/18/21 without the
`manual_escalation.` prefix; the resolver consults the canonical key
first and falls back to the legacy alias so existing rows do not lose
their override on upgrade. New writes always go to the canonical key.

Dashboard surfaces (admin section, gated by existing auth):
- Master kill switch: **Manual escalation**: On / Off
- Per-mode toggles: **Chat**, **Claude Code**
- Budget extension: **Allow extensions**: On / Off
- Slider: **Max daily extension** (capped at `hard_monthly_ceiling_usd
  / days_left_in_month`, computed and enforced server-side; the GET
  response carries the cap so the slider's max matches the PUT
  acceptance window)
- Per-task-type override grid: each task type with a `manual_escalation`
  block shows a row with `Auto / Force-API / Force-Manual / Disabled`.
  - **Auto** — default; offered_modes follow global toggles.
  - **Force-API** — only the `api_extended` button renders; manual
    handoff is hidden for this task type.
  - **Force-Manual** — only the manual handoff button renders;
    `api_extended` is hidden.
  - **Disabled** — neither manual nor API extension; the gate falls
    straight through to Pause / Cancel.

The override is applied AFTER the underlying preconditions (chat
prompt builder wired, claude_code host repo mounted, budget headroom
available), so toggling `force_manual` for a task type that has no
chat or claude_code config does not invent a button.

`hard_monthly_ceiling_usd` is **not** dashboard-mutable — only YAML.
Prevents a compromised dashboard session from authorizing unlimited
spend (§10.7 row 4).

**Audit.** Every successful write also inserts an
`escalation_lifecycle` row in `invocation_log` with
`event='dashboard_setting_changed'` and a payload of `{key, value,
previous_value, had_lock_token}`. `escalation_request_id` is NULL —
these are subsystem-level events, not tied to one row. The slice 19
per-row timeline only surfaces row-scoped events (it filters on
`escalation_request_id`); these subsystem-level rows are picked up by
the existing log viewer at `/admin/logs`.

**API contract.**

- `GET /admin/escalation-settings` — returns the catalog rows (resolved
  value + YAML default + `updated_at` / `updated_by`), the per-task-type
  override grid, and a `constraints` block with the slider cap.
- `PUT /admin/escalation-settings/{key:path}` — body
  `{"value": ..., "expected_updated_at": "<iso8601>"|null}`. Writes the
  value if `expected_updated_at` matches the stored row (or no row
  exists when `null`). Returns 409 with the live state on mismatch,
  422 on type/range violation, 404 on unknown key.
- `PUT /admin/escalation-settings/task-types/{task_type}` — same
  contract for the per-task-type override grid. Rejects task types
  that do not declare a `manual_escalation` block (404).

**(b) Escalation workspace**

New dashboard area at `/escalations` (SPA route — see slice 19 follow-up
for why the path differs from the original `/admin/escalations` wording;
the backend API endpoints remain under `/admin/escalations*`):

- **List view**: all escalation_requests with status filter
  (`open | submitted | validated | failed | cancelled`), sort by
  age, expandable rows showing the summary inline.
- **Detail view** at `/admin/escalations/<correlation_id>`:
  - Full prompt rendered with one-click copy
  - Mode-specific submission UI:
    - `chat`: textarea + Submit button
    - `claude_code`: copy worktree command, copy spec, "Mark as
      built" modal asking for branch name / SHA
  - Status timeline (each `invocation_log` lifecycle row)
  - Validation result panel (post-submission)
  - Re-submit affordance if validation failed (within iteration cap)
- **Auth**: same auth as the rest of admin dashboard. Multi-user-ready
  via `user_id` filter on the list view.

This area is the canonical surface that resolves the prompt-size and
paste-back issues from §5.2/§5.3.

---

## 7. Tool gap protocol

Tools cannot be auto-drafted (security, dependencies, credentials,
image rebuild). When a missing tool is detected, Donna takes one of
two paths based on **blocking severity**.

### Severity enum

| Value | Trigger | Surfacing |
|---|---|---|
| **`high`** | Capability is active *and* about to run (scheduler pre-run, automation creation, runtime dispatch trip-wire) | Real-time Discord ping with `[File request] [Snooze 24h]` to the configured channel (default `agents`) |
| **`speculative`** | Boot-time mismatch on `pending_review` / `on_manual` capability, OR a skill draft (AutoDrafter pre-flight) proposed using a not-yet-existing tool | Filed silently to `tool_request`; surfaces in the morning digest under **Tool Gaps (speculative)** |

### Detection points (slice 22)

| `detection_point` literal | Site | Severity emitted |
|---|---|---|
| `capability_tool_check` | `CapabilityToolRegistryCheck` boot pass; for `pending_review` / `on_manual` capabilities only | speculative |
| `scheduler_pre_run` | `AutomationDispatcher.dispatch()` before launching a skill run; one gap per missing tool, dispatch returns `outcome='blocked_missing_tool'` | high |
| `automation_creation` | `MissingToolError` thrown by `AutomationCreationPath.approve` when a Discord automation flow needs an unregistered tool | high |
| `skill_draft` | `AutoDrafter` pre-flight: every `step.tools` name not in registry | speculative |
| `runtime_dispatch` | `SkillExecutor._run_tool_invocations` defensive trip-wire — catches mid-run dispatch attempts that boot-check + scheduler-pre-run both missed | high |

`CapabilityToolRegistryCheck` keeps its **fail-loud** boot guarantee
for the dangerous subset (capability `status='active'` AND
`trigger_type IN ('on_schedule', 'on_message')`) — those mismatches
still raise `CapabilityToolConfigError`. Speculative rows are
surfaced *before* the raise so they're still in the table when the
operator reboots after fixing the fatal subset.

### Storage

Both paths upsert a `tool_request` row (full schema in §8). The
partial-unique index `ix_tool_request_open_user_tool` on
`(user_id, tool_name) WHERE status='open'` deduplicates re-emission
while a row is open: the repository bumps `priority = max(existing,
new)`, refreshes `rationale` / `severity` (promoting speculative →
high if the new gap is more urgent), updates `last_seen_at`. Once
the row is `completed` / `rejected`, a fresh emission creates a new
row so historical pattern isn't lost.

### Snooze + re-ping

- `[Snooze 24h]` sets `tool_request.snoozed_until = now + 86400s`
  (default; configurable). Snoozed rows are excluded from the
  morning digest until the deadline passes; they remain `open` so
  re-emission still upserts onto the same row.
- The surfacer rate-limits high-severity Discord re-pings via
  `last_pinged_at`; default cooldown is 4h
  (`tool_gap.reping_cooldown_seconds`). Inside the cooldown,
  re-emission updates the row but doesn't post a fresh message.

### Tool builds

The `[File request]` button kicks off a `tool_request_fulfillment`
escalation that reuses the slice-21 `claude_code` protocol via the
gate's new `open_tool_build_escalation()` method. The escalation row
carries `originating_entity_type='tool_request'` and
`originating_entity_id=<tool_request.id>` so
`ManualValidationRouter._validate_tool` can resolve back. Tool
builds use a separate Jinja template
(`prompts/escalation/tool_build.md`) and an extra lint pipeline
(§10.5).

Crucially: the **decision to start a tool build is always the user's**.
A real-time ping is a notification, not an estimate-driven escalation —
there are no `[Approve $X extension]` buttons because no API spend
will fix the gap.

---

## 8. Data model additions

```sql
-- New: tracks every over-budget decision and outcome.
CREATE TABLE escalation_request (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,                  -- slice 17 ships TEXT to match other tables
  correlation_id TEXT UNIQUE NOT NULL,    -- UUIDv7 (uuid6.uuid7), sent on Discord
  task_id TEXT,                           -- nullable: drafting has no task_id
  task_type TEXT NOT NULL,
  estimate_usd REAL NOT NULL,
  daily_remaining_usd REAL NOT NULL,
  offered_modes JSON NOT NULL,            -- ["api_extended","chat","claude_code","pause","cancel"]
  resolution TEXT,                        -- one of offered_modes; NULL while open
  resolved_by TEXT,                       -- 'user' | 'timeout'
  resolved_at TIMESTAMP,
  prompt_path TEXT,                       -- workspace path for claude_code mode
  prompt_body TEXT,                       -- slice 19: full prompt rendered by dashboard
  summary TEXT,                           -- slice 19: short summary used in Discord + list view
  mode TEXT,                              -- slice 19: chosen manual mode (chat|claude_code)
  result TEXT,                            -- slice 19: submission payload JSON (post-submit)
  validation_result JSON,                 -- slice 19: post-validation panel content
  branch_name TEXT,                       -- for claude_code mode
  iteration INTEGER DEFAULT 1,
  status TEXT DEFAULT 'open',             -- open|resolved|submitted|validated|failed|cancelled
  created_at TIMESTAMP NOT NULL,          -- slice 17 addition; needed by retry loop
  submitted_at TIMESTAMP,
  validated_at TIMESTAMP,
  priority INTEGER DEFAULT 2,             -- slice 17 addition; gates SMS tier-2 fan-out
  -- Slice 17 delivery-retry bookkeeping (drift vs original §8 — required
  -- so escalation_delivery_loop can poll deliverable rows separately
  -- from rows already sent).
  delivery_status TEXT,                   -- pending | sent | failed
  delivery_attempts INTEGER DEFAULT 0,
  last_delivery_attempt_at TIMESTAMP,
  parent_escalation_id INTEGER,           -- for re-escalations
  -- Slice 21 additions (migration a1b2c3d4e5f7) — see §5.3.
  human_review BOOLEAN DEFAULT 0,         -- iteration cap reached
  target_paths JSON,                      -- snapshot of rendered scope globs
  originating_entity_type TEXT,           -- e.g. 'skill_candidate_report' or 'skill'
  originating_entity_id TEXT,             -- FK pair pointing at the originating row
  base_sha TEXT,                          -- pinned main SHA at gate-fire time
  merged_at TIMESTAMP,                    -- user-driven Mark as merged tracking
  FOREIGN KEY (parent_escalation_id) REFERENCES escalation_request(id)
);

-- New: per-day spend extensions.
CREATE TABLE daily_budget_extension (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  date DATE NOT NULL,
  amount_usd REAL NOT NULL,
  granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  granted_by TEXT NOT NULL,               -- discord user id
  escalation_request_id INTEGER,
  FOREIGN KEY (escalation_request_id) REFERENCES escalation_request(id)
);

-- New: dashboard-mutable settings.
CREATE TABLE dashboard_setting (
  key TEXT PRIMARY KEY,
  value JSON NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_by TEXT NOT NULL
);

-- §7 — tool gaps. Slice 22 actual layout (migration b2c3d4e5f6a8).
-- Original §8 spec listed only id / user_id / tool_name /
-- proposed_signature / rationale / blocking_capability_id / priority /
-- status / created_at / resolved_at / resolved_branch; the columns
-- below add what surfacing + dedup + snooze need.
CREATE TABLE tool_request (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,                     -- TEXT for parity with escalation_request.user_id
  tool_name TEXT NOT NULL,
  proposed_signature JSON,                   -- loose Python-type-hint shape, see §7
  rationale TEXT,
  blocking_capability_id TEXT,               -- NULL ⇔ severity='speculative' from skill draft
  priority INTEGER NOT NULL DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'open',       -- open|in_progress|completed|rejected
  -- Slice 22 additions ---------------------
  severity TEXT NOT NULL DEFAULT 'speculative',  -- 'high' | 'speculative'
  detection_point TEXT,                      -- capability_tool_check|scheduler_pre_run|automation_creation|skill_draft|runtime_dispatch
  snoozed_until TIMESTAMP,                   -- set by [Snooze 24h]
  first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at   TIMESTAMP,
  resolved_branch TEXT,
  escalation_request_id INTEGER REFERENCES escalation_request(id),  -- set by [File request]
  last_pinged_at TIMESTAMP                   -- rate-limits Discord re-pings on dedup hits
);
-- Dedup: only one open row per (user, tool). Resolved/rejected rows
-- allow new emissions so historical pattern isn't lost.
CREATE UNIQUE INDEX ix_tool_request_open_user_tool
  ON tool_request(user_id, tool_name) WHERE status = 'open';
CREATE INDEX ix_tool_request_status_severity ON tool_request(status, severity);
CREATE INDEX ix_tool_request_blocking_capability ON tool_request(blocking_capability_id);

-- Existing invocation_log gains:
ALTER TABLE invocation_log ADD COLUMN escalation_request_id INTEGER
  REFERENCES escalation_request(id);
```

Migrations: one Alembic revision **per slice**, not per table. Slice 17
ships all three new tables (`escalation_request`,
`daily_budget_extension`, `dashboard_setting`) and the `invocation_log`
ALTER in a single revision (`c7d8e9f0a1b2`). Splitting an FK target
across revisions adds no value when the slice is already an atomic
shipping unit. No manual ALTERs.

Slice 19 backfills the dashboard-required columns
(`prompt_body`, `summary`, `mode`, `result`, `validation_result`) in a
follow-up revision (`d8e9f0a1b2c3_escalation_workspace_columns.py`)
because they were named inline in §5.2 / §5.3 but missed by §8's
original `CREATE TABLE` listing.

---

## 9. Configuration files added/modified

| File | Change |
|---|---|
| `config/manual_escalation.yaml` | **New.** Bootstrap defaults (§6.1). |
| `config/task_types.yaml` | **Modify.** Add `manual_escalation` block per task type (§6.2). |
| `config/dashboard.yaml` | **Modify.** Add `escalation_card` section listing the runtime keys this dashboard exposes. |
| `prompts/escalation/skill_draft.md` | **New.** Jinja template for `claude_code` mode skill builds. |
| `prompts/escalation/tool_build.md` | **New.** Jinja template for tool builds (extra security clauses). |
| `prompts/escalation/chat_question.md` | **New.** Jinja for `chat` mode (slice 20). |
| `prompts/escalation/summarize.md` | **New.** Local-Ollama summarizer prompt invoked from `ChatPromptBuilder` (slice 20). |
| `schemas/escalation_submission.json` | **New.** For `/donna submit` endpoint payload. |
| `schemas/escalation_summary_output.json` | **New.** Strict shape for the local-Ollama summarizer output (slice 20). Validated in `ChatPromptBuilder._generate_summary`; malformed responses fall through to the deterministic fallback per §10.2 row 3. |

---

## 10. Failure modes & mitigations

Organized by failure category.

### 10.1 Discord channel failures

| Failure | Mitigation |
|---|---|
| Discord API down / network partition during escalation | Escalation request created in DB regardless. Cron retries delivery every 60s for up to `escalation_timeout_minutes`. If still undelivered: SMS tier-2 fallback (existing `slice_07_sms_escalation.md` tiers) for priority ≥ 3. |
| User on mobile, didn't see ping | `escalation_timeout_minutes` default 60. Below daily-pause-threshold tasks survive timeout into `paused` state and re-offer next morning. |
| Stale button click (escalation already resolved) | Buttons carry the `correlation_id`. Click handler checks `status='open'` before mutating; otherwise replies ephemerally "already resolved". |
| Replay attack (old message clicked) | Same correlation_id check + buttons disabled (component re-render) on resolution. |
| Wrong-account approval (someone else with Discord token) | Discord interaction `user.id` must match the configured `OWNER_DISCORD_ID` (single user) or be in the `admin_discord_ids` list (multi-user later). Reject + log otherwise. |

### 10.2 Prompt delivery failures

| Failure | Mitigation |
|---|---|
| Rendered prompt > Discord 2000 char limit | Non-issue by design: full prompt lives in `escalation_request.prompt_body` and on disk. Discord only carries the summary (≤1500 chars) plus optional MD attachment. |
| Discord attachment upload fails (rate limit / network) | Attachment is best-effort. Notification still posts with the summary + dashboard link. Log `attachment_upload_failed` for observability. |
| Summarizer (local Ollama) is down | Fall back to a deterministic templated summary: "{task_type} request — estimate ${estimate}. Click for full prompt." Never blocks the escalation. |
| Dashboard down when user clicks notification link | MD attachment in Discord acts as backup read-only view. User can also `cat ${DONNA_WORKSPACE_PATH}/escalations/<id>.md` from the host. Submission still requires dashboard or `/donna submit`. |
| User on mobile with no MD reader | MD renders fine in the Discord client itself when displayed inline. For the attachment case, Discord's mobile app previews .md as text. |

### 10.3 Manual-handoff submission failures

| Failure | Mitigation |
|---|---|
| User submits empty / malformed answer in chat mode | Both the dashboard endpoint and the `/donna_submit` slash command run through `donna.cost.escalation_submit_service.apply_submission`, which enforces the JSON schema (``answer.minLength=50``). The slash command also enforces an upper bound (``prompt_delivery.slash_command_max_chars``, default 3000) and replies "use dashboard for long answers" when exceeded. Discord button click without text reply prompts "paste your answer first". |
| User submits but never builds the branch in claude_code mode | Poller checks `branch_exists(branch_name)` against the read-only host-repo mount on every tick. If absent: posts a one-shot "branch not found, did you push?" feedback and leaves the row in `submitted` so a later push triggers re-ingestion (no iteration burn). The slice 21 implementation runs this check immediately on each poller tick rather than after a 5-min delay — branch resolution is cheap (`git rev-parse`) and the user benefits from the fastest possible feedback. Pushed and local-only branches are equivalent through the read-only mount, so a separate `/donna submit-local` command was not needed; the dashboard's "Mark as built" + the `/donna_submit_built` slash command both cover the case. |

| User pushes a branch with wrong files (touched files outside spec scope) | Diff-validator rejects with specific list of out-of-scope files. User can edit and resubmit; iteration count increments. |
| User force-pushes branch between submission and validation | Resolution is locked to the SHA at submission time. New SHA = new submission required. |
| Branch contains uncommitted/staged changes mixed with the work | Diff is computed against `base..tip`, ignoring working tree. User's local mess is irrelevant. |

### 10.4 Validation failures (post-submission)

| Failure | Mitigation |
|---|---|
| Skill from manual handoff fails fixture validation | Failures posted to Discord; same correlation thread. User iterates in worktree, resubmits. Iteration cap `manual_iteration_limit` (3). |
| At iteration cap, still failing | Auto-cancel the escalation: status becomes `cancelled` and `human_review = 1` is set on the same `escalation_request` row (slice 21 decision §15 — reuses the row, no separate queue table). The poller writes an `iteration_limit_reached` audit event and posts a Discord notice with the dashboard link. Dashboard list view filters / banners on `human_review = 1`. No infinite loop. |
| Tool build missing mock entry | Pre-validation lint (§10.5). User cannot submit a tool without its mock — diff-validator rejects. |
| Tool build passes validation but breaks an existing skill in shadow | Standard regression handling: skill enters `flagged_for_review`, escalation marked `validated_with_warnings`, dashboard shows banner. |

### 10.5 Tool-build-specific failures (extends §10.4)

Slice 22 implements every row below as a discrete check in
`src/donna/cost/tool_lint/`. `ManualValidationRouter._validate_tool`
runs the full pipeline against the diff scope before promoting the
`tool_request` row to `completed`. Lint failures keep the row in
`open` so the user can iterate in the same worktree (slice 21
iteration cap then governs).

| Failure | Mitigation |
|---|---|
| Tool needs new dependency, image not rebuilt | `tool_lint/metadata.py` — module-level `requires_rebuild: bool` is required. `True` is accepted but emits a `requires_rebuild_warning` (surfaced on the dashboard panel). Slice 24 ships the hourly Discord nag in `donna.cost.requires_rebuild_nag.RequiresRebuildNagger`: an orchestrator-tick scanner that diffs `tool_request WHERE status='completed' AND resolved_at < now-grace` against `ToolRegistry.list_tool_names()`, posts a "tool built but unrebuilt" reminder for any unregistered tool, respects a per-row cooldown via `tool_request.last_pinged_at`, and stops once the rebuild lands. |
| Tool hardcodes a credential value | `tool_lint/secrets.py` — curated regex list (`sk-ant-…`, `sk-…`, `xoxb-…`, `xapp-…`, `ghp_…`, `AKIA…`, PEM private-key headers, Google `AIza…`) plus a vault-key naming heuristic that flags `*_API_KEY = "…"` assignments unless the value goes through `vault.read(…)` / `os.environ` / `getenv`. The opt-in `detect-secrets` shim runs only when `tool_gap.lint.detect_secrets_enabled = true` AND the package is importable. |
| Tool calls Anthropic API directly (bypassing gateway) | `tool_lint/anthropic_import.py` — AST walk; `import anthropic` / `from anthropic[…] import …` outside `src/donna/llm/` is a hard fail. |
| Tool not added to any agent allowlist | `tool_lint/allowlist.py` — diff must include at least one of `config/agents.yaml`, `config/skills.yaml`, `config/task_types.yaml` AND the modified file's text must contain the `tool_name` near a `tools:` / `tools_json` key. OR the tool source declares module-level `unallowlisted = True` (intentional defined-but-unusable). |
| Tool does I/O at import time (would break ValidationExecutor) | Two layers. (a) `tool_lint/import_io.py` — AST walk over the **module body** only; rejects top-level Calls whose target chain starts with `open`, `requests`, `urllib`, `aiohttp`, `httpx`, `socket`, `subprocess`, `os.system`, `pathlib.Path(...).read_text/write_text`, etc. Descends into `If`/`Try`/`With` at module scope. (b) `tool_lint/inert_test.py` — branch must include `tests/skills/tools/test_<tool_name>.py` containing a Call to `is_inert_at_import('donna.skills.tools.<tool_name>')` (see `donna.skills.tool_test_kit`). |
| Tool unbounded latency | `tool_lint/metadata.py` — module-level `default_timeout_seconds: int > 0` is required. The dispatcher's enforcement is out of scope for slice 22; the lint guarantees the metadata exists for slice 24's enforcement step. Default rendered into the spec template is `5`. |
| Tool module fails to import after lint passes | `tool_lint/import_smoke.py` — subprocess `python -c "import donna.skills.tools.<tool_name>"` against the host-repo worktree at the branch tip. Catches `ImportError` for missing optional deps that AST inspection can't see. 10s timeout. |

### 10.6 Budget-extension-specific failures

| Failure | Mitigation |
|---|---|
| User approves extension; then estimate was wrong; actual cost overshoots | API call's `complete()` enforces a hard token limit derived from `extension_amount × token_rate`. Truncated output triggers a re-estimate + re-escalation rather than silent overspend. |
| Multiple extensions in one day stack to absurd amounts | `max_daily_extension_usd` enforced at gate fire-time: `api_extended` is omitted from `offered_modes` when remaining headroom < estimate, so the button never renders. |
| Approver clicks but interaction fails (Discord 5xx) | Idempotency: granting an extension is keyed on `(escalation_request_id, granted_by)`. Retry-safe. |
| Extension granted, task never runs (orchestrator crash) | On orchestrator boot, scan extensions whose `escalation_request.resolution='api_extended'` row has no non-`escalation_lifecycle` `invocation_log` entry — i.e. the extension was granted but the actual API call never landed an audit row. Implemented by :meth:`donna.cost.budget_extension.BudgetExtensionRepository.find_stale_grants` (SQLite `LEFT JOIN invocation_log ... WHERE il.id IS NULL`). Stale grants are voided (`extension_voided` audit event); resume is deferred (see `docs/superpowers/specs/followups.md#S18`). Voided extensions are never charged. |
| Hard monthly ceiling reached | `api_extended` is omitted from `offered_modes` so no extension button renders, and the Discord summary reads "Monthly cap. Pause / Cancel only." in place of the usual "Choose: …" line. Implemented in :meth:`donna.cost.escalation_gate.EscalationGate.extension_filter_reason` (returns `"over_ceiling"`) and rendered by ``donna.cli_wiring._build_escalation_message_body``. |

### 10.7 Routing & toggle failures

| Failure | Mitigation |
|---|---|
| Dashboard toggle race (two browser tabs flip same setting) | `dashboard_setting` writes use `updated_at` optimistic lock via :meth:`EscalationRepository.set_dashboard_setting_with_lock` (slice 23). The PUT body carries `expected_updated_at` from the GET response; a mismatch returns 409 with `{current_value, current_updated_at, current_updated_by}` so the client can surface the live state. The transaction is wrapped in `BEGIN IMMEDIATE` so two parallel writers see consistent ordering. |
| Config reload during in-flight escalation | Resolution semantics: an open escalation uses the offered_modes snapshotted in its row, NOT live config. Disabling claude_code mid-flight does not retroactively cancel an open claude_code escalation. |
| Task type has `manual_escalation: claude_code` but no reference_module configured | Validation at config load via :func:`donna.config.validate_manual_escalation_config` (slice 23): any task type declaring claude_code mode MUST have non-empty ``target_paths`` AND ``reference_module``. Raises :class:`ManualEscalationConfigError` on boot listing every offender at once. Wired into `cli_wiring.build_startup_context` and the API's lifespan startup. |
| Dashboard authentication compromised | `hard_monthly_ceiling_usd` is YAML-only (§6.3). Worst case attacker can run today's daily extension cap — bounded blast. |

### 10.8 Privacy / data exposure failures

| Failure | Mitigation |
|---|---|
| Task contains email body / calendar invite / vault data, posted raw to Discord | Documented as accepted risk per user direction ("send raw"). Mitigations layered: (a) Discord channel is private DM to owner; (b) `OWNER_DISCORD_ID` check prevents leaks via wrong-channel post; (c) prompts that reference vault entries pass *names* not *values*. |
| Workspace mount accessible from unauthorized host process | Workspace is on host filesystem with standard POSIX perms; user owns it. Documented in SETUP.md. |
| Branches contain sensitive data merged into git history | Pre-commit secret scanner (§10.5) runs on every submission. Reject + require force-fix. |

### 10.9 Multi-user readiness

| Failure | Mitigation |
|---|---|
| Phase 2 multi-user enabled, escalations from one user trigger another's Discord | Every table has `user_id`; Discord routing uses each user's configured channel. Slice 24 added :func:`EscalationRepository.find_open_for_originating_entity` `user_id` argument (was previously cross-tenant) and pinned the contract via the parametrised `tests/integration/test_multi_user_isolation.py` fixture so two users running the same skill_draft never collide on dedup. |
| Cross-user budget mixing | Budget is per-user from day one; no global pool. Existing `donna_models.yaml` budget keys would need to move into a per-user config table — call out as a follow-up but enforce schema today. Slice 24 pinned :meth:`BudgetExtensionRepository.get_daily_total` per-user isolation in `tests/integration/test_multi_user_isolation.py::TestBudgetExtensionIsolation`. |

### 10.10 Audit & observability

Every state transition writes to `invocation_log` with `task_type='escalation_lifecycle'` and a small JSON payload. Specifically log:
- `escalation_offered` — modes shown, estimate, remaining
- `escalation_resolved` — chosen mode, resolved_by
- `escalation_submitted` — branch, iteration
- `escalation_validated` / `escalation_failed`
- `escalation_branch_not_found` (slice 21) — branch missing on poller tick
- `extension_granted` / `extension_voided`
- `iteration_limit_reached` (slice 21) — manual handoff reached
  `manual_iteration_limit` resubmits without passing; row auto-cancels
  with `human_review=1`
- `tool_gap_detected` (slice 22) — written every time
  `ToolGapSurfacer.surface(gap)` upserts a `tool_request` row,
  regardless of severity. Payload includes `tool_name`, `severity`,
  `blocking_capability_id`, `detection_point`, `rationale`, `is_new`.
- `tool_request_filed` (slice 22) — written when a high-severity ping
  is successfully posted to Discord (or re-posted after the cooldown).
  Distinguishes a fresh ping from a noop.
- `tool_request_filled` (slice 22) — written by
  `ManualValidationRouter._validate_tool` after lint + import-smoke
  pass and the row is marked `completed`.
- `tool_gap_snoozed` / `tool_gap_owner_mismatch` (slice 22) — view
  button hooks; mirror the `escalation_owner_mismatch` precedent.

Slice 22 audit rows use `task_type='tool_gap_lifecycle'` (separate
from `escalation_lifecycle` so cost aggregations and per-subsystem
queries stay clean) and store `input_hash='toolreq:<id>'` so the row
is queryable by tool_request id without a JSON scan.

Dashboard renders these as a timeline per escalation_request_id.

Slice 24 ships the dedicated :http:get:`/admin/escalations/{correlation_id}/timeline`
endpoint (separate from the slice-19 detail-blob timeline) so the
detail page can poll for newly-landed audit rows on its 30-second
tick without re-fetching the entire detail payload. The endpoint
joins `escalation_lifecycle` and `tool_gap_lifecycle` rows for the
same `escalation_request_id` so a `tool_request_fulfillment`
escalation surfaces both its lifecycle envelope (offered, resolved,
submitted) and the slice-22 tool-gap audit chain
(`tool_request_filled`, `tool_request_filed`, etc.) on one
timeline. The response carries `next_after_id` for cursor-style
append-only polling.

---

## 11. Verification / acceptance criteria

### Functional
- [x] Cost router emits the four-button Discord message when estimate > min(daily_remaining, threshold) AND any escalation mode enabled. *Slice 17 — `test_escalation_gate.py`.*
- [x] Each of `api_extended`, `chat`, `claude_code`, `pause`, `cancel` reaches the documented terminal state. *Slices 17/18/20/21 unit tests cover each path; slice 24 ties chat + api_extended + tool_gap together end-to-end.*
- [x] Per-task-type `manual_escalation` config gates which buttons render. *Slice 20 — `test_escalation_gate_chat_mode.py`; slice 23 — `test_escalation_gate_overrides.py`.*
- [x] Dashboard toggles override YAML and take effect on next escalation (no restart). *Slice 23 — `test_admin_escalation_settings.py` and `test_dashboard_setting_resolver.py`.*
- [x] Master kill switch removes Manual handoff button entirely. *Slice 17 — `TestShouldFire::test_does_not_fire_when_kill_switch_off`.*
- [x] Discord notification carries summary + dashboard link + optional MD attachment; full prompt body always lives in `escalation_request.prompt_body` and is rendered by the dashboard. *Slice 20 — `test_escalation_chat_prompt.py` + `test_escalation_delivery_callback.py`.*
- [x] Dashboard `/admin/escalations/<id>` page renders prompt, accepts answers (chat) or branch confirmations (claude_code), and writes back to the same row. *Slice 19 — `test_admin_escalations.py`; slice 24 added the merged-task-type timeline + the dedicated `/timeline` endpoint.*
- [x] Tool gaps file `tool_request` rows; high-blocking gaps ping in real time. *Slice 22 — `test_tool_gap_surfacer.py`; slice 24 wired the §10.5 row 1 hourly nag for unrebuilt tools.*
- [x] All escalation outcomes logged to `invocation_log` with the correlation_id. *Slices 17/22 escalation_lifecycle + tool_gap_lifecycle audits; slice 24 multi-user isolation test pins ``user_id`` on every row.*
- [x] Iteration cap hits force-cancel and write a `human_review` flag. *Slice 21 — `test_claude_code_poller.py::test_iteration_cap_promotes_to_human_review`.*

### Failure-mode regression tests (one per row in §10)
- [x] Each mitigation has a corresponding fixture / unit test. *Slice 24 closed the audit-flagged gaps (§10.1 row 5, §10.5 row 1 nag, §10.6 row 4 + 5, §10.8 row 1, §10.9 rows 1–2, §10.10 row 6) in `tests/integration/test_section_10_residual_gaps.py`, `tests/integration/test_multi_user_isolation.py`, and `tests/cost/test_requires_rebuild_nag.py`. The §10.4 row 4 dependent-skill regression remains explicitly deferred per `docs/superpowers/specs/followups.md#S24`; §10.6 row 1 re-estimate-on-overspend is also deferred there.*
- [ ] Discord 5xx retry is integration-tested with mocked Twilio + Discord. *Tier-2 SMS fallback wiring lives in `tests/unit/test_escalation_tiers.py` (slice 7 plumbing); the Discord-5xx → escalation-row resilience is covered by the slice-17 `test_escalation_delivery_loop.py` retry suite. Twilio-mock-end-to-end remains an integration harness gap.*
- [x] Stale button click test asserts no double-resolution. *Slice 17 — `test_escalation_delivery_callback.py`; slice 19 — `test_admin_escalations.py::TestSubmit` 409 paths.*
- [ ] Dashboard down → Discord MD attachment fallback exercised by a fixture. *Discord attachment is best-effort in the slice-17 delivery callback; a dedicated fixture for the dashboard-down branch is open.*

### End-to-end
- [x] **chat mode E2E:** trigger an over-budget chat_escalation; receive Discord prompt; submit answer via dashboard; task completes with answer as result. *(Slice 20 added `tests/integration/test_chat_mode_e2e.py`; slice 24 restored it after the ORM/Alembic drift broke it on main.)*
- [ ] **claude_code mode E2E:** trigger over-budget skill_draft; receive Discord ping; build branch in worktree; mark as built via dashboard; skill enters sandbox state. *Deferred — needs a real-disk worktree harness; the slice-21 `test_claude_code_poller.py` battery covers every transition individually. Logged as a follow-up so the next slice that introduces an integration harness picks it up.*
- [x] **api_extended E2E:** approve extension; task runs; daily_remaining reflects extension; invocation_log carries escalation_request_id. *(Slice 24 — `tests/integration/test_api_extended_e2e.py`.)*
- [x] **tool gap E2E:** add a capability that requires a missing tool; capability_tool_check fires; ping arrives in real time; user files request. *(Slice 24 — `tests/integration/test_tool_gap_e2e.py`.)*

---

## 12. Open questions (for follow-up before slice work begins)

1. **Default `OWNER_DISCORD_ID` source** — env var, vault entry, or `auth.yaml`? *Resolved (slice 17, §15): env var `DONNA_OWNER_DISCORD_ID`; fail-soft when unset.*
2. **Local-only branches** — does the orchestrator have read access to the host repo's `.git` directory? *Resolved (slice 21, §15): env var `DONNA_HOST_REPO_PATH` points at a read-only mount; pushed and local branches are equivalent through it; fail-soft when unset.*
3. **`human_review_request` table vs reusing `tool_request`** — do we want one queue for all manual interventions, or separate queues per kind? *Resolved (slice 21, §15): reuse `escalation_request` with a `human_review` BOOLEAN column; revisit when Phase 2 surfaces non-skill / non-tool human-review cases.*
4. **Tier 2 SMS escalation on Discord delivery failure** — confirm we want this; SMS rate limits in slice 7 are tight (10/day). *Resolved (slice 17, audited slice 24). The slice-17 timeout sweep already calls `EscalationDeliveryLoop._maybe_fan_out_sms` for any timed-out row whose `priority >= sms_priority_threshold`, hitting `start_at_tier=2` so the slice-7 SMS tiers carry it. Slice 24 confirmed the wiring; the only open thread is a Twilio-mock integration test (tracked under §11 "Discord 5xx retry").*
5. **Re-escalation parent chains** — current spec stores `parent_escalation_id`. Do we need a depth limit beyond `manual_iteration_limit`? *Open. Slice 24 audited the path: `manual_iteration_limit` (default 3) bounds the inner loop, and no real cross-row chains have been observed in slice 17–23 deployments. Adding `max_re_escalation_depth` is a new behaviour and explicitly out of slice-24 scope per the brief's "Not in Scope" section. Logged in `followups.md#S24` for the next behavioural slice.*

---

## 13. Migration / build sequence

Best built as a sequence of slices. None should be merged half-finished
(per `CLAUDE.md` "no half-finished implementations"). Each slice has a
matching brief in `slices/`.

| Slice | File | Scope |
|---|---|---|
| 17 | `slice_17_escalation_core.md` | `escalation_request`, `daily_budget_extension`, `dashboard_setting` tables. Cost router emits `escalation_offered`. Discord four-button view. `pause` and `cancel` paths only. Master kill switch in YAML (dashboard wiring deferred). |
| 18 | `slice_18_budget_extension.md` | `api_extended` mode end-to-end. Hard ceilings. Idempotent grant. Crash-recovery scan. |
| 19 | `slice_19_dashboard_escalation_workspace.md` | `/admin/escalations` list + detail views. Full-prompt rendering. Submit endpoint. Built BEFORE the modes that depend on it. |
| 20 | `slice_20_chat_mode.md` | `chat` mode wired to dashboard submit. Summary generator (Ollama). Discord attachment of full prompt. `/donna submit` slash command as fallback. |
| 21 | `slice_21_claude_code_mode.md` | `claude_code` mode. Worktree spec template. Diff validator. Iteration limit. "Mark as built" dashboard modal. Reuse existing AutoDrafter validation pipeline. |
| 22 | `slice_22_tool_gap_surfacing.md` | `tool_request` table. `capability_tool_check` integration with real-time vs digest routing. |
| 23 | `slice_23_dashboard_runtime_overrides.md` | UI cards for all toggles. `dashboard_setting` resolution. Optimistic locking. |
| 24 | `slice_24_escalation_hardening.md` | Each row of §10 with a regression test. Audit timeline view. Multi-user scoping audit. |

---

## 14. Reference files (existing code to integrate with)

| File | Why it matters |
|---|---|
| `src/donna/skills/manual_draft_poller.py` | Polling pattern to mirror for escalation submission ingestion. |
| `src/donna/skills/lifecycle.py` | `human_approval` transition exists; manual handoffs use it. |
| `src/donna/cost/budget.py`, `tracker.py` | Budget envelope; add extension awareness. |
| `src/donna/integrations/discord_views.py` | `AgentActionView` is the pattern for the four-button view. |
| `src/donna/api/routes/admin_dashboard.py` | Where escalation card / settings UI plugs in. |
| `src/donna/capabilities/capability_tool_check.py`, `tool_requirements.py` | Tool-gap detection; emits to `tool_request`. |
| `src/donna/skills/validation_executor.py` + `mock_tool_registry.py` | Validation pipeline manual handoffs route through unchanged. |
| `config/task_types.yaml`, `config/skills.yaml`, `config/llm_gateway.yaml` | Existing config patterns. New `manual_escalation.yaml` follows same shape. |
| `slices/slice_07_sms_escalation.md` | Format reference for the slice briefs. Tier-2 fallback also lives in slice 7. |

---

## 15. Key decisions

- ToS: **Manual user-driven Claude Code is fine.** Programmatic injection (tmux, expect, OAuth-token reuse) is not. Spec keeps the human in the loop always.
- Skills vs tools: **Skills can be auto-drafted; tools cannot.** Tool gaps are surfaced, never escalated for autonomous build.
- Browser tool: **Out of scope for this spec.** Will be its own slice.
- Privacy / delivery: **Dashboard is canonical for full prompts and answer submission.** Discord delivers a summary + optional MD attachment; full prompt always exists in `escalation_request.prompt_body`. Owner-DM channel is the trust boundary for the Discord side; dashboard auth is the trust boundary for the canonical side.
- Toggles: **Dashboard is canonical at runtime; YAML is bootstrap default.**
- Budget extension and manual handoff: **Unified four-button decision tree, not two separate flows.**
- **(2026-05-05, slice 17)** Correlation IDs are **UUIDv7** via the
  existing `uuid6` dependency, not ULID. Same sortability properties,
  no new dependency.
- **(2026-05-05, slice 17)** Discord retry uses a **single polling
  coroutine** (`escalation_delivery_loop`) modelled on
  `EscalationManager.check_and_advance`, not per-request
  `asyncio.create_task`. Survives bot restarts because state lives in
  the row, not in a coroutine.
- **(2026-05-05, slice 17)** SMS tier-2 fan-out on timeout is gated on
  `priority >= 4` per §4. The §10.1 row-1 ≥ 3 threshold (Discord
  delivery failure) is a separate trigger and not yet wired; the
  delivery loop instead retries within the timeout window and lets the
  ≥ 4 timeout rule cover sustained delivery failures. To be reconciled
  in slice 24.
- **(2026-05-05, slice 17)** `OWNER_DISCORD_ID` source: env var
  `DONNA_OWNER_DISCORD_ID`. When unset *and* a Discord bot is wired
  *and* `manual_escalation.enabled=true`, boot logs
  `escalation_gate_disabled_no_owner` and continues with the gate
  inactive (rather than crashing the entire orchestrator) — over-budget
  paths fall back to `BudgetGuard.check_pre_call`.
- **(2026-05-06, slice 21)** Host repo access: env var
  `DONNA_HOST_REPO_PATH` points at a read-only mount. Donna never
  writes to the host repo. If unset / not a git repo, the
  `claude_code` button is not offered (logged once at boot,
  fail-soft). Resolves §12 Q2.
- **(2026-05-06, slice 21)** Iteration-cap routing: reuse
  `escalation_request` with a new `human_review` BOOLEAN column rather
  than introducing a separate `human_review_request` table. Resolves
  §12 Q3.
- **(2026-05-06, slice 21)** Originating-entity tracking: added
  explicit `originating_entity_type` + `originating_entity_id` columns
  on `escalation_request` and a parallel kwarg on `router.complete` /
  `gate.fire_and_wait`. Required because `task_id` is NULL for every
  claude_code path (auto_drafter / evolution call sites both pass
  `task_id=None`). Diff-validator reads from these to render
  `{name}`-substituted target_paths globs.
- **(2026-05-06, slice 21)** Manual `claude_code` lifecycle landing
  state: `sandbox` (not `draft`). The user's "Mark as built" + green
  fixtures *is* the human approval; manual mode lands one hop deeper
  than AutoDrafter for that reason. Existing automatic promotion gates
  take over from `sandbox`.
- **(2026-05-06, slice 21)** Donna does **not** auto-merge validated
  branches. The user runs `git merge` manually; the dashboard
  "Mark as merged" button is a tracking-only write that flips
  `merged_at`. Preserves the §15 ToS principle ("human is always the
  operator") for code-writing actions.
- **(2026-05-06, slice 21)** Submit-flow extracted to
  `donna.cost.escalation_submit.submit_escalation_core` — both the
  HTTP route and the `/donna submit` slash command delegate to it,
  so schema validation, mode mismatch, iteration cap, and concurrent-
  submission guards are identical across surfaces.
- **(2026-05-06, slice 22)** Tool-gap dedup key is
  `(user_id, tool_name)` enforced via partial-unique index on
  `status='open'`. Re-emission while open bumps `priority`, refreshes
  `rationale`, can promote `severity` speculative→high; once
  resolved/rejected, fresh emissions create new rows so historical
  pattern isn't lost.
- **(2026-05-06, slice 22)** Snooze is a column on `tool_request`
  (`snoozed_until`), not a separate table. The repository's
  `snooze()` is idempotent against terminal rows (returns False).
- **(2026-05-06, slice 22)** **No tool lifecycle table.** Tools live
  in source code; the deployment cycle (manual merge + orchestrator
  restart, plus rebuild when `requires_rebuild=True`) is the
  activation path. `_validate_tool`'s success only marks the
  `tool_request` row `completed` and fires the `tool_request_filled`
  audit; the user runs `git merge` + restart manually. This preserves
  the §15 ToS principle ("human is always the operator").
- **(2026-05-06, slice 22)** Tool validation in slice 22 = **lint +
  import-smoke**. Dependent-skill regression (re-running fixtures of
  every skill that references the new tool) is deferred to slice 24
  (escalation hardening) per spec §10.4 row 4.
- **(2026-05-06, slice 22)** Secret scanner is a curated regex list
  by default; the `detect-secrets` shim is opt-in via
  `tool_gap.lint.detect_secrets_enabled`. Slice 24 may flip the flag
  without code changes.
- **(2026-05-06, slice 22)** `EscalationGate.open_tool_build_escalation`
  bypasses `fire_and_wait` because tool builds have no API spend to
  gate — the user already chose to fulfill the request by clicking
  `[File request]`. The gate creates the `escalation_request` row
  with `offered_modes=['claude_code']` and immediately calls
  `record_manual_handoff` to render the spec from `tool_build.md`.
- **(2026-05-06, slice 22)** Tool-build prompt selection is driven by
  `task_type` via `_TASK_TYPE_TO_TEMPLATE` in
  `donna.cost.claude_code_spec`; `record_manual_handoff` accepts an
  `extra_context` kwarg so the gate can plumb tool-specific Jinja
  variables (`proposed_signature`, `requires_rebuild_default`,
  `default_timeout_seconds`) without forking the spec builder.
- **(2026-05-06, slice 22)** `CapabilityToolRegistryCheck` keeps
  fail-loud on the dangerous subset (active + scheduled/messaged
  capabilities) but surfaces speculative gaps for `pending_review` /
  `on_manual` capabilities **before** any raise so the rows exist
  on the next reboot. New `surfacer` and `boot_owner_user_id`
  constructor kwargs default to `None` / `"boot"` for backward
  compatibility.
- **(2026-05-06, slice 23)** Canonical dashboard_setting key namespace.
  Every dashboard-mutable key follows the dot-path of the YAML
  structure under ``manual_escalation.*``. Slice 17/18/21 had drifted
  to two short names (``modes.claude_code.enabled``,
  ``budget_extension.enabled``); the resolver now consults the
  canonical key first and falls back to legacy aliases registered in
  ``donna.cost.dashboard_settings_catalog`` so existing rows do not
  silently lose their override on upgrade. New writes always go to
  the canonical key.
- **(2026-05-06, slice 23)** Optimistic-lock UI behaviour on 409.
  Brainstorm gap (silent retry vs visible toast): we surface a
  `Setting changed in another tab. Showing latest.` toast and replace
  the stale value with the live state from the conflict response,
  rather than silently retrying. Silent retry would race with another
  intentional change and confuse the operator about which value is
  authoritative.
- **(2026-05-06, slice 23)** Per-task-type override grid lives on a
  dedicated page (`/escalation-settings`) rather than as a section on
  the existing `/escalations` workspace. The escalation workspace is
  per-row; the settings page is per-subsystem. Mixing the two would
  bloat the row detail view that slice 19 already designed for the
  escalation lifecycle.
- **(2026-05-06, slice 23)** Audit log target. Toggle changes write
  an `escalation_lifecycle` row to `invocation_log` with
  `event='dashboard_setting_changed'`. Brainstorm gap weighed adding a
  dedicated audit table; reusing `invocation_log` lets the slice 19
  timeline view surface toggle changes alongside actual escalations
  without bolting on a second source. `escalation_request_id` stays
  NULL because these are subsystem-level events, not tied to one
  escalation row.
- **(2026-05-06, slice 24)** Per-row timeline merges
  ``tool_gap_lifecycle`` events. Slice 22's tool-build audit rows
  share an ``escalation_request_id`` when a tool gap drives a
  ``tool_request_fulfillment`` escalation; slice 24's
  :http:get:`/admin/escalations/{correlation_id}/timeline`
  surfaces both task_types under one chronological feed so the
  detail UI no longer hides the lint outcome. Cursor-style
  ``next_after_id`` enables append-only polling on the existing 30 s
  dashboard refresh cadence.
- **(2026-05-06, slice 24)** ``find_open_for_originating_entity``
  now requires ``user_id``. Multi-user §10.9 row 1 bug: the slice-21
  helper was cross-tenant, so user B's open tool_request would
  shadow user A's dedup lookup once Phase 2 lands. Both call sites
  in :class:`EscalationGate` already had the owner in scope; the
  signature change is enforced by ``tests/integration/test_multi_user_isolation.py``
  parametrised over two distinct user_ids.
- **(2026-05-06, slice 24)** Schema-source of truth pinned by
  ``tests/unit/test_orm_alembic_consistency.py``. Slice 21's
  Alembic migration added six columns to ``escalation_request``
  (``human_review``, ``target_paths``, ``originating_entity_*``,
  ``base_sha``, ``merged_at``) without updating the SQLAlchemy
  ORM, so any test fixture that built schema from
  ``Base.metadata.create_all`` raised ``OperationalError`` on the
  first write. Slice 24 added the columns + a regression test that
  diffs ORM column-set vs. ``alembic upgrade head`` for every
  manually-managed table. Future ORM/Alembic drift fails the test
  suite within seconds.
- **(2026-05-06, slice 24)** ``requires_rebuild=True`` Discord nag
  (§10.5 row 1) shipped as
  :class:`donna.cost.requires_rebuild_nag.RequiresRebuildNagger` —
  hourly tick scans completed tool_requests, diffs against
  ``ToolRegistry.list_tool_names()``, posts a reminder per
  unregistered tool with a per-row cooldown via
  ``tool_request.last_pinged_at``. Closes the deferral S22 logged.

---

## Document History

- **2026-05-05** — Initial canonical spec authored. Decisions captured
  from the ToS / Claude Code / over-budget design conversation.
  Source plan file: `~/.claude/plans/would-it-be-against-playful-pelican.md`
  (kept until cross-doc edits per §3 are merged, then deleted).
