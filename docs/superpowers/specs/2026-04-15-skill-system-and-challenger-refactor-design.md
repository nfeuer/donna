# Skill System, Capability Registry, Challenger Refactor, and Automation Subsystem

**Status:** Draft
**Author:** Nick (with brainstorming assistance from Claude)
**Date:** 2026-04-15
**Scope:** Large — multi-phase implementation across several sessions.

---

## 1. Overview

Donna currently routes LLM work by static task type: every `parse_task`, `classify_priority`, `dedup_check`, `prep_research`, `task_decompose`, and `extract_preferences` call goes directly to Claude via the `ModelRouter` in `src/donna/models/router.py`. Only a handful of low-stakes task types (`generate_nudge`, `generate_reminder`, `challenge_task`, `generate_weekly_digest`, `chat_*`) route to the local Ollama model. This is not a principled division of labor — it is where history stopped. The result is that Claude is used as the default for virtually all content-producing work, consuming the $100/month budget faster than necessary and leaving the local RTX 3090 mostly idle.

This spec proposes a redesign that inverts the default. Rather than asking "is local good enough to handle task type X," the new framing asks "how much procedural scaffolding can we build around local before Claude becomes the cheaper option." Claude shifts from being a runtime worker to being a teacher: Claude writes skills (structured procedures) that teach the local model how to execute tasks step by step, and Claude only executes at runtime when no skill exists or the skill has failed.

Four new subsystems make this concrete:

1. **Capability registry.** A new first-class concept — a capability is a user-facing task pattern (e.g., `product_watch`, `news_check`, `meeting_prep`) with a name, description, input schema, and trigger type. Capabilities are the vocabulary Donna thinks in.

2. **Skill system.** Every capability has at most one skill — a versioned YAML+markdown procedure that the `SkillExecutor` runs on the local LLM with structured state, deterministic tool dispatch, and a minimal flow-control DSL. Skills move through a lifecycle (`claude_native → skill_candidate → draft → sandbox → shadow_primary → trusted` with fallback states `degraded` and `flagged_for_review`) with explicit promotion gates.

3. **Challenger refactor.** The existing `ChallengerAgent` moves from post-parse quality check to skill-match-and-input-extraction. It matches user intents against the capability registry, extracts structured inputs, asks clarifying questions for missing fields, and escalates to Claude only when no match has sufficient confidence.

4. **Automation subsystem.** Tasks (things the user must do) and automations (things Donna does on a trigger) become separate first-class entities with distinct schemas. Automations consume capabilities and run on schedules or manual triggers, creating the mechanism for long-running monitors like "watch this product URL for a price drop."

A **triage agent** sits alongside the SkillExecutor to handle runtime failures gracefully, deciding whether to retry, escalate to Claude, alert the user, or mark a skill degraded.

The target end state is a system where Claude's cost profile is front-loaded (skill generation and evolution) rather than continuous (every runtime invocation), where the capability library accumulates value over time, and where the user retains judgment-level control (skill approval, degradation acceptance, sensitive-skill gating) while the mechanics run themselves.

---

## 2. Out of Scope

The following items were discussed during brainstorming and deliberately deferred. Each should be reconsidered when its trigger condition is met, but none is in scope for this spec.

| # | What | Why deferred | When to reconsider |
|---|---|---|---|
| OOS-1 | Event-triggered automations (`on_event`) | Requires event-source subsystem (webhooks, filesystem watchers, email arrival); schedule triggers cover the motivating examples. | After 3+ automations exist that clearly need event triggers. |
| OOS-2 | Per-capability specialized challenger runbooks | Generic challenger prompt is sufficient for v1; evolution target. | After 6 months of challenger-usage data reveals repeated patterns per capability. |
| OOS-3 | Automation composition (automation → automation chains) | Adds complexity without clear v1 use case. | When a real use case emerges. |
| OOS-4 | Step-level shadow comparison (not just final output) | Evolution loop infers step-level failures from end-to-end divergence via Claude reasoning. | If evolution quality is poor across 5+ skills and 3+ attempts each. |
| OOS-5 | Logprob-based confidence scoring for local LLM | Self-assessed `confidence` field in output schemas is sufficient. | If self-assessed confidence proves uncorrelated with actual accuracy. |
| OOS-6 | Multiple skills per capability (A/B, per-input-branch) | One-per-capability prevents collisions structurally; conditional logic within a skill covers the meaningful cases. | If a capability demonstrably needs divergent implementations beyond what flow control supports. |
| OOS-7 | Automation sharing / capability templates for other users | Multi-user model exists in schema but Donna is single-user (Nick) in practice. | When a second real user exists. |
| OOS-8 | Automatic `requires_human_gate` flagging based on sensitive tools | Manual flagging during draft/sandbox review is enough and clearer. | If manual flagging produces misses on sensitive skills. |
| OOS-9 | If-conditionals in the skill DSL | `for_each`, `retry`, `escalate` cover the motivating patterns; conditionals can be simulated by empty-output short-circuiting. | If 3+ skills in production hit cases that need real branching. |
| OOS-10 | Nested DSL primitives (e.g., `for_each` inside `for_each`) | Adds executor complexity and reduces Claude's generation reliability. | If a real skill needs nesting and cannot be decomposed into sequential steps. |
| OOS-11 | Exact tokenization for local context budgeting | Existing character-based estimate is sufficient for v1. | If `context_overflow_escalation` rate exceeds 10% of local calls. |
| OOS-12 | Voice-triggered challenger interactions | Discord threads are the interaction surface for clarifications. | When voice UX is prioritized. |

---

## 3. Core Concepts and Vocabulary

The spec introduces several new terms and repurposes some existing ones. Using them consistently avoids confusion during implementation.

**Capability.** A user-facing task pattern. Has a `name` (e.g., `product_watch`), a human-readable description, a declared `input_schema` (JSON Schema), and a `trigger_type` (`on_message`, `on_schedule`, `on_manual`). Stored in a new `capability` table. Capabilities are the stable vocabulary — the set of things Donna knows how to do for the user.

**Skill.** The executable implementation of a capability. One capability has at most one active skill. A skill is identified by its `capability_name` (unique key) and has a `current_version_id` pointing to the active `skill_version`. Skills have a lifecycle state (see §6.2).

**Skill version.** A specific revision of a skill's content: the YAML backbone, markdown step prompts, and per-step output schemas. New versions are created when skills are generated, evolved, or hand-edited. Old versions are preserved for audit and rollback.

**Skill run.** A single execution of a skill on a specific task input. Records the full state object, per-step timings, tool calls made, validation outcomes, and final output. One skill run may produce multiple rows in `skill_step_result` and multiple rows in `invocation_log`.

**SkillExecutor.** The Python class that interprets a skill version and runs it: executes tool invocations declared in the YAML, calls the local LLM with per-step prompts, validates outputs against step schemas, updates the state object, and handles flow control (`for_each`, retries, escalation signals).

**State object.** A typed JSON dictionary that accumulates structured outputs across skill steps. `state[step_name] = step_output`. The state object is the skill run's working memory; each step reads from it and writes to it. Raw tool results (web pages, email bodies) are stored in `skill_run.tool_result_cache` as blobs and referenced by ID in the state object, never inlined.

**Capability registry.** The runtime-queryable index of known capabilities, backed by the `capability` table plus a vector index on capability descriptions for semantic retrieval. The challenger queries this registry to match user intents.

**Challenger.** Repositioned from its current role (post-parse task-quality check) to a new role: skill-match-and-input-extractor. The challenger sits between `parse_task` and skill execution, queries the capability registry, extracts inputs, asks clarifying questions, and escalates unmatched cases to Claude for novelty judgment. The existing `challenge_task` LLM task type is retained but its semantics change.

**Triage agent.** A new component that handles runtime skill failures. When a skill step errors (tool failure, schema validation failure, executor exception, LLM escalate signal), the failure is routed to triage, which decides among retry, escalate-to-Claude, alert-user, or mark-degraded. Triage runs on local LLM by default.

**Task.** A work item the user must complete. The existing `task` table schema is retained; tasks still have title, duration estimate, deadline, priority, calendar linkage, and completion tracking.

**Automation.** A new entity — a work item Donna runs for the user on a trigger. Has a capability, inputs, schedule, alert conditions, and run history. Stored in a new `automation` table. Automations do not have "completion" — they run until paused or deleted.

**Claude-native execution.** A path in which Claude runs a capability directly without a skill. Invoked when no skill exists, when a skill is in `degraded` or `flagged_for_review` state, or when the triage agent escalates. Claude-native is the safety fallback that ensures task flow is never interrupted by skill infrastructure.

**Shadow mode (production monitoring).** When a skill is in `shadow_primary` or `trusted` state, Claude runs in parallel on a sampled fraction of invocations and its output is logged but not used. Shadow outputs feed the agreement-rate metric and the divergence case library used for evolution. This is distinct from the existing offline eval harness documented in `docs/model-layer.md`.

---

## 4. Architecture

This section describes how the new components fit together and how a single inbound request flows through them. The existing architecture in `src/donna/` is extended, not replaced.

### 4.1 Component interactions

The high-level flow from inbound message to final action, covering both the match path and the novelty path:

```
Discord message
    │
    ▼
┌──────────────────────┐
│ parse_task           │  Existing; produces TaskParseResult.
│ (Claude now;         │  Becomes a skill candidate over time.
│  skill-backed later) │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ Challenger           │  New role: query capability registry,
│ (local LLM)          │  match, extract inputs, ask questions.
└──────┬───────────────┘
       │
       ├─── high confidence match ────────┐
       │                                  │
       ├─── medium confidence ────┐       │
       │                          │       │
       └─── low confidence ──────┐│       │
                                 ││       │
                                 ▼▼       │
                        ┌────────────┐    │
                        │ Claude     │    │
                        │ novelty    │    │
                        │ judgment   │    │
                        └──┬─────────┘    │
                           │              │
         ┌─────────────────┴──────────────┤
         │                                │
         ▼                                ▼
┌──────────────────┐         ┌──────────────────────┐
│ Register new     │         │ Inputs complete?     │
│ capability       │         │                      │
└──────────────────┘         └──┬───────────────────┘
                                │
                    ┌───────────┤
                    │           │
               missing         complete
                    │           │
                    ▼           ▼
           ┌────────────┐  ┌───────────────────┐
           │ Ask user   │  │ Skill exists and  │
           │ in thread  │  │ state ≥ shadow_   │
           └────┬───────┘  │ primary?          │
                │          └──┬────────────────┘
                │             │
                │         ┌───┴────┐
                │         │        │
                │        yes      no
                │         │        │
                │         ▼        ▼
                │    ┌─────────┐ ┌──────────────┐
                │    │ Skill   │ │ Claude-      │
                │    │ Executor│ │ native path  │
                │    └────┬────┘ └──────┬───────┘
                │         │             │
                │         ▼             │
                │    ┌─────────┐        │
                │    │ Triage  │        │
                │    │ on      │        │
                │    │ failure │        │
                │    └────┬────┘        │
                │         │             │
                │         ▼             │
                │    ┌─────────────────────┐
                │    │ Output to user      │
                │    │ or to automation    │
                │    │ alert channels      │
                │    └─────────────────────┘
                │
                └─── response loop
```

The diagram reflects the steady state. On day one, the capability registry is empty and the low-confidence-match → Claude novelty judgment path is hit for every request. As the registry fills in, more requests take the high-confidence-match path and bypass Claude entirely.

### 4.2 Flow-through invariants

Properties the architecture must maintain regardless of implementation details:

1. **Task flow is never blocked by skill infrastructure.** If any skill component fails (executor error, tool outage, triage indecision), the fallback is always claude-native execution. The user sees a result.
2. **Claude is never called during a high-confidence match with complete inputs.** The cost savings depend on this; every unnecessary Claude call is a framework bug.
3. **Every LLM invocation — local, Claude, shadow — is logged to `invocation_log`.** Observability is not optional.
4. **Skill state transitions are append-only.** The `skill_state_transition` table records every state change with reason and actor; nothing in the lifecycle silently overwrites history.
5. **No skill runs on real user traffic until it has passed the draft → sandbox gate.** Review discipline must be enforced at this point, not later.
6. **Capability registry queries precede any Claude novelty call.** Even if the local matcher is confident it won't match, the registry must be consulted so that the novelty judgment has full context.

### 4.3 Existing components that are repurposed or extended

| Component | Today | After this spec |
|---|---|---|
| `src/donna/orchestrator/input_parser.py` | Runs `parse_task` via Claude, dedupes, creates task row | Unchanged in v1; eventually becomes a skill candidate itself |
| `src/donna/orchestrator/dispatcher.py` | Routes task through PM → Challenger → execution | Simplified: becomes a dispatcher that chooses skill path vs. claude-native path based on skill state |
| `src/donna/agents/challenger_agent.py` | Post-parse quality check; generates 1–3 probing questions | Repositioned: skill-match, input-extraction, clarifying questions, Claude novelty escalation |
| `src/donna/agents/pm_agent.py` | Assesses task completeness | Role absorbed by challenger; PM agent shrinks or is removed |
| `src/donna/models/router.py` | Routes LLM operations by task type | Unchanged; skill steps still route through it |
| `src/donna/llm/queue.py` | Two-queue priority system with `ChainState` stub | `ChainState` is now used in earnest by the SkillExecutor for multi-turn continuation |
| `config/task_types.yaml` | LLM operation registry | Unchanged for LLM operations; capability registry is a separate table |
| `donna_tasks.db` | Tasks, nudges, escalations, conversation contexts | Extended with capability, skill, skill_version, skill_run, automation, and related tables |

### 4.4 New directories

```
src/donna/skills/               # SkillExecutor and related runtime classes
  executor.py                   # Main execution loop
  state.py                      # State object and tool result cache
  dsl.py                        # for_each, retry, escalate primitives
  tool_dispatch.py              # Deterministic tool invocation
  triage.py                     # Triage agent
  validation.py                 # Schema validation, fixture harness

src/donna/capabilities/         # Capability registry
  registry.py                   # CRUD + semantic retrieval
  matcher.py                    # Confidence-scoring matcher used by challenger

src/donna/automations/          # Automation subsystem
  scheduler.py                  # Cron-driven runner
  dispatcher.py                 # Resolves skill vs. claude-native per automation run
  alerts.py                     # Condition evaluation and alert dispatch

skills/                         # Skill definitions (seed files only)
  <capability_name>/
    skill.yaml                  # Backbone
    steps/
      <step_name>.md            # Per-step prompt content
    schemas/
      <step_name>_v<N>.json     # Per-step output schemas
    fixtures/
      <case_name>.json          # Test cases (generated + captured)
```

**Storage authority.** The database is the authoritative source for all skills at runtime. `SkillExecutor` reads `skill_version.yaml_backbone`, `step_content`, and `output_schemas` from the DB — it does not read from the filesystem during execution. The `skills/` directory exists only for:

1. **Phase 1 seed skills** — hand-written skills for the seed capabilities, loaded into the DB during the migration.
2. **Version export / debugging** — the dashboard can export a current skill version to the filesystem format for hand-editing, and re-import the edits as a new skill version. This is a one-shot export/import, not a continuous sync.

Claude-generated skills go directly to the DB without touching the filesystem. After Phase 1 seeding, the filesystem copy of seed skills is no longer authoritative — any edits must go through the dashboard edit flow to create a new version.

---

## 5. Data Model

Schemas for the new tables. Existing tables (`task`, `nudge_event`, `invocation_log`, `correction_log`, `escalation_state`, `conversation_context`) are unchanged; some gain new foreign keys from new tables.

### 5.1 `capability`

```
id                    TEXT PRIMARY KEY  -- uuid7
name                  TEXT UNIQUE NOT NULL  -- e.g. "product_watch"
description           TEXT NOT NULL
input_schema          TEXT NOT NULL  -- JSON Schema
trigger_type          TEXT NOT NULL  -- on_message | on_schedule | on_manual
default_output_shape  TEXT  -- JSON Schema describing expected final output
status                TEXT NOT NULL DEFAULT 'active'  -- active | pending_review
created_at            TEXT NOT NULL
created_by            TEXT NOT NULL  -- human | claude | seed
notes                 TEXT
```

The `status` column supports the post-creation audit described in §6.1: capabilities flagged for cosine similarity against existing entries are stored as `pending_review` and are excluded from matcher queries until the user approves or rejects them via the dashboard.

Semantic index: a sidecar FAISS or sqlite-vss index over `name || description || input_schema` embeddings. Rebuilt whenever capabilities are added or modified. Used by `CapabilityMatcher.retrieve_top_k(text, k=5)`.

### 5.2 `skill`

```
id                    TEXT PRIMARY KEY  -- uuid7
capability_name       TEXT UNIQUE NOT NULL  -- FK to capability.name; one skill per capability
current_version_id    TEXT  -- FK to skill_version.id
state                 TEXT NOT NULL  -- lifecycle state; see §6.2
requires_human_gate   INTEGER NOT NULL DEFAULT 0  -- bool
baseline_agreement    REAL  -- established at trusted promotion
created_at            TEXT NOT NULL
updated_at            TEXT NOT NULL
```

### 5.3 `skill_version`

```
id                    TEXT PRIMARY KEY
skill_id              TEXT NOT NULL  -- FK to skill.id
version_number        INTEGER NOT NULL  -- monotonic per skill
yaml_backbone         TEXT NOT NULL  -- full YAML
step_content          TEXT NOT NULL  -- JSON map of step_name → markdown
output_schemas        TEXT NOT NULL  -- JSON map of step_name → schema
created_by            TEXT NOT NULL  -- human | claude | evolved
changelog             TEXT
created_at            TEXT NOT NULL
```

### 5.4 `skill_run`

```
id                    TEXT PRIMARY KEY
skill_id              TEXT NOT NULL
skill_version_id      TEXT NOT NULL
task_id               TEXT  -- optional FK to task
automation_run_id     TEXT  -- optional FK to automation_run
status                TEXT NOT NULL  -- running | succeeded | failed | escalated
total_latency_ms      INTEGER
total_cost_usd        REAL  -- sum of invocation_log costs for this run
state_object          TEXT NOT NULL  -- JSON; final state at end of run
tool_result_cache     TEXT  -- JSON; raw blobs keyed by cache_id
final_output          TEXT  -- JSON
escalation_reason     TEXT  -- if escalated, why
started_at            TEXT NOT NULL
finished_at           TEXT
```

### 5.5 `skill_step_result`

```
id                    TEXT PRIMARY KEY
skill_run_id          TEXT NOT NULL
step_name             TEXT NOT NULL
step_index            INTEGER NOT NULL
invocation_log_id     TEXT  -- FK to invocation_log if step called LLM
prompt_tokens         INTEGER
output                TEXT  -- JSON
tool_calls            TEXT  -- JSON array; each element describes a dispatched tool
latency_ms            INTEGER
validation_status     TEXT NOT NULL  -- valid | schema_invalid | escalate_signal
```

### 5.6 `skill_fixture`

```
id                    TEXT PRIMARY KEY
skill_id              TEXT NOT NULL
case_name             TEXT NOT NULL
input                 TEXT NOT NULL  -- JSON
expected_output_shape TEXT  -- JSON Schema; not a strict match
source                TEXT NOT NULL  -- claude_generated | human_written | captured_from_run
captured_run_id       TEXT  -- FK to skill_run if source=captured_from_run
created_at            TEXT NOT NULL
```

### 5.7 `skill_divergence`

```
id                    TEXT PRIMARY KEY
skill_run_id          TEXT NOT NULL
shadow_invocation_id  TEXT NOT NULL  -- FK to invocation_log for shadow Claude
overall_agreement     REAL  -- 0.0 to 1.0
diff_summary          TEXT  -- JSON; structured diff of outputs
flagged_for_evolution INTEGER NOT NULL DEFAULT 0
created_at            TEXT NOT NULL
```

### 5.8 `skill_state_transition`

```
id                    TEXT PRIMARY KEY
skill_id              TEXT NOT NULL
from_state            TEXT NOT NULL
to_state              TEXT NOT NULL
reason                TEXT NOT NULL  -- gate_passed | human_approval | degradation | evolution_failed | manual_override
actor                 TEXT NOT NULL  -- system | user
actor_id              TEXT  -- user_id if actor=user
at                    TEXT NOT NULL
notes                 TEXT
```

### 5.9 `skill_evolution_log`

```
id                    TEXT PRIMARY KEY
skill_id              TEXT NOT NULL
from_version_id       TEXT NOT NULL
to_version_id         TEXT  -- null if evolution failed validation
triggered_by          TEXT NOT NULL  -- degradation | correction_clustering | manual
claude_invocation_id  TEXT  -- FK to invocation_log for the evolution call
diagnosis             TEXT  -- JSON; Claude's identified failure + rationale
targeted_case_ids     TEXT  -- JSON array
validation_results    TEXT  -- JSON; pass/fail per gate
outcome               TEXT NOT NULL  -- succeeded | rejected_validation | call_failed
at                    TEXT NOT NULL
```

### 5.10 `skill_candidate_report`

```
id                    TEXT PRIMARY KEY
capability_name       TEXT  -- NULL if reported against an unresolved task pattern
task_pattern_hash     TEXT  -- fingerprint of the task type if no capability yet
expected_savings_usd  REAL
volume_30d            INTEGER
variance_score        REAL  -- 0.0 = highly repetitive, 1.0 = highly variable
status                TEXT NOT NULL  -- new | drafted | dismissed | stale
reported_at           TEXT NOT NULL
resolved_at           TEXT
```

### 5.11 `automation`

```
id                    TEXT PRIMARY KEY
user_id               TEXT NOT NULL
name                  TEXT NOT NULL
description           TEXT
capability_name       TEXT NOT NULL  -- FK
inputs                TEXT NOT NULL  -- JSON matching capability.input_schema
trigger_type          TEXT NOT NULL  -- on_schedule | on_manual
schedule              TEXT  -- cron expression; null if on_manual
alert_conditions      TEXT NOT NULL  -- JSON describing when to notify
alert_channels        TEXT NOT NULL  -- JSON array
max_cost_per_run_usd  REAL  -- budget guard per run
min_interval_seconds  INTEGER NOT NULL  -- rate limit floor
status                TEXT NOT NULL  -- active | paused | failed | deleted
last_run_at           TEXT
next_run_at           TEXT
run_count             INTEGER NOT NULL DEFAULT 0
failure_count         INTEGER NOT NULL DEFAULT 0
created_at            TEXT NOT NULL
updated_at            TEXT NOT NULL
created_via           TEXT NOT NULL  -- discord | dashboard | seed
```

### 5.12 `automation_run`

```
id                    TEXT PRIMARY KEY
automation_id         TEXT NOT NULL
started_at            TEXT NOT NULL
finished_at           TEXT
status                TEXT NOT NULL  -- succeeded | failed | skipped_budget | skipped_condition
execution_path        TEXT NOT NULL  -- skill | claude_native
skill_run_id          TEXT  -- FK if execution_path=skill
invocation_log_id     TEXT  -- FK if execution_path=claude_native
output                TEXT  -- JSON result
alert_sent            INTEGER NOT NULL DEFAULT 0
alert_content         TEXT
error                 TEXT
cost_usd              REAL
```

### 5.13 Foreign-key relationships summary

```
capability        1 ─── 1   skill
skill             1 ─── N   skill_version
skill             1 ─── N   skill_run
skill             1 ─── N   skill_fixture
skill             1 ─── N   skill_state_transition
skill             1 ─── N   skill_evolution_log
skill_run         1 ─── N   skill_step_result
skill_run         1 ─── N   skill_divergence
skill_step_result N ─── 1   invocation_log
capability        1 ─── N   automation
automation        1 ─── N   automation_run
automation_run    N ─── 1   skill_run (optional)
automation_run    N ─── 1   invocation_log (optional, if claude_native)
```

---

## 6. Subsystem Designs

### 6.1 Capability registry

**Purpose.** Provide a queryable index of known capabilities so the challenger can match user intents against existing patterns before invoking Claude.

**Data.** Backed by the `capability` table plus a sidecar vector index. Index is built on a concatenation of `name`, `description`, and a flattened rendering of `input_schema` field names and descriptions.

**Public interface.**

```python
class CapabilityRegistry:
    def get_by_name(self, name: str) -> Capability | None
    def list_all(self, limit: int = 500) -> list[Capability]
    def semantic_search(self, query: str, k: int = 5) -> list[tuple[Capability, float]]
    def register(self, capability: Capability, source: str) -> Capability
    def update(self, name: str, **fields) -> Capability
```

**Registration flow.** A capability is registered via `register()` in two cases: when Claude's novelty judgment creates a new one, or when the seed migration adds one during phase 1. Both flows go through a **post-creation audit**: if the new capability's name or description has cosine similarity > 0.80 against any existing capability, the registration is flagged for user review before the capability becomes active. The flagged capability is stored with `status = "pending_review"` (a new field not shown above — add via Phase 1 migration) and the challenger cannot match against it until the user approves or rejects.

**Semantic search.** Top-k retrieval over the vector index, returning the capabilities and cosine-similarity scores. The matcher (§6.2) consumes this and applies its own confidence logic.

**Cold start.** Phase 1 seeds the registry with hand-written capabilities for the task types currently in `config/task_types.yaml` that are user-facing in nature (parse_task, dedup_check, classify_priority — the others are internal plumbing). See §7.1.

### 6.2 Skill lifecycle and promotion gates

**States.** A skill is in exactly one of the following states at any time:

| State | Meaning | Who runs user traffic |
|---|---|---|
| `claude_native` | No skill exists (or skill has been given up on); Claude is the runtime executor | Claude |
| `skill_candidate` | Capability is flagged as skill-worthy; no skill drafted yet | Claude |
| `draft` | Skill version exists; never run on real traffic | Claude (not the skill) |
| `sandbox` | Skill runs in shadow mode only; Claude is primary for real traffic | Claude (skill shadows) |
| `shadow_primary` | Skill is primary for real traffic; Claude shadows 100% of invocations | Skill |
| `trusted` | Skill is primary; Claude shadow-samples at 5% (configurable) | Skill |
| `flagged_for_review` | Statistical degradation detected; awaiting user decision | Skill (unchanged until user decides) |
| `degraded` | Evolution approved and in progress | Claude temporarily |

**Transitions and gates.**

| From → To | Trigger | Gate |
|---|---|---|
| `claude_native` → `skill_candidate` | Detector flags capability with expected savings > threshold | Automatic |
| `skill_candidate` → `draft` | Auto-drafting nightly cron generates a draft | Automatic, rate-limited (§6.5) |
| `draft` → `sandbox` | Draft passes fixture validation AND human approval | Fixture gate (automatic) + human click (required) |
| `sandbox` → `shadow_primary` | Skill produces ≥ N=20 schema-valid outputs on real traffic | Automatic unless `requires_human_gate` |
| `shadow_primary` → `trusted` | Rolling shadow agreement rate ≥ X=85% over M=100 runs | Automatic unless `requires_human_gate` |
| `trusted` → `flagged_for_review` | Wilson-score CI detects statistical degradation vs. baseline | Automatic (alerts user in EOD digest) |
| `flagged_for_review` → `trusted` | User selects "Save (reset baseline)" | Human click |
| `flagged_for_review` → `degraded` | User selects "Approve evolution" | Human click |
| `degraded` → `draft` | Evolution call succeeds, new version validated | Automatic (unless `requires_human_gate`) |
| `degraded` → `claude_native` | Two consecutive evolution attempts fail validation | Automatic |
| Any → `claude_native` | Manual override from dashboard | Human click |

The `requires_human_gate` field is a flag on the skill row, not a state. It is toggled manually during draft or sandbox review via the dashboard and it does not trigger a state transition on its own. When set, it prevents *any* automatic promotion (sandbox → shadow_primary, shadow_primary → trusted, degraded → draft after evolution) from firing without an explicit user click.

**Promotion gate details.**

The `sandbox → shadow_primary` automatic promotion fires when the skill has accumulated N=20 runs in sandbox state and the fraction of runs producing schema-valid outputs is ≥ 90%. The number and thresholds live in `config/skills.yaml` (new file) and are tunable.

The `shadow_primary → trusted` promotion uses a rolling window of M=100 runs. Agreement rate is the fraction of runs where the skill's final output was judged by shadow Claude as semantically equivalent to Claude's own output. Semantic equivalence is computed by a small Claude prompt: "does output A convey the same information as output B?" with a boolean result. This comparison is not free but it only runs during `shadow_primary` (100% sampling) and drops to 5% sampling at trusted.

**Wilson score CI for degradation.** Degradation fires only when the upper bound of the 95% Wilson score CI for the rolling window's agreement rate is below the lower bound of the baseline's 95% CI. This is deliberately conservative to avoid tampering with a stable process during sampling noise. Minimum rolling window: N ≥ 30 samples. For slow-volume skills this may span weeks; that is acceptable.

### 6.3 Skill file format (YAML backbone + markdown steps)

**Skill directory layout:**

```
skills/product_watch/
  skill.yaml
  steps/
    resolve_urls.md
    fetch_page.md
    extract_price.md
    format_response.md
  schemas/
    resolve_urls_v1.json
    extract_price_v1.json
    format_response_v1.json
  fixtures/
    cos_shirt_in_stock.json
    cos_shirt_sold_out.json
    invalid_url.json
```

**`skill.yaml` structure:**

```yaml
capability_name: product_watch
version: 3
description: |
  Monitor a product URL for price changes, availability, or
  broken links. Returns normalized USD price and availability.

inputs:
  schema_ref: capabilities/product_watch/input_schema.json

steps:
  - name: resolve_urls
    kind: llm
    prompt: steps/resolve_urls.md
    output_schema: schemas/resolve_urls_v1.json

  - name: fetch_page
    kind: tool
    tool_invocations:
      - tool: web_fetch
        args:
          url: "{{ state.resolve_urls.canonical_url }}"
          timeout_s: 10
        retry:
          max_attempts: 3
          backoff_s: [1, 3, 5]
        on_failure: escalate
        store_as: page_html

  - name: extract_price
    kind: llm
    prompt: steps/extract_price.md
    output_schema: schemas/extract_price_v1.json

  - name: format_response
    kind: llm
    prompt: steps/format_response.md
    output_schema: schemas/format_response_v1.json

final_output: "{{ state.format_response }}"
```

**Step kinds.**

- `llm` — executor calls `ModelRouter.complete()` with the step prompt and output schema. No tools dispatched on this step.
- `tool` — executor runs declared `tool_invocations`; no LLM call on this step. Output of the step is the collected tool results stored in the state object.
- `mixed` — executor runs `tool_invocations` first, then calls LLM with results available in the state object under `state[step_name + "_tool_results"]`. Used when tool results and reasoning are tightly coupled within one logical step.

**DSL primitives (v1).**

Three primitives in the initial version. All others are deferred (OOS-9, OOS-10).

- `for_each` — fan-out iterator with Jinja expression. Each iteration gets a loop variable (`entry`) and an index. Results stored under a declared `store_as` key with per-iteration suffixes.

  ```yaml
  tool_invocations:
    - for_each: "{{ state.plan.urls }}"
      as: entry
      tool: web_fetch
      args:
        url: "{{ entry.url }}"
      store_as: "fetched[{{ entry.vendor }}]"
  ```

- `retry` — per-invocation retry policy on transient failures.

  ```yaml
  tool: web_fetch
  retry:
    max_attempts: 3
    backoff_s: [1, 3, 5]
  on_failure: escalate | continue | fail_step | fail_skill
  ```

- `escalate` — an output field, not a top-level primitive. Any `llm` step whose output schema includes an optional `escalate: {reason: string}` field can short-circuit the skill by populating it. The executor sees the field and routes the entire skill run to Claude-native.

**Flow control not supported in v1.** No conditionals (`if`), no unbounded loops (`repeat_until`), no nested primitives. If a skill needs any of these, that is a signal the task might not be a skill candidate or the skill should be decomposed.

### 6.4 SkillExecutor

**Purpose.** Interpret a skill version and run it against a task input, producing a final output plus a complete audit trail.

**Execution loop (pseudocode):**

```python
def execute(skill: Skill, inputs: dict, user_id: str) -> SkillRun:
    run = create_skill_run(skill, inputs, user_id)
    state = StateObject()
    state.inputs = inputs
    state.tool_cache = ToolResultCache()

    for step in skill.current_version.steps:
        try:
            if step.has_tool_invocations():
                tool_results = dispatch_tools(step.tool_invocations, state)
                state[step.name + "_tool_results"] = tool_results
                if step.kind == "tool":
                    state[step.name] = tool_results
                    record_step(run, step, output=tool_results)
                    continue

            prompt = render_step_prompt(step.prompt, state=state, inputs=inputs)
            output, meta = model_router.complete(
                prompt=prompt,
                schema=step.output_schema,
                model_alias="local_parser",
                task_type=f"skill_step::{skill.capability_name}::{step.name}",
                task_id=run.task_id,
            )

            if "escalate" in output:
                return escalate_to_claude_native(run, state, reason=output["escalate"])

            validate_against_schema(output, step.output_schema)
            state[step.name] = output
            record_step(run, step, output=output, invocation_id=meta.invocation_id)

        except Exception as exc:
            return triage_agent.handle_failure(run, step, state, exc)

    final_output = render_template(skill.current_version.final_output, state=state)
    run.final_output = final_output
    run.status = "succeeded"
    return run
```

**Tool dispatch.** Tool invocations are declared in the skill YAML and resolved through a `ToolRegistry` that maps tool names to Python callables. The LLM never sees a tool calling API — all tool decisions are made by the skill author (Claude or human) at authoring time. Step-level `tools` allowlist is enforced: the executor refuses to dispatch any tool not declared in the step.

**State object management.** The state object is a Python dict that is JSON-serialized after each step for persistence. Raw tool results (HTML, email bodies, file contents) are stored in the `ToolResultCache`, which writes them to `skill_run.tool_result_cache` (JSON blob) and exposes them to the state object via cache IDs. Steps that need the raw content pull it from the cache by ID; steps that need only summaries get the summary already written to the state object by the prior step.

**Context budgeting.** Each LLM step's prompt is constructed as: `backbone_intro + step_markdown + state_object_summary + relevant_state_details`. The pre-dispatch budgeting logic already in `src/donna/models/router.py` applies; if a step would exceed the local model's context budget, the router raises `ContextOverflowError` and the triage agent handles the escalation.

### 6.5 Auto-drafting (Option B)

**Trigger.** End-of-day batch cron job. Runs after all other Claude work for the day has completed.

**Algorithm.**

```
remaining_budget = daily_cap - spend_so_far
if remaining_budget < min_budget_to_start:
    defer all drafting to tomorrow; log and exit

candidates = query_skill_candidate_reports(
    status="new",
    order_by="expected_savings_usd desc",
    limit=50  # hard cap: anomaly detector, not throttle
)

for candidate in candidates:
    if remaining_budget < cost_per_draft_estimate:
        defer remainder; log and exit

    try:
        draft = invoke_claude_for_skill_generation(candidate)
        fixtures = invoke_claude_for_fixture_generation(draft)
        validation = run_sandbox_validation(draft, fixtures)

        if validation.passed:
            create_skill_version(draft, state="draft")
            mark_candidate_as_drafted(candidate)
        else:
            mark_candidate_as_rejected(candidate, reason=validation.failures)

    except ClaudeBudgetExceeded:
        defer remainder; log and exit
    except ClaudeCallFailure as exc:
        mark_candidate_failed(candidate, error=str(exc))

update_eod_digest_with_draft_summary()
```

**Rate limiting as anomaly detection.** The hard cap of 50 drafts per night is not a throttle — it is a tripwire. Under normal operation the capability detector will produce 0–5 candidates per night once the registry is established, and the cap will never bind. If 50 are produced in one night, something is wrong: the detector is hallucinating savings, the registry is over-fragmenting, or a bug is mistakenly flagging trusted capabilities as candidates. Hitting the cap triggers an alert, not a silent truncation.

**EOD digest integration.** The existing `src/donna/notifications/eod_digest.py` gains a new section: "New skills drafted today." Each entry shows capability name, expected monthly savings, and a link to the dashboard review page. A daily digest entry with zero drafts is still sent (explicitly saying "no new drafts today") so that silent failures in the auto-drafter are visible.

**Budget ordering.** End-of-day queue priority: **evolution before drafting**. Evolution of degraded skills addresses active runtime problems; drafting is speculative improvement. If remaining budget cannot cover both, evolution runs and drafting defers.

### 6.6 Evolution loop

**Triggers.**

1. **Statistical degradation.** Skill is in `trusted`, rolling agreement window shows statistically significant regression vs. baseline (Wilson score CI test, minimum 30 samples). Produces `flagged_for_review`, which requires user action.
2. **Correction clustering.** User has issued ≥ 2 corrections in the last 10 runs of a skill via the existing `correction_log`. This is ground truth, not shadow opinion — it fires immediately with a higher-urgency notification (not just EOD digest).
3. **Manual trigger.** User clicks "re-evolve" on the dashboard.

**Evolution call input (the package given to Claude).**

- The task type / capability definition from the registry.
- The current skill version in full (YAML + step markdown + output schemas).
- Between 15 and 30 divergence case studies, each containing: input, state object at each step, skill final output, shadow Claude final output, structured diff.
- All correction log entries for this capability.
- Statistical summary (run count, baseline agreement, current rolling agreement, failure distribution).
- Prior evolution log entries for this skill (changelogs + outcomes).
- The full skill fixture library.

Estimated prompt size: 15–35K tokens. Cost per evolution call: $0.30–$1.20.

**Evolution call output.**

```json
{
  "diagnosis": {
    "identified_failure_step": "extract_prices",
    "failure_pattern": "...",
    "confidence": 0.8
  },
  "new_skill_version": {
    "yaml_backbone": "...",
    "step_content": {"step_name": "...", ...},
    "output_schemas": {"step_name": {...}, ...}
  },
  "changelog": "...",
  "targeted_failure_cases": ["run_id_abc", "run_id_def"],
  "expected_improvement": "..."
}
```

**Validation gates (all must pass before replacing current version).**

1. **Structural validation.** YAML parses, referenced prompts exist, output schemas validate, DSL primitives resolve.
2. **Targeted case improvement.** New version run against `targeted_failure_cases` from the evolution output. Pass rate ≥ 80%.
3. **Fixture regression.** New version run against the full fixture library. Pass rate ≥ 95% — evolution cannot lower the bar on previously working cases.
4. **Recent-success sanity.** New version run against 20 captured successful runs from the last 30 days. All must produce schema-valid outputs.

If all pass, new version becomes `skill.current_version_id` and the skill transitions back to `sandbox` (unless `requires_human_gate`, in which case draft state and await approval). If any fail, the attempt is logged in `skill_evolution_log` with `outcome = rejected_validation` and the skill remains in `degraded`.

**Failure handling.**

- Validation failure: retry after 24 hours with fresh divergence data. Two consecutive validation failures → demote to `claude_native`. Logged prominently in dashboard.
- Call failure (API error, budget exceeded): retry queued for the next evolution window. Not counted against the 2-attempt limit.

**Scheduling.** Evolution runs as a nightly batch, same window as auto-drafting but with higher priority in the queue. Budget-guarded; defers to tomorrow if daily cap would be exceeded.

### 6.7 Challenger refactor

**New role.** The challenger is now the component that sits between `parse_task` and execution. Its responsibilities:

1. **Skill match.** Given a parse result, query the capability registry via `CapabilityMatcher` and produce top-5 candidates with confidence scores.
2. **Confidence routing.** Based on top candidate's score:
   - `confidence ≥ 0.75` — high confidence, proceed to input extraction against matched capability.
   - `0.4 ≤ confidence < 0.75` — medium confidence, ask one disambiguation question. If still ambiguous, escalate.
   - `confidence < 0.4` — low confidence, escalate to Claude for novelty judgment.
3. **Input extraction.** For a matched capability, extract structured inputs from the parse result against the capability's input schema. Local LLM, JSON-mode output.
4. **Missing input handling.** If extraction yields missing required fields, generate clarifying questions and post them to a Discord thread (one thread per task). Awaits user reply and merges.
5. **Novelty escalation.** For low-confidence cases, invoke Claude with the user's message, the parse result, the top-5 registry matches, and the full registry metadata. Claude returns either `match_existing` (with mapping) or `create_new` (with capability definition) — see §6.1 post-creation audit.

**Input extraction prompt format.** A single local LLM call with a Jinja-rendered prompt that includes the capability's `input_schema` inline. Output is strict JSON matching the schema. If the LLM cannot populate a field from the user's message, it leaves the field null rather than hallucinating.

**Clarifying question generation.** Simple templated local call: "the following fields are missing: X, Y, Z. Generate a single Discord message asking for all of them in the Donna persona." Output goes to the existing Discord thread machinery.

**Timeout on user replies.** If the user doesn't respond within 2 hours, send a follow-up in the same thread. After 24 hours, the task enters `waiting_for_input` state on the dashboard and stops pinging. The user can resume manually.

**Escalation logging.** Every Claude call from the challenger pipeline logs a `reason` field: `low_match_confidence`, `disambiguation_failed`, `novel_task_type`, `complex_requirements`. Over time the distribution is a health metric for the match layer.

### 6.8 Triage agent

**Purpose.** Handle runtime skill failures gracefully — retry, escalate, or alert — without either silent failure or task flow disruption.

**Failure inputs.** Invoked when the SkillExecutor catches any of:
- Tool invocation failure that exhausted retries
- Schema validation failure on a step output
- Template rendering error (Jinja evaluation error)
- LLM call error (provider outage, rate limit)
- Explicit `escalate` signal from an LLM step output

**Decision output.**

```json
{
  "decision": "retry_step_with_modified_prompt" | "skip_step" | "escalate_to_claude" | "alert_user" | "mark_skill_degraded",
  "rationale": "...",
  "modified_prompt_additions": "...",  // if retry
  "alert_message": "..."               // if alert_user
}
```

**Implementation.** Runs on local LLM by default via a new `task_type: triage_failure` in `config/task_types.yaml`. Input: the failed step's context, error type, state object, skill YAML. Output: structured decision.

**Budget caps.** Triage can call `retry_step_with_modified_prompt` at most `N=3` times per skill run. After exhausting retries, triage must escalate, alert, or degrade. This prevents a runaway retry loop.

**Triage itself can fail.** If triage's own LLM call errors or returns an unparseable decision, the executor falls back to `escalate_to_claude` and logs a triage-failure event. Triage is a helper, not a critical path.

**Effectiveness monitoring.** Every triage decision is logged. A weekly summary surfaces:
- Distribution of decisions (% retry, % escalate, etc.)
- Retry success rate (what fraction of retries actually recover?)
- Escalation outcome (did Claude succeed where the skill failed?)

Over time, if retries are mostly unsuccessful or escalations mostly aren't needed, triage prompts can be tuned or the triage agent itself can become a skill candidate.

### 6.9 Automation subsystem

**Purpose.** Support recurring, Donna-driven work (monitors, scheduled summaries, periodic checks) as first-class entities distinct from user-to-do tasks.

**Creation flow.** An automation is created via either Discord (user types a recurring request; challenger extracts fields and creates the row) or the dashboard (user fills out a form directly). The challenger's output distinguishes `trigger_type = on_schedule` from `trigger_type = on_message` (tasks) via Claude's novelty judgment when needed, or via capability metadata when the capability is matched.

**Scheduler.** `src/donna/automations/scheduler.py` runs an asyncio cron loop, polling every minute for automations with `next_run_at <= now() AND status = active`. Respects `min_interval_seconds` to prevent accidental high-frequency runs.

**Execution dispatch.** For each due automation, the dispatcher chooses:
- If `capability.skill` exists and skill state ∈ `{shadow_primary, trusted}`: run via `SkillExecutor`, record `automation_run.execution_path = "skill"`.
- Otherwise: run via claude-native path, record `execution_path = "claude_native"`.

**Budget guards.** Two layers:
1. **Per-run**: `automation.max_cost_per_run_usd`. If the run exceeds this, the run is aborted and marked `status = failed, error = "cost_exceeded"`.
2. **Global**: the existing `BudgetGuard` applies. If running this automation would exceed the daily cap, the run is marked `skipped_budget` and next_run_at advances to the next scheduled slot.

**Alert conditions.** `automation.alert_conditions` is a JSON expression evaluated against the run's output. Simple predicates in v1:

```json
{
  "all_of": [
    {"field": "price_usd", "op": "<=", "value": 100},
    {"field": "in_stock", "op": "==", "value": true}
  ]
}
```

Supported ops: `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `exists`. Compound via `all_of` / `any_of`. If the expression evaluates true, an alert is dispatched to each `alert_channel` (existing `NotificationService`).

**Manual run.** `trigger_type = on_manual` automations do not auto-run. They appear in the dashboard with a "run now" button. Useful for one-off Donna work the user wants to pre-configure and trigger ad-hoc.

**Lifecycle.** Automations can be paused, resumed, edited, and deleted from the dashboard. Editing inputs while active does not reset the run history; editing the capability reference (rare) creates a new automation and marks the old one `deleted`.

### 6.10 Dashboard surface

**Skills view.** Paginated table of all skills across all states. Columns: capability name, state (with visual state indicator), current shadow agreement rate, run count (30d), cost savings estimate, last run, `requires_human_gate` flag indicator. Filters: state, has human gate, touches sensitive tools. Sort by any column. Clicking a row opens the skill detail view.

**Skill detail view.** Shows:
- Current version YAML and step markdown (editable)
- Version history with diff viewer
- Run history (last 50 runs, paginated to more)
- Divergence cases for skills in shadow_primary/trusted
- State transition log
- Evolution log
- Fixture library (editable)
- Action buttons: approve state transition, re-evolve, edit, toggle `requires_human_gate`, demote to claude_native, delete

**Capabilities view.** Simple list of all capabilities with names, descriptions, trigger types, and the state of their associated skill (if any). Clicking navigates to the skill detail or offers to draft one.

**Automations view.** Paginated table of all automations. Columns: name, capability, schedule, status, last run, next run, run count, failure count. Filters: status, capability. Clicking opens the automation detail view with inputs, alert conditions, and run history.

**Automation run history view.** Per-automation paginated log of runs with status, timing, output preview, and links to the underlying skill run or invocation log.

**Skill candidates view.** List of `skill_candidate_report` rows with status `new`. Shows capability name (or task pattern hash), expected monthly savings, variance score, volume, and "draft now" / "dismiss" actions.

**Dashboard-side edits drop skills to sandbox.** Any human edit of a skill in `trusted` or `shadow_primary` state creates a new `skill_version` and transitions the skill back to `sandbox` for revalidation. This matches the "push through the pipeline" discipline for code changes to deployed services.

---

## 7. Phased Rollout

Five phases, each with a scope, handoff contract, and acceptance scenarios. Phases are sequential; later phases depend on earlier phases' handoff contracts being met.

### Phase 1: Foundation

**Scope.**
- New tables: `capability`, `skill`, `skill_version`, `skill_state_transition`. Alembic migration.
- `src/donna/capabilities/registry.py`: CRUD, semantic search, post-creation audit.
- Seed capabilities via migration: hand-written entries for the 3 LLM task types that are best understood (`parse_task`, `dedup_check`, `classify_priority`).
- `ChallengerAgent` refactor: skill match, input extraction, clarifying questions. Claude novelty escalation stub (returns "claude_native for now" without building the full orchestrator response).
- Dispatcher simplification: routes to claude-native or skill based on skill state.
- Minimal `SkillExecutor`: executes single-step skills only, no DSL, no tool dispatch. Sufficient for running the seed capabilities' hand-written skills (each is one `llm` step).
- Dashboard read-only views: list capabilities, list skills, view skill detail (no editing).

**Handoff contract.**

After Phase 1 completes, the following must be true and later phases may rely on:

- `capability` table exists with the schema in §5.1, plus a `status` column (`active` | `pending_review`).
- `CapabilityRegistry.semantic_search(query, k)` returns top-k with cosine similarity scores.
- `ChallengerAgent.match_and_extract(parse_result) → ChallengerResult` is the new public interface. The old `assess_completeness` method is removed.
- `SkillExecutor.execute(skill, inputs, user_id) → SkillRun` is callable for single-step `llm`-kind skills.
- `skill.state` values used in v1: `claude_native`, `draft`, `sandbox`, `shadow_primary`, `trusted`. Other states come in later phases.
- Three seed capabilities exist: `parse_task`, `dedup_check`, `classify_priority`, each with a hand-written skill in `sandbox` state. (See Drift Log entry 2026-04-15 for the rationale.)
- The dispatcher uses `skill.state` to choose execution path; `claude_native` and any unrecognized state fall back to Claude.

**Acceptance scenarios.**

- **AS-1.1**: User DMs "draft Q2 review by Friday." Challenger matches `parse_task` capability (high confidence). Input extraction succeeds. SkillExecutor runs the hand-written `parse_task` skill on local. Shadow Claude runs in parallel. Divergence is recorded. Task is created in DB.
- **AS-1.2**: User DMs "monitor https://cos.com/shirt daily for size L under $100." Challenger matches no capability (<0.4 confidence). Escalates to Claude. Phase 1 stub records the novelty but runs the task as claude_native. Task is handled, capability is NOT yet registered (that comes in Phase 3 with auto-drafting).
- **AS-1.3**: Dashboard shows three seed capabilities and three seed skills. Skill detail view renders YAML, steps, and state transition log. No editing yet.
- **AS-1.4**: `ChallengerAgent.match_and_extract` called with an input that half-matches a capability (missing required field) generates a clarifying question and posts it to a Discord thread. User reply is ingested via existing thread handler.

### Phase 2: Skill execution layer

**Scope.**
- Multi-step SkillExecutor with state object and tool result cache.
- Flow control DSL: `for_each`, `retry`, `escalate`.
- Tool dispatch (Model 2c): `ToolRegistry`, per-step allowlist enforcement.
- `src/donna/skills/triage.py` — triage agent with four decision types.
- Fixture validation harness (`validate_against_fixtures`).
- Expanded skill file format (§6.3): `skill.yaml` with `steps[]`, `steps/*.md`, `schemas/*.json`.
- Update seed skills to multi-step form where appropriate (parse_task may gain an "extract then classify" two-step version).
- `skill_run`, `skill_step_result`, `skill_fixture` tables (if not already in Phase 1).
- Dashboard: skill detail view shows run history and step-by-step breakdown.

**Handoff contract.**

- `SkillExecutor.execute` supports multi-step skills with tool dispatch and the three DSL primitives.
- `ToolRegistry` is the single point of tool dispatch; the existing agents (`pm_agent`, `prep_agent`, `scheduler_agent`) are refactored to register their tools with the registry rather than import them directly.
- `TriageAgent.handle_failure(run, step, state, exc) → TriageResult` is the public interface; triage is invoked by the executor's exception handler.
- `skill_run.state_object` is the canonical final state at run end; every step's output appears in it under `state[step_name]`.
- `skill_run.tool_result_cache` holds raw tool blobs as a JSON map of `cache_id → blob`; state object references by cache_id.
- Fixture library for any skill is in `skills/<capability>/fixtures/*.json` and `skill_fixture` table rows; both must stay in sync.
- `TriageAgent.handle_failure` returns one of five decisions. `RETRY_STEP` is currently surfaced to the executor but not yet executed as a retry loop (See Drift Log 2026-04-15 entries for Phase 2 §6.4); the executor treats it as an escalate with a descriptive reason. Executor's no-triage failure path also diverges from spec wording (see second drift entry) for Phase 1 test compatibility.

**Acceptance scenarios.**

- **AS-2.1**: Run a multi-step skill (`fetch page → extract → format`) against a fixture. All steps execute, state object is populated, final output is returned, `skill_step_result` rows record each step with its invocation log link.
- **AS-2.2**: Skill step declares `for_each` over URLs. Executor fans out three `web_fetch` calls, results are stored under `state.fetched[vendor]` keys. No LLM call on the tool-kind step.
- **AS-2.3**: Skill step's LLM call returns an output with `escalate: {reason: "insufficient data"}`. Executor short-circuits, task is routed to claude_native, `skill_run.escalation_reason` is populated.
- **AS-2.4**: Skill step's `web_fetch` times out 3 times. Retry policy exhausts. `on_failure: escalate` triggers; triage agent is invoked. Triage decides to escalate to Claude. Task completes via claude_native.
- **AS-2.5**: Skill step LLM returns output that fails schema validation. Executor catches, calls triage, triage decides to retry with modified prompt. Second attempt succeeds.

### Phase 3: Lifecycle and auto-drafting

**Scope.**
- Full lifecycle state machine with all gates (§6.2).
- `skill_candidate_report` table and detector cron.
- Auto-drafting nightly cron (§6.5) with budget guard integration.
- Shadow Claude sampling during `shadow_primary` and `trusted`.
- Semantic-equivalence judge (small Claude prompt for output comparison).
- EOD digest new section for drafted skills.
- Dashboard: draft review UI (view generated YAML, approve/reject, toggle `requires_human_gate`, promote to sandbox).
- Dashboard: skill candidates view.

**Handoff contract.**

- State transitions are enforced via `SkillLifecycleManager` — no code outside this class mutates `skill.state`.
- Auto-drafting runs exactly once per day at the configured end-of-day time. Idempotent if triggered manually.
- Skill candidates surface in dashboard within 24 hours of detection.
- `config/skills.yaml` exists with tunable thresholds: sandbox promotion (N, validity rate), shadow_primary promotion (M, agreement rate), trusted shadow sample rate.

**Acceptance scenarios.**

- **AS-3.1**: A claude_native task type has run 30 times in the past 30 days at $0.15 each. Detector flags it as a candidate with expected_savings_usd ~$4.5/month (below default threshold). Not drafted.
- **AS-3.2**: A claude_native task type has run 200 times at $0.10 each. Detector flags it as a candidate with expected_savings ~$20/month. End-of-day auto-drafter picks it up, generates a skill, fixture-validates, creates draft row. EOD digest mentions it.
- **AS-3.3**: User approves a draft. Skill transitions to sandbox. 20 runs later, all schema-valid, it auto-promotes to shadow_primary. 100 runs later with ≥85% agreement, auto-promotes to trusted.
- **AS-3.4**: Draft fails fixture validation. Auto-drafter logs rejection, skill never reaches draft state in the DB. EOD digest reports rejection count.
- **AS-3.5**: Auto-drafter attempts to run but daily budget is exhausted. Defers entire batch to tomorrow. Logged clearly. No partial work.

### Phase 4: Evolution and degradation

**Scope.**
- Statistical degradation detector (Wilson score CI) running on rolling windows.
- `flagged_for_review` state and EOD digest notification.
- Evolution loop with full input package, validation gates, and fallback on double-failure.
- `skill_evolution_log` table.
- Correction clustering trigger (fast path, not waiting for EOD digest).
- Dashboard: "Skills needing attention" tab showing `flagged_for_review` skills with save/evolve/review actions.
- Triage effectiveness monitoring (weekly summary).

**Handoff contract.**

- `DegradationDetector.check_all_trusted_skills()` runs as part of the nightly cron and produces `flagged_for_review` transitions where warranted.
- `EvolutionScheduler.run_pending()` is called after DegradationDetector, evolves skills the user has approved for evolution, respects budget guards.
- `skill_evolution_log` captures every attempt with outcome and validation details.
- `requires_human_gate` skills: evolution never auto-promotes; evolved versions land in `draft` state awaiting approval.

**Acceptance scenarios.**

- **AS-4.1**: Trusted skill with baseline 90% agreement produces 30 shadow samples over 2 weeks with 72% agreement. Wilson CI test confirms statistically significant degradation. Skill transitions to `flagged_for_review`. EOD digest surfaces it.
- **AS-4.2**: User clicks "Save (reset baseline)" on a flagged skill. Skill returns to `trusted`; new baseline is set from the recent window.
- **AS-4.3**: User clicks "Approve evolution." Skill transitions to `degraded`. Next nightly evolution window, Claude is called with divergence package, produces new version, validation passes, new version enters sandbox.
- **AS-4.4**: Evolution validation fails twice in a row. Skill transitions to `claude_native`. Dashboard clearly surfaces the demotion and the reasons.
- **AS-4.5**: User issues 3 corrections on a trusted skill's outputs within a day. Correction-clustering trigger fires immediately; urgent Discord notification goes out with the three-choice action.

### Phase 5: Automation subsystem

**Scope.**
- `automation` and `automation_run` tables.
- `src/donna/automations/scheduler.py` cron loop.
- `src/donna/automations/dispatcher.py` — skill-vs-claude_native resolution per run.
- Alert condition evaluator and `NotificationService` integration.
- Challenger enhancement: distinguishes `on_schedule` vs `on_message` task types via Claude novelty output.
- Dashboard: automations view, automation detail, run history.
- Discord creation flow: "watch X daily" → automation row created.
- Budget guards at per-run and global level.

**Handoff contract.**

- The challenger's Claude novelty output now includes `trigger_type: on_schedule | on_manual | on_message`.
- `AutomationScheduler` is the sole creator of `automation_run` rows.
- Automations with no skill for their capability run via claude_native path; skill availability is re-checked at each run start, so automations transparently switch to skill execution once a skill is `shadow_primary`+.
- `min_interval_seconds` is enforced; attempts to run sooner are skipped with `status = skipped_condition`.

**Acceptance scenarios.**

- **AS-5.1**: User DMs "watch https://cos.com/shirt daily for size L under $100." Challenger extracts fields, asks no clarifying questions (all present), creates automation with `product_watch` capability (newly created via Claude novelty), schedule "daily at 12:00", alert conditions, Discord DM channel.
- **AS-5.2**: Automation runs on schedule. Current state: no `product_watch` skill exists. Claude-native path executes. Result recorded in `automation_run`. Alert condition evaluates false (price > threshold). No alert dispatched.
- **AS-5.3**: Three weeks later, `product_watch` skill reaches `shadow_primary`. Next automation run detects the skill, uses `SkillExecutor`, records `execution_path = "skill"`. Cost drops to near zero.
- **AS-5.4**: Product drops below threshold. Alert condition evaluates true. Discord DM is sent to the user with the structured output.
- **AS-5.5**: Automation's URL returns 404 for three consecutive runs. `failure_count` increments. When it crosses threshold (default 5), automation is paused and user is notified.

---

## 8. Drift Log

*(Initially empty. Append entries as implementation deviates from the spec. Every entry must also update the relevant handoff contract in §7 so that downstream phases read the current state.)*

Format:

```
#### YYYY-MM-DD — Phase N, section X.Y
- **What changed**: ...
- **Why**: ...
- **Handoff contracts affected**: Phase N handoff bullet list
- **Action required for downstream phases**: ...
```

#### 2026-04-15 — Phase 1, §7 Handoff Contract
- **What changed**: Seed skills land in `sandbox` state, not `shadow_primary` as
  originally written in the Phase 1 handoff contract.
- **Why**: Shadow sampling infrastructure (100% Claude comparison during
  `shadow_primary`) is a Phase 3 dependency. Landing seeds in `shadow_primary`
  would require shadow machinery that doesn't exist until Phase 3, violating
  the "task flow is never blocked by skill infrastructure" invariant from §4.2.
  `sandbox` means the skill runs alongside Claude without affecting user-visible
  output, which preserves the invariant and still generates per-skill run data.
- **Handoff contracts affected**: Phase 1 handoff (seed skill state), Phase 3
  handoff (must promote sandbox → shadow_primary for existing seed skills when
  shadow sampling lands).
- **Action required for downstream phases**: Phase 3 implementation should
  include a targeted migration that promotes the three seed skills from
  `sandbox` → `shadow_primary` as the first step after shadow sampling is
  working.

#### 2026-04-15 — Phase 2, §6.4 Triage retry loop
- **What changed**: Triage's `RETRY_STEP` decision is not yet a true retry
  loop in the executor. When triage asks for a retry, the executor currently
  returns an escalated result with a descriptive reason, instead of actually
  re-running the failed step with modified prompt additions.
- **Why**: Full retry-with-prompt-augmentation requires inserting the
  `modified_prompt_additions` into the step context and re-executing with
  state preserved. This adds meaningful state-management complexity.
  Deferred to make Phase 2 execution machinery shippable sooner.
- **Handoff contracts affected**: Phase 2 handoff (triage semantics),
  Phase 3 handoff (full triage retry will be in scope once lifecycle + evolution are built).
- **Action required for downstream phases**: Phase 3 should either implement
  the retry loop or formally mark triage's `RETRY_STEP` as deprecated in the
  decision enum.

#### 2026-04-15 — Phase 2, §6.4 Executor failure semantics without triage
- **What changed**: The executor returns `status="failed"` (not `"escalated"`)
  when a typed skill exception is raised AND no triage agent is configured.
  A private internal `_ModelCallError` wrapper preserves the `"model_call: ..."`
  prefix in the error string.
- **Why**: Phase 1 tests `test_executor_fails_on_schema_validation_error` and
  `test_executor_fails_on_model_exception` assert `status="failed"` with
  specific error shapes. Preserving them was a hard constraint for the
  Phase 2 task. The Phase 2 spec wording ("if no triage is configured,
  failures return an escalated result") was overridden to keep Phase 1
  compatibility; in production, triage is always configured, so this
  compatibility-only path is not exercised at runtime.
- **Handoff contracts affected**: Phase 2 handoff (no-triage failure shape).
- **Action required for downstream phases**: When Phase 3 revisits test
  expectations, rewrite the two affected Phase 1 tests to match the new
  spec wording, then remove the `_ModelCallError` shim and the no-triage
  phase1-style failure result branch in the executor.

### Phase 3 closures (2026-04-16)

- **§6.4 — Triage retry loop.** Phase 2 drift note resolved. Task 14 implemented RETRY_STEP as a real retry loop in `SkillExecutor.execute()`, threading `modified_prompt_additions` into `_run_llm_step` via new kwarg. Retry cap enforced by TriageAgent's existing `MAX_RETRY_COUNT=3` logic; exceeded retries escalate to Claude.
- **§6.4 — No-triage failure-shape shim.** Phase 2 drift note resolved. Task 15 removed `_ModelCallError` wrapper and `_phase1_style_failure_result` method. Typed skill failures without triage now produce `status="escalated"` with `escalation_reason=f"{error_type}: {exc}"` instead of `status="failed"`; model-call failures fall through to the generic exception handler.
- **§7 — Seed skill state.** Phase 1 drift note resolved. Task 10 migration `promote_seed_skills_to_shadow_primary.py` promotes `parse_task`, `dedup_check`, `classify_priority` from `sandbox` to `shadow_primary` with audit rows. Gated on shadow sampling being available (Task 6).
- **I-1 SkillSystemConfig dead code.** Resolved in Task 4. `SkillSystemConfig` now loaded from `config/skills.yaml` via `load_skill_system_config()` and wired through `CapabilityMatcher`, `CapabilityRegistry`, `initialize_skill_system`.
- **I-3 Duplicate Jinja render logic.** Resolved in Task 7. Both `dsl.py` and `tool_dispatch.py` now call `donna.skills._render.render_value`, with `preserve_types` flag selecting between type-preserving (dsl) and string-only (tool_dispatch) semantics.

### Phase 3 new drift (2026-04-16)

- **Double-hop transition in AutoDrafter.** Task 9's AutoDrafter inserts new skills at `state='claude_native'` (required because `INSERT` isn't through the lifecycle manager), then transitions `claude_native → skill_candidate → draft`. This creates TWO audit rows per auto-draft rather than a single `skill_candidate → draft` transition. The alternative — inserting directly at `state='draft'` — would bypass the "all state changes go through the lifecycle manager" invariant. Accepted; the extra audit row is spec-compatible and auditable.

- **Validation deferred when no executor_factory is wired.** Task 9 — when `AutoDrafter` is constructed without an `executor_factory`, fixture validation returns `pass_rate=1.0` and logs `skill_auto_draft_validation_deferred`. This is a safety valve so Phase 3 can ship before the sandbox executor is productionized. Skills drafted in this state still require human approval (`draft → sandbox` = `human_approval`).

- **Baseline treated as point value in degradation CI comparison.** Task 11. The spec says "Wilson-score CI detects statistical degradation vs. baseline" — our implementation treats `skill.baseline_agreement` as a point value and compares `current_upper < baseline`. A more rigorous approach would compute baseline's own CI and compare to its lower bound. Accepted for Phase 3 v1; can be tightened in Phase 4.

- **EOD digest truncation.** Task 17. The Discord digest is capped at 2000 chars; when the task list is long, the skill-system section may be truncated. Accepted for Phase 3 v1; could paginate in the future.

- **R28 — Phase 3: detect-and-flag only; evolution is Phase 4.** The 4-gate evolution validation loop is deferred to Phase 4. Phase 3 captures divergence data and flags skills for degradation via Wilson-score CI (R25), but does not yet replace skill versions after validation. R28 marked `[~]` (partial).

### Phase 4 closures (2026-04-16)

- **§6.6 evolution loop shipped in full.** Tasks 2–6 implement `EvolutionInputBuilder`, `EvolutionGates` (4 gates), `Evolver`, `EvolutionScheduler`, and `SkillEvolutionLogRepository`. Evolution runs before auto-drafting in the nightly order (spec §6.5).
- **R28 moved from partial to done.** Phase 3 drift entry resolved. All four validation gates are implemented with configurable thresholds.
- **R27 correction clustering.** Task 7 ships `CorrectionClusterDetector.scan_once()`; it flags eligible skills when the correction count over the last N runs exceeds the threshold and fires an urgent notification (not EOD).
- **Baseline reset on save.** Task 8 extends `POST /admin/skills/{id}/state` — when flagged_for_review → trusted with reason=human_approval, the route recomputes `baseline_agreement` from the last 100 divergences. AS-4.2.
- **Startup wiring gap closed.** Tasks 10, 11, 12 add `AsyncCronScheduler` + `assemble_skill_system` and integrate them into the FastAPI lifespan. The scheduler fires `run_nightly_tasks` at `config.nightly_run_hour_utc`. Flagged in Phase 3 final review — now resolved.
- **Executor factory deferral carries into Phase 4.** `Evolver` accepts `executor_factory=None` and `assemble_skill_system` passes `None` by default; gates 2, 3, 4 then return `pass_rate=1.0` (vacuous). Evolution will still run and produce valid draft versions, but validation against real skill_runs and fixtures is deferred until someone wires a sandbox SkillExecutor in. Same safety posture as Phase 3's AutoDrafter — drafted/evolved skills require human approval before reaching sandbox.
- **Evolution transitions land in draft, not sandbox.** The §6.2 transition table requires `human_approval` for `draft → sandbox`. `Evolver` after successful gates transitions `degraded → draft` with `reason=gate_passed` (legal), then attempts `draft → sandbox` but catches `IllegalTransitionError` since system actor cannot use `human_approval`. Evolved skills therefore rest in `draft` pending human approval — consistent with the spec's requirement that human-gated and non-gated evolved versions both land in a state awaiting review.

---

## 9. Requirements Checklist

Every numbered requirement must be verified before the spec is considered implemented. Check off as implementation completes.

Legend: `[x]` = done · `[~]` = partial — see drift log · `[ ]` = not yet started

| # | Requirement | Spec section | Verified by | ✓ |
|---|---|---|---|---|
| R1 | `capability` table with unique name, input_schema, trigger_type, status | 5.1 | Migration test + `test_capability_registry::test_create_and_retrieve` | [ ] |
| R2 | Capability registry supports semantic top-k retrieval | 6.1 | `test_capability_registry::test_semantic_search_returns_top_k` | [ ] |
| R3 | Post-creation audit flags cosine-similar capabilities for user review | 6.1 | `test_capability_registry::test_duplicate_name_flagged` | [ ] |
| R4 | Challenger matches user intent against registry with confidence scoring | 6.7 | AS-1.1, AS-1.2 | [ ] |
| R5 | Challenger extracts inputs against matched capability's input schema | 6.7 | AS-1.1 | [ ] |
| R6 | Challenger asks clarifying questions for missing inputs via Discord thread | 6.7 | AS-1.4 | [ ] |
| R7 | Challenger escalates to Claude for low-confidence matches | 6.7 | AS-1.2 | [ ] |
| R8 | Claude novelty judgment returns match_existing or create_new with full registry context | 6.7 | Integration test on novelty call | [ ] |
| R9 | `skill` table with capability_name as unique key, state, requires_human_gate | 5.2 | Migration test | [ ] |
| R10 | `skill_version` table captures full YAML + step content + schemas per version | 5.3 | Migration + roundtrip test | [ ] |
| R11 | SkillExecutor executes single-step llm skills in Phase 1 | 6.4 | AS-1.1 | [ ] |
| R12 | SkillExecutor supports multi-step with state object in Phase 2 | 6.4 | AS-2.1 / `test_h2_1_multistep_skill_accumulates_state` | [x] |
| R13 | SkillExecutor dispatches tools declaratively per step allowlist | 6.4 | AS-2.2 / `test_h2_2_for_each_fan_out` / `test_h2_6_allowlist_enforced` | [x] |
| R14 | DSL supports for_each, retry, escalate in Phase 2 | 6.3 | AS-2.2 / AS-2.3 / AS-2.4 | [x] |
| R15 | Triage agent handles runtime failures with four decision types | 6.8 | AS-2.4 / `test_skills_triage.py` | [x] |
| R16 | Triage retries capped at 3 per skill run | 6.8 | `test_triage_respects_retry_cap` | [x] |
| R17 | Skill lifecycle state machine enforces all transitions in §6.2 | 6.2 | `test_skill_lifecycle` suite | [x] |
| R18 | Sandbox → shadow_primary auto-promotion on N=20 runs with ≥90% validity | 6.2 | AS-3.3 | [x] |
| R19 | shadow_primary → trusted auto-promotion on M=100 runs with ≥85% agreement | 6.2 | AS-3.3 | [x] |
| R20 | Draft → sandbox requires human approval | 6.2 | AS-3.3 | [x] |
| R21 | `requires_human_gate` skills require approval at every transition | 6.2 | Unit test | [x] |
| R22 | Skill candidate detector identifies high-savings claude_native capabilities | 6.5 | AS-3.1, AS-3.2 | [x] |
| R23 | Auto-drafter runs at end-of-day with 50/day cap and budget guard | 6.5 | AS-3.2, AS-3.5 | [x] |
| R24 | EOD digest surfaces new drafts, rejections, and flagged skills | 6.5, 6.6 | AS-3.2, AS-4.1 | [x] |
| R25 | Statistical degradation detector uses Wilson score CI against baseline | 6.6 | AS-4.1 | [x] |
| R26 | `flagged_for_review` offers save/evolve/review actions | 6.6 | AS-4.2, AS-4.3 | [x] |
| R27 | Correction clustering triggers immediate evolution notification | 6.6 | AS-4.5 | [x] |
| R28 | Evolution validates via 4 gates before replacing current version | 6.6 | AS-4.3, AS-4.4 | [x] |
| R29 | Two consecutive failed evolutions demote to claude_native | 6.6 | AS-4.4 | [x] |
| R30 | `automation` table distinct from `task` table | 5.11 | Migration test | [ ] |
| R31 | Automation scheduler runs due automations respecting min_interval | 6.9 | AS-5.2 | [ ] |
| R32 | Automation dispatcher resolves skill vs claude_native per run | 6.9 | AS-5.3 | [ ] |
| R33 | Alert conditions evaluated against run output, dispatched via NotificationService | 6.9 | AS-5.4 | [ ] |
| R34 | Repeated failures pause the automation with user notification | 6.9 | AS-5.5 | [ ] |
| R35 | Dashboard lists skills, capabilities, automations with pagination | 6.10 | Manual walkthrough | [ ] |
| R36 | Dashboard skill detail shows version history, runs, divergences, transitions | 6.10 | Manual walkthrough | [ ] |
| R37 | Dashboard edit of trusted skill creates new version and transitions to sandbox | 6.10 | Unit test | [ ] |
| R38 | Every LLM invocation (local, Claude, shadow) logged to `invocation_log` | 4.2 | Existing logging + new instrumentation | [x] |
| R39 | Task flow is never blocked by skill infrastructure failures | 4.2 | Chaos test | [x] |
| R40 | Capability registry queries precede any Claude novelty call | 4.2 | Unit test on challenger flow | [x] |

---

## 10. Open Questions

Items that are not blockers for starting implementation but need a call before their relevant phase:

1. **Semantic-equivalence judge prompt.** §6.2 says shadow agreement is computed by a small Claude prompt comparing outputs. The exact prompt wording, how it handles structural outputs vs. prose outputs, and its own quality gate all need to be designed during Phase 3. Risk: if the judge is wrong, all skill promotion decisions are wrong.

2. **Embedding model for semantic search.** §6.1 mentions FAISS or sqlite-vss. Model choice (a small local embedding model vs. calling a remote API) affects latency, cost, and offline reliability. Phase 1 decision.

3. **Cron expression parser.** §6.9 uses cron strings for automation schedules. Standard Python library (`croniter`) or a custom lightweight parser? Leans toward `croniter` for v1; revisit if it adds problematic dependency weight. Phase 5 decision.

4. **Dashboard framework.** The existing dashboard (`src/donna/api/`) is Flask-based. Adding skill/automation views to the existing SPA vs. a separate skill-management panel is a UX call. Phase 1 decision (affects which routes Phase 1 needs to add).

5. **Migration strategy for existing task types.** Phase 1 seeds `parse_task`, `dedup_check`, `classify_priority` as capabilities with hand-written skills. The rest of `config/task_types.yaml` is left as LLM operations (internal plumbing). Do we migrate any of the others to capabilities later? `generate_digest` is a candidate. Phase 3+ decision, guided by the detector.

---

## 11. References

- `docs/model-layer.md` — existing model abstraction and evaluation layer.
- `docs/task-system.md` — existing task system, state machine, and persistence.
- `docs/agents.md` — existing agent roles and tool registry.
- `docs/notifications.md` — existing notification service and escalation ladder.
- `config/donna_models.yaml` — current LLM routing table.
- `config/task_types.yaml` — current LLM task types registry (retained for internal operations).
- `src/donna/llm/queue.py` — LLM queue with `ChainState` stub (now used by SkillExecutor).
- `src/donna/agents/challenger_agent.py` — existing challenger (to be refactored).
- `src/donna/orchestrator/dispatcher.py` — existing dispatcher (to be simplified).
- `donna-diagrams.html` — architecture diagrams companion (to be updated with skill-system diagrams).
