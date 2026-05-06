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

Used for `task_types` whose output is code or files: `skill_draft`,
`skill_evolution`, `tool_request_fulfillment`.

**Surface split:** same as §5.2 — dashboard is canonical workspace,
Discord is alert. Spec file is also written to disk because the user
needs filesystem access to do the work anyway.

**Donna → user:**
1. Write spec file to
   `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md`. Contents:
   - Task summary
   - Acceptance criteria (test fixtures path, fixture IDs)
   - Target file paths (templated from task_type config)
   - Reference module path (existing skill/tool to mimic)
   - Exact `git worktree add` and branch name commands
   - Forbidden patterns (e.g., "do not embed secret values; use
     `vault.read('<name>')`")
2. Mirror the spec into `escalation_request.prompt_body`.
3. Discord notification:
   - Short summary
   - Correlation ID + dashboard link
   - Optional MD attachment of the spec
   - `[Approve $X / Manual / Pause / Cancel]` buttons (§4)

Dashboard escalation detail page for `claude_code` mode shows:
   - Full spec (rendered)
   - **Copy spec** button — paste straight into Claude Code
   - Pre-filled `git worktree add` command in a copy-on-click block
   - Branch name, target paths, reference module — all copyable
   - **Mark as built** button (replaces the chat-mode submit textarea)
     — opens a modal asking for the local branch SHA or push
     confirmation
   - Validation result panel (populated post-submission with pass/fail
     per fixture, lint outcomes)

**User → Donna:**
1. User opens dashboard escalation page, copies the worktree command.
2. User runs `git worktree add` per the spec.
3. User opens Claude Code in the worktree, pastes spec into the
   prompt. Claude Code reads the reference module, writes skill/tool
   + tests, runs `pytest`.
4. User commits on the branch (push optional — orchestrator can read
   local git).
5. User clicks **Mark as built** in the dashboard (or `/donna submit
   <correlation_id> --branch <name>` from Discord as fallback).

**Donna ingestion (existing pattern, mirrors `manual_draft_poller`):**
1. Polls `escalation_request` rows where `submitted_at IS NOT NULL
   AND status = 'submitted'`.
2. Verifies branch exists (local or remote).
3. Diffs branch against base, validates touched paths match the spec's
   declared targets (no rogue files, no edits outside scope).
4. Routes through existing validation pipeline:
   - Skills: `ValidationExecutor` against fixture set; sandbox →
     shadow → trusted promotion ladder unchanged.
   - Tools: lint check (must have mock entry, must have at least one
     agent allowlist update, must be inert at import), then validation.
5. On pass: existing skill/tool registry update path. On failure:
   post failures back to Discord; user iterates in same worktree;
   resubmit triggers same pipeline. Cap iterations at
   `manual_iteration_limit` (default 3) before forced cancel +
   `tool_request` entry for human review.

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
task_types:
  skill_draft:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill: "src/donna/skills/{name}.py"
        test:  "tests/skills/test_{name}.py"
      reference_module: "src/donna/skills/schema_inference.py"
  chat_escalation:
    manual_escalation:
      mode: chat
  evolution:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill: "src/donna/skills/{name}.py"
      reference_module: "src/donna/skills/{name}.py"      # in-place edit
```

Task types without a `manual_escalation` block are **never** offered
manual mode — only `Approve / Pause / Cancel`.

### 6.3 Dashboard runtime overrides + escalation workspace

The dashboard plays two roles for this subsystem: **(a) toggle
control panel** and **(b) escalation workspace** (the canonical place
to view/submit chat answers and mark claude_code work as built).

**(a) Toggle control panel**

New table `dashboard_setting (key TEXT PK, value JSON, updated_at,
updated_by)`. Resolution order: dashboard_setting → YAML default.

Dashboard surfaces (admin section, gated by existing auth):
- Master kill switch: **Manual escalation**: On / Off
- Per-mode toggles: **Chat**, **Claude Code**
- Budget extension: **Allow extensions**: On / Off
- Slider: **Max daily extension** (capped at `hard_monthly_ceiling_usd
  / days_left_in_month`)
- Per-task-type override grid: each task type with a manual mode shows
  a row with `Auto / Force-API / Force-Manual / Disabled`.

`hard_monthly_ceiling_usd` is **not** dashboard-mutable — only YAML.
Prevents a compromised dashboard session from authorizing unlimited
spend.

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
image rebuild). When `capability_tool_check` finds a missing tool,
Donna takes one of two paths based on **blocking severity**:

| Blocking severity | Trigger | Surfacing |
|---|---|---|
| **High** | Capability is active (scheduled or user-invoked) and cannot run | Real-time Discord ping with `[File request] [Snooze 24h]` |
| **Speculative** | Capability is registered but not yet scheduled, OR a skill draft proposed using a not-yet-existing tool | Filed silently to `tool_request` table; surfaces in morning digest |

Both paths write a `tool_request` row:

```sql
CREATE TABLE tool_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  proposed_signature JSON,
  rationale TEXT,
  blocking_capability_id INTEGER,    -- NULL = speculative
  priority INTEGER DEFAULT 3,        -- 1-5 like tasks
  status TEXT DEFAULT 'open',        -- open|in_progress|completed|rejected
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,
  resolved_branch TEXT
);
```

Tool builds use the same `claude_code` protocol as §5.3 but with
extra checks (§10 tool-build-specific failures): mock entry required,
`pyproject.toml` change requires `requires_rebuild=true`, secret
references must be by name not value.

Crucially: the **decision to start a tool build is always the user's**.
A real-time ping is a notification, not an escalation request — there
are no `[Approve $X extension]` buttons because no API spend will
fix the gap.

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

-- §7 — tool gaps
CREATE TABLE tool_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  proposed_signature JSON,
  rationale TEXT,
  blocking_capability_id INTEGER,
  priority INTEGER DEFAULT 3,
  status TEXT DEFAULT 'open',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,
  resolved_branch TEXT
);

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
| User submits but never builds the branch in claude_code mode | Poller checks `branch_exists(branch_name)` before processing. If absent after 5 min: posts "branch not found, did you push? or run /donna submit-local if local-only" — local-only path uses git plumbing to read the branch from the host repo. |
| User pushes a branch with wrong files (touched files outside spec scope) | Diff-validator rejects with specific list of out-of-scope files. User can edit and resubmit; iteration count increments. |
| User force-pushes branch between submission and validation | Resolution is locked to the SHA at submission time. New SHA = new submission required. |
| Branch contains uncommitted/staged changes mixed with the work | Diff is computed against `base..tip`, ignoring working tree. User's local mess is irrelevant. |

### 10.4 Validation failures (post-submission)

| Failure | Mitigation |
|---|---|
| Skill from manual handoff fails fixture validation | Failures posted to Discord; same correlation thread. User iterates in worktree, resubmits. Iteration cap `manual_iteration_limit` (3). |
| At iteration cap, still failing | Auto-cancel the escalation; create a `tool_request`-shaped row in a new `human_review_request` queue (or just log with `human_review` flag). User reviews via dashboard. No infinite loop. |
| Tool build missing mock entry | Pre-validation lint (§10.5). User cannot submit a tool without its mock — diff-validator rejects. |
| Tool build passes validation but breaks an existing skill in shadow | Standard regression handling: skill enters `flagged_for_review`, escalation marked `validated_with_warnings`, dashboard shows banner. |

### 10.5 Tool-build-specific failures (extends §10.4)

| Failure | Mitigation |
|---|---|
| Tool needs new dependency, image not rebuilt | Tool-build template requires `requires_rebuild: bool` field in tool metadata. If `true` after merge: registry refuses to mark tool active until orchestrator restart with new build SHA. Discord nag posted hourly until rebuild. |
| Tool hardcodes a credential value | Pre-commit secret scanner runs on the branch before validation. Common patterns (long entropy strings, `sk_`, `xoxb-`, etc.) blocked. Plus diff inspection: any string matching the vault key naming convention is flagged. |
| Tool calls Anthropic API directly (bypassing gateway) | Lint check: `import anthropic` outside `src/donna/llm/` is a hard fail. |
| Tool not added to any agent allowlist | Lint check. Submission requires at least one allowlist update or an explicit `unallowlisted=true` flag (which keeps the tool defined-but-unusable). |
| Tool does I/O at import time (would break ValidationExecutor) | Lint check: tool module's top-level scope must not invoke network/disk APIs. Heuristic + explicit `is_inert_at_import` test fixture. |
| Tool unbounded latency | Per-tool `default_timeout_seconds` declared in metadata; dispatcher enforces. Default 5s; tool builds set explicitly. |

### 10.6 Budget-extension-specific failures

| Failure | Mitigation |
|---|---|
| User approves extension; then estimate was wrong; actual cost overshoots | API call's `complete()` enforces a hard token limit derived from `extension_amount × token_rate`. Truncated output triggers a re-estimate + re-escalation rather than silent overspend. |
| Multiple extensions in one day stack to absurd amounts | `max_daily_extension_usd` enforced at button render time (button disabled if remaining headroom < estimate). |
| Approver clicks but interaction fails (Discord 5xx) | Idempotency: granting an extension is keyed on `(escalation_request_id, granted_by)`. Retry-safe. |
| Extension granted, task never runs (orchestrator crash) | On orchestrator boot, scan `escalation_request WHERE resolution='api_extended' AND task_status NOT IN ('completed','failed')`; resume or rollback the extension. Rolled-back extensions get a `voided=true` flag, never charged. |
| Hard monthly ceiling reached | All extension buttons disabled. Discord message reads "Monthly cap. Pause / Cancel only." |

### 10.7 Routing & toggle failures

| Failure | Mitigation |
|---|---|
| Dashboard toggle race (two browser tabs flip same setting) | `dashboard_setting` writes use `updated_at` optimistic lock; second write returns 409 with current value. |
| Config reload during in-flight escalation | Resolution semantics: an open escalation uses the offered_modes snapshotted in its row, NOT live config. Disabling claude_code mid-flight does not retroactively cancel an open claude_code escalation. |
| Task type has `manual_escalation: claude_code` but no reference_module configured | Validation at config load: any task type declaring claude_code mode MUST have target_paths + reference_module. Hard fail at boot. |
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
| Phase 2 multi-user enabled, escalations from one user trigger another's Discord | Every table has `user_id`; Discord routing uses each user's configured channel. Tested via integration fixture even in Phase 1. |
| Cross-user budget mixing | Budget is per-user from day one; no global pool. Existing `donna_models.yaml` budget keys would need to move into a per-user config table — call out as a follow-up but enforce schema today. |

### 10.10 Audit & observability

Every state transition writes to `invocation_log` with `task_type='escalation_lifecycle'` and a small JSON payload. Specifically log:
- `escalation_offered` — modes shown, estimate, remaining
- `escalation_resolved` — chosen mode, resolved_by
- `escalation_submitted` — branch, iteration
- `escalation_validated` / `escalation_failed`
- `extension_granted` / `extension_voided`
- `tool_gap_detected` / `tool_request_filed`
- `iteration_limit_reached`

Dashboard renders these as a timeline per escalation_request_id.

---

## 11. Verification / acceptance criteria

### Functional
- [ ] Cost router emits the four-button Discord message when estimate > min(daily_remaining, threshold) AND any escalation mode enabled.
- [ ] Each of `api_extended`, `chat`, `claude_code`, `pause`, `cancel` reaches the documented terminal state.
- [ ] Per-task-type `manual_escalation` config gates which buttons render.
- [ ] Dashboard toggles override YAML and take effect on next escalation (no restart).
- [ ] Master kill switch removes Manual handoff button entirely.
- [ ] Discord notification carries summary + dashboard link + optional MD attachment; full prompt body always lives in `escalation_request.prompt_body` and is rendered by the dashboard.
- [ ] Dashboard `/admin/escalations/<id>` page renders prompt, accepts answers (chat) or branch confirmations (claude_code), and writes back to the same row.
- [ ] Tool gaps file `tool_request` rows; high-blocking gaps ping in real time.
- [ ] All escalation outcomes logged to `invocation_log` with the correlation_id.
- [ ] Iteration cap hits force-cancel and write a `human_review` flag.

### Failure-mode regression tests (one per row in §10)
- [ ] Each mitigation has a corresponding fixture / unit test.
- [ ] Discord 5xx retry is integration-tested with mocked Twilio + Discord.
- [ ] Stale button click test asserts no double-resolution.
- [ ] Dashboard down → Discord MD attachment fallback exercised by a fixture.

### End-to-end
- [ ] **chat mode E2E:** trigger an over-budget chat_escalation; receive Discord prompt; submit answer via dashboard; task completes with answer as result.
- [ ] **claude_code mode E2E:** trigger over-budget skill_draft; receive Discord ping; build branch in worktree; mark as built via dashboard; skill enters sandbox state.
- [ ] **api_extended E2E:** approve extension; task runs; daily_remaining reflects extension; invocation_log carries escalation_request_id.
- [ ] **tool gap E2E:** add a capability that requires a missing tool; capability_tool_check fires; ping arrives in real time; user files request.

---

## 12. Open questions (for follow-up before slice work begins)

1. **Default `OWNER_DISCORD_ID` source** — env var, vault entry, or `auth.yaml`? (Spec assumes env var for now.)
2. **Local-only branches** — does the orchestrator have read access to the host repo's `.git` directory? If not, we add a small mount to support `submit-local` (not pushed) workflows.
3. **`human_review_request` table vs reusing `tool_request`** — do we want one queue for all manual interventions, or separate queues per kind?
4. **Tier 2 SMS escalation on Discord delivery failure** — confirm we want this; SMS rate limits in slice 7 are tight (10/day).
5. **Re-escalation parent chains** — current spec stores `parent_escalation_id`. Do we need a depth limit beyond `manual_iteration_limit`?

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

---

## Document History

- **2026-05-05** — Initial canonical spec authored. Decisions captured
  from the ToS / Claude Code / over-budget design conversation.
  Source plan file: `~/.claude/plans/would-it-be-against-playful-pelican.md`
  (kept until cross-doc edits per §3 are merged, then deleted).
