# Sub-Agent System

> Split from Donna Project Spec v3.0 — Sections 7, 8

## Agent Hierarchy

The Orchestrator (core process, not a sub-agent) receives all tasks and determines routing.

| Agent | File | Responsibilities | Tool Access | Autonomy Level |
|-------|------|-----------------|-------------|----------------|
| **Research / Prep** | `prep_agent.py` | Web research, info compilation, resource gathering before flagged tasks | Web search (MCP), Gmail (read-only), Filesystem (MCP read), GitHub (MCP read) | High — runs autonomously when prep flagged |
| **Challenger** | `challenger_agent.py` | Capability matching, intent extraction, task quality evaluation, follow-up questions | Task DB (read-only), `CapabilityMatcher` | Medium — probes task quality, returns questions to user via Discord thread |
| **ClaudeNoveltyJudge** | `claude_novelty_judge.py` | Evaluates no-capability-match messages via Claude. Returns structured `NoveltyVerdict` with intent, schedule, skill candidate assessment | Model router (Claude API) | Medium — called by DiscordIntentDispatcher on `escalate_to_claude` status |
| **DecompositionService** | `decomposition.py` | Breaks complex tasks into subtasks via LLM. Two-pass insert resolves dependency indices to UUIDs. Persists subtasks as real Task rows | Model router, Task DB (read-write) | On demand — triggered by the `/breakdown` command (R2); surfaces `missing_information` and `deadline_feasible` assessment |
| **ToolRegistry** | `tool_registry.py` | Validates and executes tool calls proposed by LLM agents. Enforces per-task-type tool allowlists from `task_types.yaml` | All registered tool handlers | N/A — infrastructure component, not an autonomous agent |
| **Coding** | _No source file_ | Code generation, file editing, project scaffolding | Filesystem (MCP sandboxed read-write), GitHub (MCP read-write), Claude Code CLI | Low — output for review only. Never pushes to main. Never deletes. *Phase 6 — [G-21](../superpowers/followups/open-backlog.md)* |
| **Communication / Drafting** | _No source file_ | Email drafts, message drafts, document creation | Gmail (draft only; send behind feature flag), Docs/markdown (write), Discord/Slack (specific channels only) | Low — always drafts. Never sends without explicit approval. *Phase 6 — [G-22](../superpowers/followups/open-backlog.md)* |

## Agent Source Files

```
src/donna/agents/
├── __init__.py              # Exports: DecomposeResult, DecompositionService, PrepAgent
├── base.py                  # AgentContext, AgentResult, ToolCallRecord
├── challenger_agent.py      # ChallengerAgent — capability matching, intent extraction
├── claude_novelty_judge.py  # ClaudeNoveltyJudge — no-match escalation via Claude API
├── prep_agent.py            # PrepAgent — research/prep execution for flagged tasks
└── decomposition.py         # DecompositionService — task → subtask breakdown
```

> **Tool validation lives on the skills path.** The tool-validation seam is
> implemented by the **skills** `ToolRegistry`
> (`src/donna/skills/tool_registry.py`), not an agent-layer registry — see
> *Tool Validation Seam* below. The separate `agents/tool_registry.py` was
> deleted in the §7.2 resolution (R3, 2026-06-18); nothing flowed through it.

### Agent Base Types (`base.py`)

Shared dataclasses used by the live agents (Challenger, NoveltyJudge, Prep):

| Type | Purpose |
|------|---------|
| `AgentContext` | Execution context: `router`, `user_id`, `project_root`. (The unused `db` and `tool_registry` fields were removed in the §7.2 R3 resolution — a raw `db` handle let an agent bypass the validated tool path, against principle #6. Live agents read only `router`/`user_id`.) |
| `AgentResult` | Outcome: `status` (complete/failed/needs_input/escalated), `output`, `tool_calls_made`, `duration_ms`, `error`, `questions` |
| `ToolCallRecord` | Record of a single tool call: `tool_name`, `params`, `result`, `allowed` |

### ClaudeNoveltyJudge

Called by `DiscordIntentDispatcher` when `ChallengerAgent` emits `status=escalate_to_claude`. Returns a `NoveltyVerdict` dataclass containing:

- `intent_kind`, `trigger_type`, `extracted_inputs`
- `schedule`, `deadline`, `alert_conditions`, `polling_interval_suggestion`
- `skill_candidate` (bool) + `skill_candidate_reasoning`
- `clarifying_question`, `notification_channels`

Uses the `claude_novelty` task type, validates output via `validate_output`, and consults the `CapabilityMatcher` for context.

### DecompositionService

Breaks a complex task into subtasks via the `task_decompose` prompt. Returns a `DecomposeResult` with `subtask_ids`, `total_estimated_hours`, `missing_information`, and `deadline_feasible`. Uses a two-pass insert: first creates all subtask rows, then resolves integer dependency indices from LLM output to real UUIDs.

### Tool Validation Seam (live, on the skills path)

Tool execution is gated and validated by the **skills** `ToolRegistry`
(`src/donna/skills/tool_registry.py`), driven by
`SkillExecutor._execute_step → ToolDispatcher.run_invocation →
ToolRegistry.dispatch`. This is the load-bearing realization of CLAUDE.md
principle #6 (*models propose, the orchestrator validates and executes*) after
the §7.2 R3 resolution (2026-06-18):

1. **Allowlist (access gate).** The per-step `tools:` allowlist declared in the
   skill YAML is the access decision; `dispatch()` raises `ToolNotAllowedError`
   if the tool isn't on it, `ToolNotFoundError` if it isn't registered.
2. **Per-tool parameter schema (fail-closed).** Each tool registers a
   declarative JSON schema (`schemas/tools/<tool>.json`, loaded by
   `tool_param_schemas.py`). `dispatch()` validates the call's args against it
   **before** invoking the handler; invalid args raise `ParameterValidationError`
   and the handler never runs. Every built-in tool is schema'd; the no-schema
   branch (ad-hoc/test registrations only) logs + fires a `fallback_activated`
   alert rather than silently skipping. The dispatcher treats a validation error
   as a deterministic, **non-retryable** failure.
3. **Caller-identity audit.** `task_type` + `agent_name` (the skill's capability
   name) are threaded executor → dispatcher → registry and recorded on the
   `tool_executed` structured log for every execution — an audit trail, not a
   second gate.

`config/agents.yaml` remains the per-agent allowlist *registry* (challenger /
research) behind the tool-lint check + admin UI; intersecting it as a runtime
ceiling is deferred to G-21/G-22 (the live path is skill-driven).

## Agent Execution Flow

This is the **live** flow. The v3.1 multi-stop `AgentDispatcher`/PM pipeline
(`PMAgent` → `SchedulerAgent`/Prep dispatch over a uniform `Agent` dispatch
contract) was **removed 2026-06-17** — resolution **keep-the-ideas,
drop-the-framework**. `DecompositionService` is now wired as a direct service
(R2, shipped 2026-06-17) behind the `/breakdown` command; the tool-validation
seam was made load-bearing on the live skills path (R3, shipped 2026-06-18 — see
*Tool Validation Seam* above); `config/agents.yaml` remains the live allowlist
registry (challenger/research) behind the tool-lint safety check and admin UI.
See `spec_v3.md §7.2` and
[`2026-06-17-subagent-72-resolution-design.md`](../superpowers/specs/2026-06-17-subagent-72-resolution-design.md).

1. A tasks-channel message is routed by the **`DiscordIntentDispatcher`** to
   **`ChallengerAgent.match_and_extract`**, which returns one of
   {`ready` | `needs_input` | `escalate_to_claude`}.
   - `needs_input` → Donna prompts the user for the missing detail in a
     clarification thread.
   - `escalate_to_claude` → the **`ClaudeNoveltyJudge`** performs intent /
     schedule / capability matching (deciding whether the task becomes a new
     capability candidate or is handled ad-hoc).
2. Time-bound task **placement** is done by the event-driven **`AutoScheduler`**
   (not an agent) via `Scheduler.find_next_slot` and
   `Scheduler.negotiate_placement` (the propose-and-confirm negotiation loop,
   see [scheduling](scheduling.md)).
3. **Prep** research runs as the **`PrepAgent`** background loop, which picks up
   tasks carrying `prep_work_flag` once they fall inside the configured
   lead-time window and attaches the prepared context.
4. **Decomposition** of a complex task into a sequenced subtask graph is
   triggered on demand by the **`/breakdown <task>`** Discord command, which
   calls **`DecompositionService`** directly (no dispatcher; principle #4),
   persists each subtask as a real Task row (`parent_task` set, dependency
   indices resolved to UUIDs), and renders the plan back to the user.
5. Progress is logged to the activity log; on completion the user receives a
   summary via the originating channel (typically Discord).

### Challenger Agent Details

The Challenger Agent runs on the **local LLM** (`challenge_task` task type → `local_parser` alias → Ollama qwen2.5:32b) at zero API cost. It is the first stop on the live tasks-channel path: the `DiscordIntentDispatcher` calls it to match capabilities, extract intent, and probe task quality.

**Behavior:**
- Evaluates task description richness, not just field presence.
- Generates 1–3 focused questions about: what "done" looks like, hidden dependencies, scope boundaries.
- Returns `status="complete"` (no questions) if the task is well-specified.
- On LLM failure, silently passes through — never blocks task creation.

**Discord Integration:**
- When questions are needed, a Discord thread is created on the original task message.
- User replies in-thread are appended to task description/notes.
- One round of follow-up per task (thread closes after first reply).

**Does not gate scheduling of dated tasks.** Time-bound tasks (those with an
`exact` / `window` / `constrained` [`time_intent`](task-system.md#time-intent))
are routed straight to the Scheduler by the deterministic
[routing gate](scheduling.md#routing-gate) and scheduled immediately, regardless
of whether the Challenger is still probing. The Challenger runs in parallel and
only enriches task quality; it no longer blocks the scheduling of dated work.
Only undated tasks remain in backlog where the Challenger surfaces them. (This
fixed a bug where time-bound tasks stranded in `backlog` awaiting the Challenger;
see followup TI-FU1.)

## LLM-Generated Nudges & Reminders

Overdue nudges and pre-task reminders are generated by the **local LLM** (`generate_nudge` / `generate_reminder` task types → `local_parser`). This replaces hardcoded template strings with contextual, Donna-persona messages at zero API cost.

**Nudge generation (`overdue.py`):**
- Prompt includes task title, domain, priority, overdue duration, nudge count, and reschedule count.
- Tone escalates based on nudge history: friendly → firm → assertive.
- If reschedule_count > 3, calls out the pattern directly.
- Fallback: original template string if Ollama is unreachable.

**Reminder generation (`reminders.py`):**
- Pre-task reminder 15 minutes before scheduled start.
- Prompt includes task context and description for personalized reminders.
- Fallback: `"⏰ '{title}' starts in 15 minutes. Duration: {duration}."`.

**Nudge tracking:**
- Every nudge is persisted to the `nudge_events` table (type, channel, tier, message, LLM flag).
- `tasks.nudge_count` is atomically incremented on each nudge.
- Stats available via `Database.get_weekly_stats()` for the weekly digest.

## Weekly Efficiency Digest

Fires every **Sunday at 7 PM UTC** via the `WeeklyDigest` class. Assembles task completion stats from the past 7 days and generates a Donna-voiced efficiency report.

**Stats collected:**
- Tasks completed vs created, completion rate percentage
- Average time to complete (hours)
- Top 5 most-nudged tasks (by `nudge_count`)
- Top 5 most-rescheduled tasks (by `reschedule_count`)
- Domain breakdown (completed, open, avg nudges per domain)
- Total nudges sent this week
- LLM cost this week (from `invocation_log`)

**Output:**
- LLM-generated report (`generate_weekly_digest` → `local_parser`) posted as Discord embed in #donna-digest.
- Donna persona: 2–3 sentence summary, one observed pattern, one actionable suggestion.
- Fallback: plain-text stats table if Ollama is down.

**Configuration:** Task type `generate_weekly_digest` in `config/task_types.yaml`, routed to `local_parser` with `parser` (Claude) fallback in `config/donna_models.yaml`.

## Agent Safety Constraints (Non-Negotiable)

Enforced at the **system level**, not reliant on agent prompting:

| Constraint | Enforcement |
|-----------|-------------|
| No sending emails externally | Gmail API scoped to draft-only by default. Send scope gated behind feature flag (disabled by default). Enabling requires config change + OAuth re-auth. |
| No deleting files | Filesystem is append/modify only. Deletes require explicit user command. |
| No pushing to main/production | GitHub API restricts to feature branches. Branch protection at GitHub level. |
| No purchases or financial transactions | No payment APIs integrated. No browser automation. |
| No modifying user-created calendar events | Scheduler only modifies events tagged `donnaManaged: true`. |
| Backup before code changes | Coding agent creates git stash or branch backup before any file modification. |
| Agent timeout | Configurable per invocation (default 10 min coding, 5 min research). Timeout → user notification + `agent_status = failed`. |

**Principle:** All agents start minimal. Constraints relaxed only after reviewing logged performance and explicitly updating config.

## Local LLM Tool Use Progression

RTX 3090 is available. Local model validation on basic parsing is the prerequisite for each stage.

### Stage 1: Read-Only Tools, Single Call (Month 1)

- **Tools:** `task_db_read`, `calendar_read`
- **Purpose:** Context enrichment during parsing (dedup check, resolving "before my meeting" to actual time)
- **Evaluation:** Offline harness + shadow mode with Claude
- **Promotion threshold:** 90%+ accuracy on tool selection and parameters over 100+ samples

### Stage 2: Conditional Tool Use (Month 2)

- **Challenge:** Model decides *whether* to use a tool. "buy milk" = no tool; "buy milk before my 3pm meeting" = `calendar_read`
- **Tracking:** Log unnecessary tool calls (false positive) and missed tool calls (false negative)
- **Promotion threshold:** 85%+ precision and recall over 100+ samples

### Stage 3: Write Tools with Guardrails (Month 3, if Stage 2 solid)

- **Tools:** `task_db_write` (create tasks directly)
- **Guardrails:** Model proposes write → orchestrator validates against schema → rejects malformed entries. Model never writes to calendar or triggers notifications directly.
- **Evaluation:** Compare model-proposed entries against Claude/human-created entries from same input.

### Tool Execution Architecture

Model **never** calls tools directly. Flow:

1. Model outputs tool call request
2. Orchestrator validates (is this tool allowed for this task type? parameters well-formed?)
3. Orchestrator executes via integration module
4. Result fed back to model

Tool access per task type defined in `config/task_types.yaml`. A task type with `tools: [calendar_read]` cannot result in a `task_db_write` call regardless of what the model requests.
