# Sub-Agent System

> Split from Donna Project Spec v3.0 — Sections 7, 8

## Agent Hierarchy

The Orchestrator (core process, not a sub-agent) receives all tasks and determines routing.

| Agent | File | Responsibilities | Tool Access | Autonomy Level |
|-------|------|-----------------|-------------|----------------|
| **Scheduler** | `scheduler_agent.py` | Calendar management, time slots, rescheduling, reminders, weekly planning | `calendar_read`, `calendar_write`, `task_db_read`, `task_db_write` | High — auto-schedules priority 1–3. 2-min timeout. |
| **Research / Prep** | `prep_agent.py` | Web research, info compilation, resource gathering before flagged tasks | Web search (MCP), Gmail (read-only), Filesystem (MCP read), GitHub (MCP read) | High — runs autonomously when prep flagged |
| **Project Manager** | `pm_agent.py` | Task assessment, requirements evaluation, agent routing recommendation | `task_db_read`, `task_db_write` | Medium — can assess and route, must confirm requirements with user. 5-min timeout. |
| **Challenger** | `challenger_agent.py` | Capability matching, intent extraction, task quality evaluation, follow-up questions | Task DB (read-only), `CapabilityMatcher` | Medium — probes task quality, returns questions to user via Discord thread |
| **ClaudeNoveltyJudge** | `claude_novelty_judge.py` | Evaluates no-capability-match messages via Claude. Returns structured `NoveltyVerdict` with intent, schedule, skill candidate assessment | Model router (Claude API) | Medium — called by DiscordIntentDispatcher on `escalate_to_claude` status |
| **DecompositionService** | `decomposition.py` | Breaks complex tasks into subtasks via LLM. Two-pass insert resolves dependency indices to UUIDs. Persists subtasks as real Task rows | Model router, Task DB (read-write) | Medium — decomposes autonomously, surfaces `missing_information` and `deadline_feasible` assessment |
| **ToolRegistry** | `tool_registry.py` | Validates and executes tool calls proposed by LLM agents. Enforces per-task-type tool allowlists from `task_types.yaml` | All registered tool handlers | N/A — infrastructure component, not an autonomous agent |
| **Coding** | _No source file_ | Code generation, file editing, project scaffolding | Filesystem (MCP sandboxed read-write), GitHub (MCP read-write), Claude Code CLI | Low — output for review only. Never pushes to main. Never deletes. *Phase 6 — [G-21](../superpowers/followups/open-backlog.md)* |
| **Communication / Drafting** | _No source file_ | Email drafts, message drafts, document creation | Gmail (draft only; send behind feature flag), Docs/markdown (write), Discord/Slack (specific channels only) | Low — always drafts. Never sends without explicit approval. *Phase 6 — [G-22](../superpowers/followups/open-backlog.md)* |

## Agent Source Files

```
src/donna/agents/
├── __init__.py              # Exports: DecomposeResult, DecompositionService, PrepAgent
├── base.py                  # AgentContext, AgentResult, ToolCallRecord, Agent protocol
├── pm_agent.py              # PMAgent — assessment, requirements, agent routing
├── challenger_agent.py      # ChallengerAgent — capability matching, intent extraction
├── claude_novelty_judge.py  # ClaudeNoveltyJudge — no-match escalation via Claude API
├── scheduler_agent.py       # SchedulerAgent — calendar slots, task scheduling
├── prep_agent.py            # PrepAgent — research/prep execution for flagged tasks
├── decomposition.py         # DecompositionService — task → subtask breakdown
└── tool_registry.py         # ToolRegistry — tool validation and execution layer
```

### Agent Base Types (`base.py`)

All agents implement the `Agent` protocol:

| Type | Purpose |
|------|---------|
| `Agent` | Runtime-checkable protocol: `name`, `allowed_tools`, `timeout_seconds`, `execute(task, context)` |
| `AgentContext` | Execution context: `router`, `db`, `user_id`, `project_root`, `tool_registry` |
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

### ToolRegistry

Infrastructure component (not an autonomous agent). Validates and executes tool calls proposed by LLM agents:

1. Agent model outputs a tool call request.
2. `ToolRegistry.is_allowed(task_type, tool_name)` checks against `task_types.yaml` allowlist.
3. If allowed, `ToolRegistry.execute(tool_name, params)` runs the registered async handler.
4. Raises `ToolNotAllowedError` or `ToolNotRegisteredError` on violations.

## Agent Execution Flow

> **Dormancy note (v3.1):** the PM-Agent-centric flow below is a **design target,
> not live behavior**. `AgentDispatcher` and the PM/Prep/Scheduler/Decomposition
> agents are built and unit-tested but **not wired into production**. The live
> path is: `DiscordIntentDispatcher` → `ChallengerAgent.match_and_extract` → (on
> escalation) `ClaudeNoveltyJudge`, with time-bound placement by the event-driven
> `AutoScheduler` (not an agent). The tool-validation layer below now enforces the
> allowlist on every call (`ToolRegistry.execute` requires `task_type`+`agent_name`
> as of v3.1), but per-parameter schema validation and `config/agents.yaml`
> autonomy enforcement remain unbuilt — they are preconditions for wiring the
> dormant pipeline or the Phase-6 Coding/Communication agents. See `spec_v3.md
> §7.2` and the Sub-Agent System critique design doc.

1. Orchestrator receives task → routes to PM Agent for assessment.
2. PM Agent evaluates completeness. If missing info → sends **targeted** questions (not open-ended).
   - Example: "For the Module A refactor, I need: (1) which API endpoints are affected, (2) should backward compatibility be maintained?"
3. User responds. PM Agent updates task.
4. **Challenger Agent** evaluates task quality. If the task is vague or missing critical context → opens a Discord thread with 1–3 probing questions about success criteria, dependencies, and scope. If the task is clear → passes through silently.
5. PM Agent packages task with full context, requirements, acceptance criteria, file references.
6. PM Agent dispatches to execution agent.
7. Execution agent works. Progress logged to activity log.
8. On completion → user receives summary via email + notification. Output available for review.

### Challenger Agent Details

The Challenger Agent runs on the **local LLM** (`challenge_task` task type → `local_parser` alias → Ollama qwen2.5:32b) at zero API cost. It sits in the dispatcher pipeline between PM assessment and execution agent dispatch.

**Behavior:**
- Evaluates task description richness, not just field presence (that's the PM Agent's job).
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
