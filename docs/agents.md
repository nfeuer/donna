# Sub-Agent System

> Split from Donna Project Spec v3.0 — Sections 7, 8

## Agent Hierarchy

The Orchestrator (core process, not a sub-agent) receives all tasks and determines routing.

| Agent | Responsibilities | Tool Access | Autonomy Level |
|-------|-----------------|-------------|----------------|
| **Scheduler** | Calendar management, time slots, rescheduling, reminders, weekly planning | Google Calendar (read-write), Task DB (read-write) | High — auto-schedules priority 1–3 |
| **Research / Prep** | Web research, info compilation, resource gathering before flagged tasks | Web search (MCP), Gmail (read-only), Filesystem (MCP read), GitHub (MCP read) | High — runs autonomously when prep flagged |
| **Project Manager** | Task decomposition, requirements assessment, interrogation, work packaging | Task DB (read-write), all agents (dispatch) | Medium — can decompose and route, must confirm requirements with user |
| **Coding** | Code generation, file editing, project scaffolding | Filesystem (MCP sandboxed read-write), GitHub (MCP read-write), Claude Code CLI | Low — output for review only. Never pushes to main. Never deletes. |
| **Communication / Drafting** | Email drafts, message drafts, document creation | Gmail (draft only; send behind feature flag), Docs/markdown (write), Discord/Slack (specific channels only) | Low — always drafts. Never sends without explicit approval. |

## Agent Execution Flow

1. Orchestrator receives task → routes to PM Agent for assessment.
2. PM Agent evaluates completeness. If missing info → sends **targeted** questions (not open-ended).
   - Example: "For the Module A refactor, I need: (1) which API endpoints are affected, (2) should backward compatibility be maintained?"
3. User responds. PM Agent updates task.
4. PM Agent packages task with full context, requirements, acceptance criteria, file references.
5. PM Agent dispatches to execution agent.
6. Execution agent works. Progress logged to activity log.
7. On completion → user receives summary via email + notification. Output available for review.

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
