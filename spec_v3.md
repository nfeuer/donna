**DONNA**

AI Personal Assistant

Project Specification Document

Version 3.0 --- March 2026

*Classification: Personal / Confidential*

**1. Executive Summary**

Donna is an AI-powered personal assistant system designed to solve a
specific, well-defined problem: the user consistently forgets to capture
tasks, rarely consults existing task lists, and does not schedule
dedicated time to complete work. The system is named after Donna Paulsen
from the television show Suits and adopts her communication style ---
sharp, confident, efficient, occasionally witty, and always one step
ahead.

The system goes beyond passive task storage. It actively pursues the
user, demands updates, reschedules dynamically, prepares for upcoming
work, and progressively delegates tasks to autonomous sub-agents.
Effectiveness is the single most important design criterion. Every
architectural decision is evaluated against the question: does this help
Nick get more done with less friction?

**1.1 Core Objectives**

-   **Active Task Capture:** Make it effortless to record tasks from any
    device or context, and proactively remind the user to capture tasks
    they may have forgotten.

-   **Intelligent Scheduling:** Dynamically schedule, reschedule, and
    prioritize tasks based on deadlines, inferred complexity, calendar
    constraints, and real-time changes.

-   **Prep Work Automation:** Perform research, compile information, and
    prepare deliverables before the user begins a task.

-   **Autonomous Sub-Agents:** Route eligible tasks to specialized AI
    agents that can assess requirements, request clarification, and
    complete work independently.

-   **Persistent Follow-Up:** Never let tasks silently expire. Escalate
    reminders across channels until the user acknowledges.

-   **Adaptive Learning:** Learn from user corrections and behavioral
    patterns to improve task classification, scheduling, and routing
    over time without model fine-tuning.

**1.2 Key Constraints**

-   **Monthly API Budget:** \$100/month for Claude API (separate from
    Claude Pro subscription). Local LLM offloading deferred until GPU
    hardware secured.

-   **Single User (Multi-User Designed):** Built for the owner. Data
    model includes user_id from day one to support future multi-user
    deployment (Phase 3--4).

-   **Platform:** Windows workstation (i7-12th gen, 32GB, RTX 2080 Ti).
    Always-on Linux server (i7-6700K, 32GB 3200MHz, GTX 1080 dedicated
    to Immich/media). Android phone (Pixel 8 Pro). MacBook Pro for
    mobile.

-   **GPU Strategy:** RTX 3090 (to be acquired) dedicated to Donna and
    local LLM. GTX 1080 remains allocated to Immich and media services.
    No GPU sharing between workloads.

-   **Storage:** Dedicated 1TB NVMe for Donna --- database, workspace,
    agent outputs, backups, and logs. All Donna data lives on this
    volume.

-   **Privacy:** Sensitive data stays off accessible filesystem. Clean
    cloud storage or sandboxed local folders only.

**1.3 Key Design Principles (v3)**

The following principles were established during design review and
govern all architectural decisions:

-   **Config over code:** Model selection, routing rules, prompt
    templates, task type definitions, and user preferences are stored as
    configuration data, not hardcoded in application logic. Adding or
    changing capabilities should not require code changes to the
    orchestrator.

-   **Safety first, dial back later:** All agents start with minimal
    autonomy and strict constraints. Trust is earned through logged,
    reviewed performance. Constraints are relaxed explicitly, never
    assumed.

-   **Structured logging on every model call:** Every LLM invocation
    (local or cloud) is logged with task type, model used, latency,
    tokens, cost, and output. This is the foundation for evaluation,
    cost tracking, and routing optimization.

-   **Comprehensive observability:** All services emit structured JSON
    logs to a dedicated logging database. A self-hosted dashboard
    provides real-time search, filtering, and alerting. Debugging any
    issue should never require SSH and grep.

-   **Dev tool that evolves into runtime feature:** The model comparison
    and evaluation system starts as a development tool for validating
    local LLM quality, but is designed with the data model and
    interfaces to evolve into runtime routing optimization.

-   **Internal API over protocol overhead:** Service integrations use a
    thin internal Python API layer for orchestrator-to-service calls.
    MCP is reserved for LLM-facing tool discovery where agents need to
    reason about available tools dynamically.

-   **Clean seams, not frameworks:** The system is designed for
    configurability where it matters (model providers, tool access, task
    types) without building abstract plugin architectures. Two
    implementations behind a clean interface, not a generic extension
    system.

**2. Persona & Communication Style**

The assistant's persona is modeled after Donna Paulsen from Suits. This
is not cosmetic --- it directly affects user engagement and compliance
with the system. The persona is implemented as a system prompt that
governs all outbound communication.

**2.1 Personality Traits**

-   **Confident and direct.** Donna does not hedge. "You have three
    tasks overdue. I rescheduled two. The third needs your input."

-   **Proactive.** She anticipates needs. "I noticed your oil change has
    been rescheduled twice. I'm putting it on Saturday morning before it
    becomes a problem."

-   **Witty but professional.** Light humor is acceptable. Sarcasm when
    the user is behind on tasks is on-brand. Never sycophantic.

-   **Efficient.** Messages are concise. No filler. Bullet points and
    clear action items.

-   **Loyal and protective of the user's time.** She pushes back on
    overcommitment and flags when the schedule is unrealistic.

**2.2 Communication Examples**

*Morning digest:* "Good morning. You have 6 tasks today, 2 carry-overs
from yesterday. Your 10am meeting prep is done --- check your email.
Also, that invoice you've been avoiding? It's due Friday. I put it at
2pm."

*Overdue nudge (SMS):* "It's 3pm and you haven't touched the API
refactor. Did you finish it or should I find time tomorrow?"

*Budget warning:* "Heads up --- agents burned through \$22 today, mostly
on the codebase analysis. I've paused all autonomous work. Want me to
continue or are we done for the day?"

**3. System Architecture**

**3.1 Architecture Overview**

The system follows a hub-and-spoke architecture with a central
orchestrator managing all task routing, scheduling, and agent
coordination. The always-on Linux server acts as the system backbone,
running the orchestrator, integration layer, and background agent
workers. All services are deployed as Docker containers using the
established homelab compose pattern.

**3.2 Tool Integration Architecture**

The original specification prescribed MCP (Model Context Protocol) as
the sole tool integration layer. Industry developments in early 2026
have revealed significant trade-offs with an MCP-only approach. Donna
adopts a hybrid architecture that uses the right integration pattern for
each use case.

**3.2.1 The MCP Context Cost Problem**

MCP servers dump their entire tool schema into the LLM's context window
on connection. A typical server with 20--90+ tools can consume
30,000--150,000+ tokens before any query is processed. Industry
experience in 2025--2026 showed this overhead consuming 40--50% of
available context windows. For Donna, where every token costs money
against a \$100/month budget, this overhead is unacceptable for internal
integrations where the orchestrator (not the LLM) is making the call.

Mitigations exist: Claude's Tool Search feature (January 2026) reduces
overhead by \~85% through deferred loading. FastMCP 3.x supports
CodeMode, which exposes just search() and execute() meta-tools (\~1,000
tokens instead of the full catalog). However, even with mitigations, MCP
adds serialization overhead and protocol complexity that is unnecessary
when the orchestrator already knows what tool to call with what
parameters.

**3.2.2 Hybrid Strategy**

Donna separates tool usage into two tiers based on who is making the
call:

**Tier 1: Internal Python API (Primary)**

All orchestrator-to-service integration uses thin Python modules. The
orchestrator calls functions directly --- no protocol overhead, no
schema in context windows. The LLM outputs structured JSON; the
orchestrator maps fields to API calls. Zero tokens consumed for tool
definitions.

**Tier 2: MCP Endpoint (LLM-Facing + External Clients)**

MCP via FastMCP 3.x is used when agents need dynamic tool discovery
during reasoning (Research Agent deciding which tools to use, Coding
Agent exploring a repo). Also maintained as a Streamable HTTP endpoint
for the Flutter app, Claude Desktop, and future third-party clients.

Decision framework for each integration:

  ----------------- ---------------- -------------------------------------
  **Integration**   **Pattern**      **Rationale**

  Google Calendar   Direct API       Orchestrator calls with known params.
  API               (Python client)  No discovery needed.

  SQLite Task DB    Direct API       Internal data store. MCP wrapper
                    (aiosqlite)      would add pure overhead.

  Discord Bot       Direct API       Bidirectional messaging. Bot
                    (discord.py)     framework handles natively.

  Gmail API         Direct API       Orchestrator reads/drafts with known
                    (Python client)  scopes.

  Twilio SMS/Voice  Direct API       Outbound notifications with fixed
                    (Python client)  parameters.

  Supabase Sync     Direct API       Background sync process with fixed
                    (supabase-py)    schema.

  GitHub            MCP (FastMCP)    Coding Agent explores repos and
                                     issues dynamically.

  Web Search        MCP (FastMCP)    Research Agent discovers and invokes
                                     search dynamically.

  Filesystem        MCP (FastMCP)    Agents need to discover and navigate
  (sandboxed)                        files dynamically.

  Notes (Local      MCP (FastMCP)    Agents need to discover and read
  Markdown)                          notes dynamically.
  ----------------- ---------------- -------------------------------------

**3.2.3 FastMCP Server (Python)**

The MCP server is implemented in Python using FastMCP 3.x, keeping the
entire stack in a single language. It exposes tools that agents need
during LLM-driven reasoning. The server uses FastMCP's CodeMode
transform to minimize context window consumption.

Key design principles: tool granularity (each action is a separate tool
for fine-grained access control per agent and per task type),
centralized authentication (all OAuth tokens, API keys stored in MCP
server config, never passed to agents), audit logging (every tool
invocation logged with timestamp, calling agent, parameters, and
result), rate limiting (per-tool limits to prevent runaway agents), and
tool registry as config (adding a new tool = implementation + config
entry, orchestrator discovers tools at startup).

**3.2.4 Integration Modules**

> integrations/
>
> ├── calendar.py ← Google Calendar (read-write personal, read all)
>
> ├── gmail.py ← Gmail (read + draft; send behind feature flag)
>
> ├── github.py ← GitHub (MCP-wrapped, read-write feature branches only)
>
> ├── filesystem.py ← Sandboxed to /donna/workspace/
>
> ├── discord.py ← Send/read in Donna channels
>
> ├── twilio_sms.py ← SMS and voice (outbound only)
>
> ├── notes.py ← Local markdown notes
>
> ├── search.py ← Web search (SearXNG or API)
>
> └── mcp_wrapper.py ← FastMCP Streamable HTTP for external clients

Each module: centralized auth, audit logging to logging DB, rate
limiting, access control per agent via task type config.

**3.2.5 Adopt Before Building**

Before implementing custom MCP tools, evaluate existing open-source MCP
servers from the community (e.g., google-calendar-mcp, GitHub MCP
server). If a community server covers 80%+ of needed functionality,
adopt it and extend rather than building from scratch. The FastMCP
framework's composability model supports mounting external servers
alongside custom tools.

**3.3 Component Map**

  ---------------- --------------- ---------------------------------------
  **Component**    **Location**    **Purpose**

  Orchestrator     Linux Server    Central brain. Manages task queue,
  Service          (Docker)        scheduling engine, agent dispatch, and
                                   cost monitoring. Runs 24/7.

  Claude API       Cloud           Primary LLM for all reasoning: task
                   (Anthropic)     parsing, classification, routing, code
                                   generation, prep work, scheduling
                                   decisions. Sole provider until local
                                   LLM hardware is available.

  Local LLM        Linux Server    DEFERRED until 3090 acquired. Will
  (Ollama)         (Docker) ---    handle task classification, priority
                   RTX 3090        inference, routing, simple NLU.
                                   Dedicated GPU, no sharing with other
                                   services.

  Integration      Linux Server    Internal Python API wrapping all
  Layer            (Docker)        external services. Centralized auth,
                                   rate limiting, audit logging.

  FastMCP Server   Linux Server    Exposes dynamic tools to agents via MCP
                   (Docker)        Streamable HTTP. Python (FastMCP 3.x).
                                   CodeMode enabled for token efficiency.

  Task Database    SQLite on NVMe  Primary task storage with full
                                   metadata. Sub-millisecond reads. WAL
                                   mode for concurrent access.

  Logging Database SQLite on NVMe  Structured application logs and audit
                   (dedicated)     trails. Separate from task DB to avoid
                                   contention.

  Sync Replica     Supabase        Cloud replica for cross-device access.
                   (Postgres)      Free tier with keep-alive; upgrade to
                                   Pro at Phase 4 or multi-user.

  Observability    Grafana + Loki  Self-hosted. Real-time log search,
  Dashboard        (Docker)        filtering, metrics, and alerting. Phase
                                   1 deliverable.

  Notification     Linux Server    Outbound communication: email (Gmail
  Service          (Docker)        API/SMTP), SMS (Twilio), phone (Twilio
                                   TTS), push (FCM), Discord bot.

  Agent Worker     Linux Server    Executes sub-agent tasks in sandboxed
  Pool             (Docker)        environments. Each agent type runs as
                                   an isolated process with defined tool
                                   access.

  Web/Mobile App   Firebase        Dashboard UI (calendar view, task
                   Hosting +       board, agent monitor) and
                   Flutter         conversational chat interface. Phase 4.
  ---------------- --------------- ---------------------------------------

**3.4 Data Flow**

All task inputs (SMS, Discord, Slack, app, email forwarding) are
normalized into a standard task schema by the Input Parser. During Phase
1--2 (pre-local LLM), parsing runs on Claude API. Once the RTX 3090 is
available and the local model is validated, high-frequency parsing
shifts to the local LLM with Claude API as fallback. The Orchestrator
evaluates each task against the scheduling engine and decides routing.
Agent outputs are stored, reviewed, and surfaced through the
notification service.

**3.5 Infrastructure & Deployment**

Donna deploys on the existing homelab Docker Compose infrastructure.
Each Donna service gets its own compose file following the established
multi-file pattern, attaching to the shared homelab network. This means
Donna services can communicate with existing services (if needed) and
new Donna components can be added without modifying existing stack
files.

**3.5.1 Docker Compose Structure**

> docker/
>
> ├── .env.example ← copy to .env (gitignored)
>
> ├── core.yml ← shared homelab network
>
> ├── immich.yml ← Immich stack (GTX 1080)
>
> ├── donna-core.yml ← Orchestrator, integration layer, notification
> service
>
> ├── donna-monitoring.yml ← Grafana, Loki, Promtail (dev dashboard)
>
> ├── donna-ollama.yml ← Ollama + local LLM (RTX 3090, added post-GPU)
>
> └── donna-app.yml ← FastAPI backend (Flutter app connects here)

**3.5.2 GPU Isolation**

GPU assignment is managed through environment variables in docker/.env,
consistent with the existing homelab pattern. Each GPU-using service
references its own variable. No compose file changes are needed when
hardware changes.

> \# docker/.env
>
> IMMICH_ML_GPU_ID=0 \# GTX 1080 \-\-- dedicated to Immich/media
>
> DONNA_OLLAMA_GPU_ID=1 \# RTX 3090 \-\-- dedicated to Donna LLM

This isolation ensures no VRAM contention between Immich's ML pipeline
and Donna's local LLM inference. The 3090's 24GB VRAM provides
substantial headroom for running larger quantized models or multiple
models concurrently if needed.

**3.5.3 NVMe Storage Layout**

The dedicated 1TB NVMe is mounted and organized as Donna's complete data
volume:

> /donna/
>
> ├── db/
>
> │ ├── donna_tasks.db ← Primary task SQLite database
>
> │ ├── donna_logs.db ← Dedicated logging SQLite database
>
> │ └── donna_eval.db ← Evaluation harness results
>
> ├── workspace/ ← Agent sandboxed working directory
>
> ├── backups/
>
> │ ├── daily/ ← 7-day retention
>
> │ ├── weekly/ ← 4-week retention
>
> │ ├── monthly/ ← 3-month retention
>
> │ └── offsite/ ← Staging for cloud backup sync
>
> ├── logs/
>
> │ └── archive/ ← Compressed historical log exports
>
> ├── config/
>
> │ ├── donna_models.yaml ← Model routing configuration
>
> │ ├── task_types.yaml ← Task type definitions
>
> │ ├── task_states.yaml ← State machine transitions
>
> │ └── preferences.yaml ← Learned preference rules
>
> ├── prompts/ ← Externalized prompt templates
>
> ├── fixtures/ ← Evaluation test fixtures (version-controlled)
>
> └── models/ ← Ollama model cache (Phase 3+)

**3.6 API Resilience Layer**

Every Claude API call goes through a resilience wrapper handling
retries, degraded mode fallback, and circuit breaking. Phase 1 is
entirely dependent on Claude API availability; the resilience layer
ensures Donna degrades gracefully rather than failing silently.

**3.6.1 Retry Policies**

  ------------------ ----------- ------------------ -------------------------
  **Task Category**  **Max       **Backoff**        **On Failure**
                     Retries**                      

  Critical (digest,  3           Exponential, 2s    Fall back to degraded
  deadline                       start, 30s cap     mode (template-based)
  reminders)                                        

  Standard (parse,   2           Exponential, 1s    Queue for retry on next
  classify)                      start, 15s cap     cycle; notify user of
                                                    delay

  Agent work         1           5s fixed           Mark agent_status =
  (research, code                                   failed; notify user; do
  gen)                                              not retry (budget
                                                    protection)
  ------------------ ----------- ------------------ -------------------------

**3.6.2 Degraded Mode Definitions**

-   **Morning digest:** Generate a template-based digest using raw
    calendar data and task list from SQLite. No LLM reasoning, no Donna
    persona. Format: "Today's schedule: \[list of events\]. Tasks due:
    \[list\]. Note: AI digest unavailable, showing raw data." This
    ensures the user always gets their morning overview even if Claude
    is down.

-   **Reminders:** Send with static template: "Reminder: \[task title\]
    starts at \[time\]." Functionality preserved, personality lost ---
    acceptable trade-off.

-   **Task parsing:** Accept raw text as-is, create a task with title =
    raw input, all other fields set to defaults, and flag it for
    re-parsing when the API recovers. Never lose a task capture because
    the LLM is unavailable.

**3.6.3 Circuit Breaker**

If 5 consecutive API calls fail within a 10-minute window, the
orchestrator enters circuit-breaker mode: pause all non-critical agent
work, switch all critical paths to degraded mode, send the user a single
SMS notification ("Donna's AI is temporarily unavailable. Reminders and
captures are running in basic mode. I'll notify you when it's
restored."). The circuit breaker tests recovery every 5 minutes with a
lightweight health-check call. Resets on first successful response.

**3.6.4 Response Validation**

Every API response is validated against the expected output schema
before being used. Malformed JSON, missing required fields, or schema
mismatches trigger a retry (counted against the retry budget). This
catches partial responses from timeouts or rate-limited truncations.

**3.7 Concurrency Model**

**3.7.1 Phase 1--2: Single-Threaded Asyncio Event Loop**

The orchestrator is a single Python process running an asyncio event
loop. All I/O (Discord bot, API calls, SQLite reads/writes, calendar
API) is async. Concurrency comes from I/O multiplexing, not parallelism.

-   SQLite access is serialized through a single async connection
    (aiosqlite) with WAL (Write-Ahead Logging) mode enabled. WAL allows
    concurrent reads with a single writer, which is exactly the access
    pattern: the orchestrator writes, and the Supabase sync process
    reads.

-   Calendar write operations are serialized through an async queue. If
    two tasks need to create calendar events simultaneously, they are
    queued and processed sequentially. This prevents double-booking race
    conditions without complex locking.

-   Task state transitions are atomic: read current state, validate
    transition, write new state, execute side effects --- all in a
    single async function with a SQLite transaction. No interleaving
    possible.

**3.7.2 Phase 3+: Task Queue with Worker Pool**

When agents need to run in parallel (Coding Agent and Research Agent
working on different tasks simultaneously), add a task queue
(asyncio.Queue or a lightweight broker like arq backed by Redis). The
orchestrator dispatches to the queue; worker processes pull tasks and
execute independently. Shared state (task DB) is accessed through the
orchestrator's internal API, not directly by workers. This prevents
workers from making conflicting state changes.

Agent isolation: each agent worker is a separate Docker container (or at
minimum a separate process) with its own tool access scope. Workers
communicate with the orchestrator via an internal API, not by directly
modifying shared state.

**3.8 Schema Migration**

All SQLite tables are defined using SQLAlchemy models. Alembic manages
schema evolution for both the task database and the logging database.
Migration files are version-controlled in alembic/versions/ in the repo.

-   On orchestrator startup: run alembic upgrade head to apply any
    pending migrations. If the DB is fresh, this creates all tables. If
    existing, applies only new migrations.

-   Every schema change (new field, type change, enum expansion, index
    addition) gets its own migration file with upgrade() and downgrade()
    functions.

-   Never modify existing migration files --- only add new ones.

-   For the Supabase Postgres replica: maintain a parallel set of
    migrations or use the same Alembic config with a Postgres connection
    string. Schemas should match, but Postgres-specific features (Row
    Level Security) get their own migration files.

-   Pre-migration backup: the Alembic migration runner automatically
    creates a SQLite backup before applying any migration. If the
    migration fails, the backup is the rollback path.

-   Testing: before applying a migration to the production DB, test it
    against a copy. The backup strategy (Section 16) makes this easy.

**4. Model Abstraction & Evaluation Layer**

The model layer is designed around a core principle: the orchestrator
and agents never call a specific model provider directly. All LLM
interactions go through a standardized interface that handles provider
abstraction, structured logging, routing decisions, and shadow
evaluation.

**4.1 Model Interface**

Every model call goes through a single function signature:

> complete(prompt, schema, model_alias) → (response, metadata)

The metadata object always includes: latency_ms, tokens_in, tokens_out,
cost_usd, model_actual (resolved provider + model name), and whether it
was a shadow run. Two implementations exist behind this interface:
AnthropicProvider (Claude API) and OllamaProvider (local LLM). A third
provider can be added when needed without changing any calling code.

**4.2 Routing Configuration**

The routing table maps model aliases to providers and defines
per-task-type behavior. This is the primary configuration surface for
controlling which model handles what work.

> \# donna_models.yaml
>
> models:
>
> parser:
>
> provider: anthropic \# ollama once 3090 available
>
> model: claude-sonnet-4-20250514
>
> reasoner:
>
> provider: anthropic
>
> model: claude-sonnet-4-20250514
>
> fallback:
>
> provider: anthropic
>
> model: claude-sonnet-4-20250514
>
> routing:
>
> task_parse:
>
> model: parser
>
> fallback: reasoner
>
> confidence_threshold: 0.7
>
> priority_classify:
>
> model: parser
>
> fallback: reasoner
>
> confidence_threshold: 0.7
>
> prep_research:
>
> model: reasoner
>
> code_generation:
>
> model: reasoner
>
> morning_digest:
>
> model: parser
>
> shadow: reasoner \# production monitoring: run secondary model, log
> only

During Phase 1 (Claude API only), all model aliases point to the
Anthropic provider. When the local LLM becomes available, switching a
task type to local requires changing only the provider and model fields
for the relevant alias. The shadow key enables production monitoring
(secondary model runs in parallel, output logged but not used). Offline
evaluation is triggered via CLI with an explicit model argument, not
configured in routing.

**4.3 Structured Invocation Logging**

Every model call is logged to the invocation_log table. This is the
foundation for cost tracking, evaluation, and future routing
optimization.

  ------------------- ---------- -------------------------------------------
  **Field**           **Type**   **Purpose**

  id                  UUID       Unique invocation identifier

  timestamp           DateTime   When the call was made

  task_type           String     Which task type (parse, classify, generate,
                                 etc.)

  task_id             UUID?      Associated task if applicable

  model_alias         String     Config alias used (parser, reasoner, etc.)

  model_actual        String     Resolved provider + model
                                 (anthropic/claude-sonnet-4-20250514)

  input_hash          String     Hash of input for dedup and comparison
                                 matching

  latency_ms          Int        Wall clock time for the call

  tokens_in           Int        Input tokens consumed

  tokens_out          Int        Output tokens generated

  cost_usd            Float      Computed cost (\$0.00 for local models
                                 before cost approx configured)

  output              JSON       The actual structured response

  quality_score       Float?     Nullable. Filled by spot-check batch job or
                                 offline eval

  is_shadow           Boolean    Whether this was a shadow run (production
                                 monitoring) or eval run (offline
                                 comparison)

  eval_session_id     UUID?      Groups invocations from a single evaluation
                                 run for session-based comparison

  spot_check_queued   Boolean    Whether this invocation is queued for
                                 Claude-as-judge review

  user_id             String     User who triggered the call
  ------------------- ---------- -------------------------------------------

**4.4 Shadow Mode (Production Monitoring)**

Shadow mode runs a secondary model on the same input in production
without affecting the primary output. The primary model's response is
used for all downstream processing; the shadow model's response is
logged to the invocation_log for comparison. This is a runtime feature
for ongoing quality monitoring after a task type has been migrated to a
different model.

**Use case:** After migrating task_parse from Claude to a local model,
keep Claude as a shadow for 2--4 weeks to monitor whether the local
model's quality holds up on real production inputs. If shadow comparison
shows quality degradation, revert the migration by changing the routing
config.

**Cost implication:** Shadow mode doubles the model cost for that task
type (two calls per input). It is intended as a temporary monitoring
tool, not a permanent configuration. Disable once confidence is
established.

**4.5 Offline Evaluation Harness (Model Comparison)**

The evaluation harness is a development tool for comparing multiple
models against the same test inputs. It is triggered via CLI, not part
of production routing. Its primary purpose is model selection:
determining which local LLM, quantization level, and parameter size best
fits each task type on available hardware.

**4.5.1 Tiered Test Fixtures**

Fixtures are organized into complexity tiers. Each tier builds on the
previous; if a model fails Tier 1, there is no need to continue to
higher tiers. This saves time when evaluating models that clearly are
not suitable. Fixtures are version-controlled in the repo so
collaborators with different hardware run identical evaluations.

> fixtures/
>
> ├── parse_task/
>
> │ ├── tier1_baseline.json \# \~10 cases: simple, unambiguous inputs
>
> │ ├── tier2_nuance.json \# \~15 cases: implicit deadlines, domain
> ambiguity
>
> │ ├── tier3_complexity.json \# \~10 cases: multi-part tasks,
> dependencies
>
> │ └── tier4_adversarial.json \# \~5 cases: edge cases, contradictions,
> buried tasks
>
> ├── classify_priority/
>
> │ ├── tier1_baseline.json
>
> │ └── tier2_nuance.json
>
> ├── generate_digest/
>
> │ └── tier1_baseline.json \# with quality rubrics for subjective
> evaluation
>
> ├── deduplication/
>
> │ └── tier1_baseline.json \# exact dupes, reformulations,
> related-but-distinct
>
> ├── escalation_awareness/ \# cross-cutting evaluation dimension
>
> │ ├── should_escalate.json \# tasks the local model should NOT attempt
>
> │ └── should_handle.json \# tasks the local model should handle (false
> positive check)
>
> └── instruction_following/ \# cross-cutting evaluation dimension
>
> ├── claude_decomposition.json \# Claude-generated subtask instructions
>
> ├── constraint_compliance.json \# multi-constraint classification
> directives
>
> └── correction_application.json \# apply a learned correction rule to
> new inputs

**4.5.2 Tier Definitions**

  ---------- ------------- ----------- ------------------------------- -------------------
  **Tier**   **Name**      **Cases**   **Purpose**                     **Pass Gate**

  1          Baseline      \~10        Simple, unambiguous inputs any  90%+ accuracy to
                                       reasonable model should handle. continue
                                       \"Buy milk,\" \"pay electric    
                                       bill by Friday.\" Quick         
                                       pass/fail gate.                 

  2          Nuance        \~15        Inputs requiring inference:     80%+ accuracy
                                       implicit deadlines (\"before    
                                       the holidays\"), domain         
                                       ambiguity (\"fix the leak\"),   
                                       priority signals (\"this is     
                                       urgent\" vs \"whenever\").      

  3          Complexity    \~10        Multi-part tasks, dependency    60%+ accuracy
                                       implications, tasks benefiting  
                                       from tool use. \"Refactor the   
                                       auth module before the API      
                                       launch next month.\"            

  4          Adversarial   \~5         Edge cases: ambiguous inputs,   No gate ---
                                       contradictions, non-tasks that  diagnostic only
                                       look like tasks, long freeform  
                                       messages with a task buried in  
                                       them. Tests graceful failure.   
  ---------- ------------- ----------- ------------------------------- -------------------

Fixtures grow over time. When user corrections or spot-checks reveal an
interesting failure case, that input/correction pair is added to the
appropriate tier. The evaluation harness becomes more comprehensive as
the system is used.

**4.5.3 Sequential Evaluation (One Model at a Time)**

A single GPU can only run one model at a time. Ollama loads models into
VRAM on request and unloads when a different model is requested. The
evaluation harness runs sequentially: load model A, run all fixtures
through it, save results, then swap to model B and repeat. Models are
not run in parallel.

Triggered via CLI:

> donna eval \--task-type task_parse \--model ollama/llama3.1:8b-q4

This loads the specified model, runs it against all fixture tiers
(stopping early if a tier fails its pass gate), and saves the results as
a model session. To compare, run the command again with a different
model at a later time. The comparison is across saved sessions, not
simultaneous runs.

The typical workflow is: install the largest model that fits in VRAM,
evaluate it, then try progressively smaller or differently quantized
models to understand the quality/speed tradeoff for each task type.

**4.5.4 Model Sessions**

Each evaluation run is saved as a model session --- a record of which
model was tested, when, on which hardware, and the results across tiers
and dimensions. Sessions persist in the evaluation database so
comparisons can be made across days or weeks as different models are
tested. Collaborators with different hardware run the same fixtures and
share their session results. The fixtures are the shared contract; the
model selection and session data are per-environment.

**4.5.5 What the Harness Answers**

-   Quantization tradeoffs: Does Q6 perform measurably better than Q4
    for task parsing, or is the quality difference negligible for the
    latency cost?

-   Parameter scaling: Is 8B sufficient for priority classification, or
    does a 13B model meaningfully improve accuracy on Tier 2--3 cases?

-   Speed vs quality: Is a smaller, faster model (Phi-3 3.8B) adequate
    for simple classification tasks where only Tier 1 accuracy matters?

-   Hardware fit: Which models fit within the VRAM constraints of
    different GPUs (8GB 1080, 12GB 4070, 24GB 3090) while still passing
    tier gates?

-   Task-type specialization: Could a smaller model handle parsing (Tier
    1--2 sufficient) while a larger model is reserved for complex
    reasoning (Tier 3--4 critical)?

-   Multi-model coordination: Can the model recognize tasks beyond its
    capability and follow structured instructions from Claude
    effectively enough to participate in a hub-and-spoke workflow?

**4.5.6 Evaluation Dimensions: Escalation Awareness & Instruction
Following**

In addition to the complexity tiers (which measure how well a model
handles increasingly difficult tasks), two cross-cutting evaluation
dimensions measure how well a model operates as a subordinate in a
multi-model system. These are critical because Donna's architecture
relies on the local model knowing its limits and taking direction from
Claude.

**Escalation Awareness --- "I shouldn't handle this."**

This measures whether the model recognizes that a task is beyond its
capability before producing output. This is distinct from confidence
scoring, which measures uncertainty about the output after the model has
already attempted it. Escalation awareness is about the model knowing
when not to try.

The escalation fixtures contain two sets: should_escalate.json (tasks
the local model should NOT attempt, e.g., multi-step research, code
review requiring full codebase understanding, ambiguous requests
requiring judgment) and should_handle.json (tasks the local model should
handle confidently, checking for over-escalation).

  --------------- ------------------- ------------ ------------------------------
  **Metric**      **Definition**      **Target**   **Why This Threshold**

  Precision       Of tasks the model  85%+         Over-escalation wastes money
  (correctly      escalated, what %                but produces correct results.
  escalated)      truly needed                     Tolerable.
                  Claude?                          

  Recall (caught  Of tasks that       85%+         Under-escalation produces
  tasks it        needed Claude, what              garbage output. Less
  shouldn't       % did the model                  tolerable. Err toward
  handle)         flag?                            escalating.

  False positive  \% of handleable    \< 25%       Above this, the cost savings
  rate            tasks unnecessarily              of local LLM are undermined.
                  escalated                        
  --------------- ------------------- ------------ ------------------------------

The asymmetry is deliberate: under-escalation (model attempts a task it
can't handle) produces bad output that may go undetected.
Over-escalation (model sends a simple task to Claude) costs more but
produces correct results. This aligns with the safety-first principle
--- better to spend an extra few cents than to silently produce wrong
answers.

**Instruction Following --- "Claude told me how to do this."**

This measures the model's ability to operate as a subordinate in a
multi-model chain. The realistic scenario: Claude decomposes a complex
task into subtasks with specific, structured instructions, and the local
model executes each subtask. Or Claude provides a correction with
guidance, and the local model applies that guidance to new inputs.

The instruction-following fixtures include three categories:
claude_decomposition.json (can the model execute subtasks as specified
by Claude?), constraint_compliance.json (does the model apply all
constraints or silently drop some?), and correction_application.json
(given a learned correction rule, does the model apply it correctly and
ignore it when irrelevant?).

  ------------------ --------------------------------- -------------------
  **Metric**         **Definition**                    **Target**

  Constraint         Out of N constraints in the       90%+
  compliance         instruction, how many were        
                     satisfied?                        

  Format adherence   Did the output match the          95%+
                     requested schema/structure?       

  Rule application   When a correction rule applies,   85%+
  accuracy           was it applied correctly?         

  Rule false         When a correction rule does NOT   \< 10%
  application        apply, was it incorrectly         
                     triggered?                        
  ------------------ --------------------------------- -------------------

A model that scores well on complexity tiers but poorly on instruction
following is useful for independent tasks but not for Claude-directed
workflows. A model that scores well on instruction following but poorly
on complexity is ideal as a Claude subordinate but should not be trusted
for autonomous work. The evaluation harness reveals these profiles so
routing decisions can be made accordingly.

**4.5.7 Model Session Output**

Model sessions include tier results and dimension scores, providing a
complete profile of each model's capabilities:

> Model Session: llama3.1-8b-q4 (2026-03-15, RTX 3090)
>
> Task Parse Tiers:
>
> Tier 1: 10/10 (100%) avg latency: 180ms
>
> Tier 2: 12/15 (80%) avg latency: 340ms
>
> Tier 3: 6/10 (60%) avg latency: 890ms
>
> Escalation Awareness:
>
> Precision: 88% Recall: 82% False positive: 18%
>
> Instruction Following:
>
> Constraint compliance: 91% Format adherence: 96%
>
> Rule application: 87% Rule false application: 8%
>
> \-\--
>
> Model Session: mistral-7b-q4 (2026-03-20, RTX 3090)
>
> Task Parse Tiers:
>
> Tier 1: 10/10 (100%) avg latency: 150ms
>
> Tier 2: 11/15 (73%) avg latency: 290ms
>
> Tier 3: 5/10 (50%) avg latency: 750ms
>
> Escalation Awareness:
>
> Precision: 92% Recall: 70% False positive: 12%
>
> Instruction Following:
>
> Constraint compliance: 84% Format adherence: 90%
>
> Rule application: 79% Rule false application: 15%

This tells you: Llama 3.1 8B is a better Claude subordinate (higher
instruction compliance, better recall on escalation). Mistral is more
precise about when to escalate (fewer false positives) but misses more
tasks it should escalate (lower recall) and is weaker at following
multi-constraint directives. For a safety-first deployment, Llama's
profile is preferable despite being slower.

**4.6 Spot-Check Quality Monitoring**

Spot-checks are periodic quality audits of production model outputs
using Claude-as-judge. They are most valuable in Phase 3+ when the local
LLM is handling production traffic. In Phase 1 (Claude-only),
spot-checks are not active since Claude would be evaluating its own
output.

**4.6.1 Configuration**

> quality_monitoring:
>
> spot_check_rate: 0.05 \# 5% of production calls sampled
>
> judge_model: reasoner \# which model evaluates
>
> judge_batch_schedule: weekly
>
> flag_threshold: 0.7 \# scores below this are flagged
>
> enabled: false \# disabled in Phase 1, enable in Phase 3

The orchestrator rolls a random number on each production invocation. If
the roll falls within the spot_check_rate, the output is queued for
batch review. The batch job runs on the configured schedule and sends
queued outputs to Claude with a judging prompt. Results are written back
to the invocation_log's quality_score field.

**4.6.2 Flagged Output Handling**

When a spot-check scores below the flag_threshold, the system does not
require a separate review UI. Instead, it creates a Donna task:

> Task: \"3 low-quality parses flagged this week. Review and provide
> corrections.\"
>
> Domain: work
>
> Priority: 2
>
> Notes: \[links to relevant invocation_log entries\]

The user reviews the flagged outputs, provides corrections, and those
corrections flow into the correction log (Section 9). This keeps quality
improvement in the same workflow as everything else --- Donna managing
its own quality as a task in the system it manages.

**4.6.3 Cadence Tuning**

During early local LLM deployment, set spot_check_rate higher
(0.10--0.20) to get fast signal on quality. As confidence builds and
corrections decrease, dial it back to 0.02--0.05. The rate is a config
value, adjustable at any time without code changes. If a model change is
deployed, temporarily increase the rate to validate the new model's
production quality.

**4.7 Confidence Scoring**

For routing decisions that depend on model confidence (e.g., falling
back from local to Claude), two approaches are supported. Confidence
scoring is relevant in Phase 3+ when the local LLM is handling
production traffic.

-   **Self-assessed confidence (default):** Include a confidence field
    (0.0--1.0) in the structured output schema. The model rates its own
    certainty. Simple to implement, effective for detecting "I don't
    know" cases, less reliable for "confidently wrong" detection.

-   **Logprob-based scoring (optional upgrade):** When using Ollama,
    examine average token logprobs on structured output. Low confidence
    in tokens correlates with low confidence in the parse. Requires
    post-processing of Ollama API response.

Recommendation: start with self-assessed confidence, log actual accuracy
against test fixtures, and correlate the two. Move to logprob-based
scoring only if self-assessment proves unreliable for specific task
types.

**Relationship to escalation awareness:** Confidence scoring and
escalation awareness (Section 4.5.6) are complementary. Confidence
scoring measures per-output uncertainty after the model has attempted a
task. Escalation awareness measures whether the model recognizes a task
is beyond its capability before attempting it. A well-functioning system
uses both.

**4.8 Local Model Cost Approximation**

To enable meaningful cost comparison between local and cloud models, the
config supports an estimated cost per 1K tokens for local models:

> models:
>
> parser:
>
> provider: ollama
>
> model: llama3.1:8b-q4
>
> estimated_cost_per_1k_tokens: 0.0001 \# approx from hardware
> amortization

This ensures the cost dashboard never shows local inference as "free"
and enables genuine cost-per-quality analysis.

**5. Task Management System**

**5.1 Task Schema**

Every task is represented by the following schema. Fields marked
auto-populated are inferred by the system; the user only needs to
provide natural language input. The user_id field is included from day
one to support future multi-user deployment.

  ------------------------ ------------- --------------- ---------------------------------
  **Field**                **Type**      **Source**      **Description**

  id                       UUID          Auto            Unique task identifier

  user_id                  String        Auto            Owner of the task. Defaults to
                                                         primary user. Enables future
                                                         multi-user.

  title                    String        User/Inferred   Task title, extracted from
                                                         natural language input

  description              String        User/Agent      Detailed description. May be
                                                         populated by PM agent
                                                         interrogation.

  domain                   Enum          Inferred        personal \| work \| family
                                                         (extensible)

  priority                 Int (1--5)    Inferred/User   1 = lowest, 5 = critical.
                                                         Auto-inferred from deadline
                                                         proximity, keywords, domain.

  status                   Enum          Auto            backlog \| scheduled \|
                                                         in_progress \| blocked \|
                                                         waiting_input \| done \|
                                                         cancelled

  estimated_duration       Minutes       Inferred        How long the task will take.
                                                         Inferred from complexity
                                                         analysis.

  deadline                 DateTime?     User/Inferred   Hard deadline if specified. Null
                                                         if flexible.

  deadline_type            Enum          Inferred        hard \| soft \| none

  scheduled_start          DateTime?     Scheduler       When the task is scheduled on the
                                                         calendar

  actual_start             DateTime?     Auto            When the user actually started

  completed_at             DateTime?     Auto            Completion timestamp for velocity
                                                         tracking

  recurrence               Cron/RRULE?   User            Recurrence pattern if applicable

  dependencies             UUID\[\]      User/Agent      Tasks that must complete before
                                                         this one can start

  parent_task              UUID?         Agent           Parent task if this is a subtask

  prep_work_flag           Boolean       User            Whether prep work should be
                                                         performed before scheduled time

  prep_work_instructions   String?       User            What the assistant should prepare

  agent_eligible           Boolean       Inferred/User   Whether this task can be
                                                         delegated to a sub-agent

  assigned_agent           String?       Orchestrator    Which agent is handling this task

  agent_status             Enum?         Agent           pending \| gathering_requirements
                                                         \| in_progress \| review \|
                                                         complete \| failed

  tags                     String\[\]    User/Inferred   Freeform tags for filtering and
                                                         grouping

  notes                    String\[\]    User/Agent      Running notes and context

  reschedule_count         Int           Auto            How many times rescheduled.
                                                         Triggers priority escalation.

  created_at               DateTime      Auto            Creation timestamp

  created_via              Enum          Auto            sms \| discord \| slack \| app \|
                                                         email \| voice

  estimated_cost           Float?        Auto            Estimated API cost if
                                                         agent-eligible

  calendar_event_id        String?       Auto            Google Calendar event ID for sync
                                                         tracking

  donna_managed            Boolean       Auto            Whether Donna created and manages
                                                         this calendar event
  ------------------------ ------------- --------------- ---------------------------------

**5.2 Task Lifecycle State Machine**

Explicit state machine defined in task_states.yaml. Orchestrator loads
at startup, rejects invalid transitions. Each transition specifies
triggers and side effects. This prevents ad-hoc transition logic from
scattering across the codebase and ensures consistent state handling.

**5.2.1 Valid Transitions**

  --------------- ------------- ---------------------- -----------------------
  **From**        **To**        **Trigger**            **Side Effects**

  backlog         scheduled     Scheduler assigns time Calendar event created;
                                slot                   calendar_event_id
                                                       stored; donna_managed =
                                                       true

  scheduled       in_progress   User acknowledges      actual_start timestamp
                                start OR scheduled     set
                                time arrives           

  scheduled       backlog       User cancels scheduled Calendar event deleted;
                                time, no new time      reschedule_count++
                                requested              

  in_progress     done          User/agent reports     completed_at set;
                                completion             velocity metrics
                                                       updated

  in_progress     blocked       User/agent reports     Dependencies updated;
                                blocker                blocking reason logged;
                                                       dependent tasks
                                                       notified

  in_progress     scheduled     User requests          New time slot assigned;
                                reschedule             reschedule_count++;
                                                       calendar event updated

  blocked         scheduled     Blocker resolved       Scheduler finds next
                                (dependency completed  available slot
                                or user unblocks)      

  blocked         cancelled     User decides to        Dependent tasks flagged
                                abandon blocked task   for review

  waiting_input   scheduled     User/agent provides    PM Agent updates task;
                                required information   scheduler assigns slot

  waiting_input   cancelled     No response after      User notified; task
                                configurable timeout   archived
                                (default 7 days)       

  any             cancelled     User explicitly        Dependent tasks
                                cancels                flagged; calendar event
                                                       deleted if exists

  done            in_progress   User reopens a         completed_at cleared
                                completed task         
  --------------- ------------- ---------------------- -----------------------

**5.2.2 Invalid Transitions (Enforced at Orchestrator Level)**

-   backlog → done: Cannot complete without scheduling. Must go through
    scheduled → in_progress → done.

-   cancelled → any state except backlog: Must be explicitly re-opened
    to backlog first.

-   done → scheduled: Must go through in_progress first for reopening.

**5.3 Task Deduplication**

Two-pass deduplication prevents duplicate task creation without blocking
the capture pipeline.

**5.3.1 Pass 1: Fuzzy Title Match**

Uses rapidfuzz (Python library, fast C implementation) with token-sort
ratio. This catches simple reformulations like "get oil change" vs "oil
change needed." Cost: zero (local computation). Applied to all incoming
tasks.

-   Above 85% similarity: auto-flag as duplicate, ask user to confirm
    merge.

-   Below 70% similarity: clearly different, no further check.

-   70--85% range: proceed to Pass 2 for LLM arbitration.

**5.3.2 Pass 2: LLM Semantic Comparison**

For candidates in the 70--85% fuzzy range, send both task descriptions
to the LLM with a structured prompt: "Are these the same task? Respond
with: same (merge), related (link but keep separate), or different (no
relation)." This catches "oil change for car" vs. "oil change for lawn
mower" (different) and "send the invoice" vs. "email that invoice to the
client" (same).

**5.3.3 User Flow**

When a duplicate is detected, the user is prompted on the same channel:
"This looks like a duplicate of '\[existing task title\]' (created
\[date\]). Should I merge them, keep both, or update the existing one?"

Dedup accuracy is tracked in evaluation fixtures
(deduplication/tier1_baseline.json). Track false positive rate
(incorrectly flagged as duplicate) and false negative rate (missed
actual duplicate) over time.

**5.4 Task Type Registry**

Task types define how the system processes different categories of work.
Each task type is a configuration entry specifying a prompt template,
output schema, model assignment, and tool dependencies. Adding a new
task type requires only config and (when a new tool is needed) a tool
implementation --- no orchestrator code changes.

> \# task_types.yaml
>
> task_types:
>
> parse_task:
>
> description: \"Extract structured task fields from natural language\"
>
> model: parser
>
> prompt_template: prompts/parse_task.md
>
> output_schema: schemas/task_parse_output.json
>
> tools: \[\]
>
> classify_priority:
>
> description: \"Assign priority 1-5 based on content and context\"
>
> model: parser
>
> prompt_template: prompts/classify_priority.md
>
> output_schema: schemas/priority_output.json
>
> tools: \[task_db_read\]
>
> generate_digest:
>
> description: \"Generate morning digest in Donna persona\"
>
> model: parser
>
> shadow: reasoner
>
> prompt_template: prompts/morning_digest.md
>
> output_schema: schemas/digest_output.json
>
> tools: \[calendar_read, task_db_read, cost_summary\]
>
> prep_research:
>
> description: \"Research and compile prep materials\"
>
> model: reasoner
>
> prompt_template: prompts/prep_research.md
>
> output_schema: schemas/prep_output.json
>
> tools: \[web_search, email_read, notes_read, fs_read\]

Prompt templates are externalized as files, enabling per-model tuning.
Different models may use different prompt formats (e.g., Llama 3.1 vs.
Mistral), and templates can include few-shot examples that accumulate
over time from the correction log.

**Schema versioning:** Output schemas use semantic versioning (e.g.,
task_parse_output_v2.json). When a schema changes, the orchestrator
handles both old and new formats during the transition period.

**5.5 Task Intelligence**

**5.5.1 Natural Language Task Parsing**

When the user sends a message like "Get oil change before end of month,"
the input parser extracts:

-   Title: "Get oil change"

-   Deadline: End of current month (soft deadline)

-   Domain: Personal (inferred from automotive context)

-   Priority: 2 initially (flexible, no urgency keywords)

-   Estimated duration: 60--90 minutes (inferred from task type)

**5.5.2 Dynamic Priority Escalation**

Priority is not static. The scheduler re-evaluates priority daily based
on:

-   **Deadline proximity:** As a soft deadline approaches, priority
    increments. A task due "end of month" starts at priority 2 but
    escalates to 4 by the last week.

-   **Reschedule count:** Each reschedule adds +0.5 to priority score.
    After 3 reschedules, the task is flagged for the user's attention.

-   **Dependency chains:** If downstream tasks are waiting, the blocking
    task's priority increases.

-   **User override:** The user can always manually set priority, which
    locks it from auto-adjustment.

-   **Learned preferences:** The preference engine may apply priority
    adjustments based on patterns extracted from correction history (see
    Section 9).

**5.5.3 Task Complexity Assessment**

-   **Simple (\< 30 min, no dependencies):** Auto-schedule without
    interrogation. Examples: oil change, pay bill, send invoice.

-   **Medium (30 min--2 hours, may have dependencies):** Schedule and
    optionally flag for prep work. Examples: research restaurants, draft
    email.

-   **Complex (2+ hours, likely has subtasks):** Route to PM agent for
    interrogation and decomposition. Examples: refactor module, build
    feature, plan event.

**5.6 Task Domains**

  ------------ --------------------- ---------------- ----------------------
  **Domain**   **Scheduling Window** **Priority       **Notes**
                                     Defaults**       

  Personal     Evenings (5--8pm),    Standard (1--3)  Flexible scheduling,
               Weekends                               can fill gaps

  Work         8am--5pm weekdays     Standard to High Respects work calendar
               (extends to 7pm if    (2--5)           blocks
               needed)                                

  Family       Evenings, Weekends,   High for         Never
               Baby time blocks      child-related    auto-deprioritize
                                     (3--5)           child care tasks
  ------------ --------------------- ---------------- ----------------------

**6. Scheduling Engine**

**6.1 Calendar Integration**

Google Calendar is the single source of truth for the user's time. The
assistant has read-write access to the personal calendar and read access
to work and family calendars. All three calendars are Google Calendar
--- no ICS forwarding workarounds needed.

**6.1.1 Calendar Sync Strategy**

Donna uses a polling-based sync with change detection. The scheduler
polls Google Calendar every 5 minutes (configurable). On each poll, it
compares the current calendar state against its local mirror stored in
SQLite.

Donna-managed events are tagged with Google Calendar extended
properties:

> extendedProperties.private:
>
> donnaManaged: \"true\"
>
> donnaTaskId: \"\<task-uuid\>\"

These are invisible to the user in the Calendar UI but readable by the
API. This allows the system to distinguish its own events from
user-created ones.

When the user modifies a Donna-managed event directly in Google
Calendar:

-   **Time change detected:** Treated as an implicit reschedule. Task's
    scheduled_start updated in SQLite. reschedule_count incremented.
    Logged as a correction (feeds preference learning, e.g., "Nick
    always moves morning tasks to afternoon"). No notification sent
    (user already knows, they made the change).

-   **Event deleted:** Task moved back to backlog status. User notified
    on next interaction: "I noticed you removed the calendar event for
    \[task\]. Want me to reschedule it or leave it in your backlog?"

-   **User creates non-Donna event that conflicts:** Donna yields to
    user-created events. Conflict resolution rules apply (Section
    6.1.2). The Donna-managed event is auto-shifted to the next
    available slot.

**6.1.2 Conflict Resolution Rules**

  --------------------- ------------------------- -----------------------
  **Conflict Type**     **Resolution**            **Notification**

  New meeting overlaps  Auto-shift task to next   None unless priority
  scheduled task        available slot            4--5, then notify user

  Two meeting           Flag user immediately     SMS or app notification
  invitations at same                             with options
  time                                            

  High-priority vs      Auto-replace, reschedule  Include in daily digest
  low-priority in same  lower-priority            
  slot                                            

  Task runs over        Auto-extend and           Notify if it impacts a
  estimated time        cascade-shift subsequent  hard-deadline task
                        tasks                     

  User cannot complete  Accept reschedule or      Confirm new time via
  a task                auto-find next slot       same channel
  --------------------- ------------------------- -----------------------

**6.2 Time Constraints**

  ---------------- ------------------------ ------------------------------
  **Time Block**   **Hours**                **Task Types Allowed**

  Work             8:00 AM -- 5:00 PM       Work domain tasks, meetings
                   (weekdays)               

  Extended Work    5:00 PM -- 7:00 PM       Work overflow, side projects
                   (weekdays, optional)     

  Personal Time    5:00 PM -- 8:00 PM       Personal tasks, R&R, projects,
                                            study

  Baby Time        Per calendar blocks      Family tasks only; never
                                            schedule other work

  Food             Per calendar blocks      Protected; no tasks scheduled

  Emergency Work   10:00 PM -- 12:00 AM     Only high-priority tasks user
                   (user-activated)         explicitly opens

  Weekends         6:00 AM -- 8:00 PM       Personal and family tasks.
                                            User reschedules freely.

  Blackout         12:00 AM -- 6:00 AM      No scheduling, no
                   (always)                 notifications, no contact

  Quiet Hours      8:00 PM -- 6:00 AM       No new scheduling. Urgent
                   (default)                (priority 5) only.
  ---------------- ------------------------ ------------------------------

**6.3 Scheduling Algorithm**

-   Weekly Planning (Monday mornings): Generate a proposed week plan.
    Present to user for review. Lock hard-deadline items first, then
    fill with flexible tasks.

-   Daily Recalculation (6:00 AM): Recalculate today's schedule based on
    previous day's completion, new tasks, and calendar changes.

-   Real-time Adjustment: When a new task arrives or is rescheduled,
    re-evaluate only affected slots, not the entire week.

-   Minimize Rescheduling: Prefer inserting new tasks into genuinely
    empty slots before displacing existing tasks. When displacement is
    necessary, move the lowest-priority, most-flexible task.

-   Get It Done Bias: Default to scheduling tasks as soon as possible
    while respecting constraints. Do not push tasks to "someday."

**7. Sub-Agent System**

**7.1 Agent Architecture**

The sub-agent system follows a hierarchical structure with the
Orchestrator at the top and specialized agents below. All agents
communicate through a shared message bus and write outputs to the task
database.

**7.1.1 Agent Hierarchy**

The Orchestrator (core process, not a sub-agent) receives all incoming
tasks and determines routing. Runs on the local LLM when available, with
Claude API fallback.

  --------------- ---------------------- ---------------- -----------------------
  **Agent**       **Responsibilities**   **Tool Access**  **Autonomy Level**

  Scheduler Agent Calendar management,   Google Calendar  High --- auto-schedules
                  time slot              API              priority 1--3 tasks
                  optimization,          (read-write),    
                  rescheduling,          Task DB          
                  reminders, weekly      (read-write)     
                  planning                                

  Research / Prep Web research,          Web search       High --- runs
  Agent           information            (MCP), Gmail     autonomously when prep
                  compilation, resource  (read-only),     flagged. Results
                  gathering before       Local filesystem delivered via email.
                  flagged tasks          (MCP read),      
                                         GitHub (MCP      
                                         read)            

  Project Manager Task decomposition,    Task DB          Medium --- can
  Agent           requirements           (read-write),    decompose and route,
                  assessment,            all other agents must confirm
                  interrogation, work    (dispatch)       requirements with user
                  packaging for other                     before dispatching
                  agents                                  

  Coding Agent    Code generation, file  Local filesystem Low --- produces output
                  editing, project       (MCP sandboxed   for review. Never
                  scaffolding            read-write),     pushes to main. Never
                                         GitHub (MCP      deletes without backup.
                                         read-write),     
                                         Claude Code CLI  

  Communication / Email drafts, message  Gmail (draft     Low --- always creates
  Drafting Agent  drafts, document       only; send       drafts. Never sends
                  creation               behind feature   externally without
                                         flag),           explicit approval.
                                         Docs/markdown    
                                         (write),         
                                         Discord/Slack    
                                         (specific        
                                         channels only)   
  --------------- ---------------------- ---------------- -----------------------

**7.2 Agent Execution Flow**

-   Orchestrator receives task and routes to PM Agent for assessment.

-   PM Agent evaluates completeness. If requirements are missing, sends
    targeted questions (not open-ended). Example: "For the Module A
    refactor, I need to know: (1) which API endpoints are affected,
    and (2) should backward compatibility be maintained?"

-   User responds. PM Agent updates the task with new information.

-   PM Agent packages the task with full context, requirements,
    acceptance criteria, and file references.

-   PM Agent dispatches to the appropriate execution agent.

-   Execution agent works. Progress logged to activity log.

-   On completion, user receives summary via email + notification.
    Output available for review.

**7.3 Agent Safety Constraints**

These constraints are non-negotiable and enforced at the system level,
not reliant on agent prompting:

  ------------------------- ---------------------------------------------
  **Constraint**            **Enforcement**

  No sending emails to      Gmail API scoped to draft-only by default.
  external addresses        Send scope gated behind feature flag
                            (disabled by default). Enabling requires
                            config change + OAuth re-authorization with
                            broader scope.

  No deleting files         Filesystem access is append/modify only.
                            Deletes require explicit user command through
                            UI.

  No pushing to             GitHub API restricts push to feature
  main/production branches  branches. Branch protection at GitHub level.

  No external purchases or  No payment APIs integrated. No browser
  financial transactions    automation for e-commerce.

  No modifying              Scheduling agent only creates/modifies events
  manually-created calendar tagged as donnaManaged: true.
  events                    

  Backup before code        Coding agent creates git stash or branch
  changes                   backup before any file modification.

  Agent timeout enforcement Each invocation has a configurable timeout
                            (default 10 min coding, 5 min research).
                            Timeout triggers user notification.
  ------------------------- ---------------------------------------------

**Safety-first principle:** All agents start with minimal autonomy.
Constraints are relaxed only after reviewing logged agent performance
and explicitly updating the agent's configuration. The system errs on
the side of requiring user confirmation rather than acting autonomously.

**8. Local LLM Tool Use Progression**

This section defines the phased approach to expanding local LLM
capabilities beyond text-in/text-out processing. This work begins after
the RTX 3090 is acquired and the local model has been validated on basic
parsing tasks. Each stage requires passing evaluation thresholds before
progressing.

**8.1 Stage 1: Read-Only Tools, Single Call**

**Timeline:** First month after local LLM deployment

**Tools available:** task_db_read, calendar_read

**Purpose:** Context enrichment during parsing. Examples: checking if a
task already exists (deduplication), resolving "before my meeting" to an
actual time by reading the calendar.

**Evaluation:** Use the offline evaluation harness (Section 4.5) to
validate tool use accuracy against test fixtures. Additionally, enable
shadow mode (Section 4.4) with Claude as the shadow to monitor
production quality. Measure: did the model call the right tool with the
right parameters? Did it incorporate the result correctly? Log every
invocation.

**Promotion threshold:** 90%+ accuracy on tool selection and parameter
correctness over 100+ samples.

**8.2 Stage 2: Conditional Tool Use**

**Timeline:** Second month after local LLM deployment

**Challenge:** The model must decide whether to use a tool, not just use
it correctly. Input "buy milk" needs no tool call; "buy milk before my
3pm meeting" needs calendar_read.

**Evaluation:** Log every case where the model calls a tool
unnecessarily or fails to call one when needed. Both false positive and
false negative tool calls are tracked.

**Promotion threshold:** 85%+ precision and recall on tool use decisions
over 100+ samples.

**8.3 Stage 3: Write Tools with Guardrails**

**Timeline:** Third month, only if Stage 2 performance is solid

**Tools available:** task_db_write (create tasks directly)

**Guardrails:** The model proposes a write operation; the orchestrator
validates against the task schema before executing. Malformed entries
are rejected and logged. The model never writes to calendar or triggers
notifications directly --- those always go through the orchestrator's
validation layer.

**Evaluation:** Compare model-proposed task entries against what a human
(or Claude) would have created from the same input.

**8.4 Tool Execution Architecture**

The model never directly calls tools (whether MCP or internal API). The
flow is: model outputs a tool call request → orchestrator validates the
request (is this tool allowed for this task type? are the parameters
well-formed?) → orchestrator executes via the appropriate integration
module → result is fed back to model. This validation layer is
model-agnostic --- the same path whether the request comes from the
local LLM or Claude.

Tool access per task type is defined in the task type registry (Section
5.4). A task type configured with tools: \[calendar_read\] cannot result
in a task_db_write call, regardless of what the model requests. This is
enforced at the orchestrator level.

**9. User Preference Learning**

The preference learning system adapts to user behavior without model
fine-tuning. It operates by logging corrections, extracting patterns
from those corrections, and applying learned rules to future processing.
All learned preferences are transparent, editable, and reversible.

**9.1 Correction Logging**

When the user corrects a system output (e.g., changes a task's domain,
priority, or scheduled time), the correction is logged:

  ----------------- ---------- -------------------------------------------
  **Field**         **Type**   **Description**

  id                UUID       Unique correction identifier

  timestamp         DateTime   When the correction was made

  user_id           String     Who made the correction

  task_type         String     Which task type was wrong (e.g.,
                               parse_task)

  task_id           UUID       The specific task that was corrected

  input_text        String     Original natural language input

  field_corrected   String     Which field was changed (domain, priority,
                               etc.)

  original_value    String     What the system produced

  corrected_value   String     What the user changed it to

  rule_extracted    UUID?      Link to extracted rule, if one was created
  ----------------- ---------- -------------------------------------------

**9.2 Rule Extraction**

Rule extraction runs on a configurable schedule (default: weekly) or on
demand. It batches recent corrections and sends them to Claude API for
pattern analysis. Claude identifies recurring patterns and outputs
structured rules.

Example extracted rule:

> {
>
> \"rule\": \"Tasks mentioning vehicle/car/automotive → domain:
> personal\",
>
> \"confidence\": 0.9,
>
> \"supporting_corrections\": \[\"uuid1\", \"uuid3\", \"uuid7\"\],
>
> \"rule_type\": \"domain_override\",
>
> \"condition\": {\"keywords\": \[\"car\", \"oil change\", \"tire\",
> \"vehicle\"\]},
>
> \"action\": {\"field\": \"domain\", \"value\": \"personal\"}
>
> }

**9.3 Learnable Preference Types**

-   **Domain overrides:** Keyword-based rules mapping task content to
    domains. Highly reliable, accumulate quickly. "Anything about cars
    is always personal."

-   **Priority adjustments:** Source-based or entity-based rules. "Tasks
    from \[boss name\] are always priority 4 minimum."

-   **Scheduling preferences:** Extracted from reschedule patterns.
    "Nick never does deep work before 10am." "Nick always reschedules
    Friday afternoon tasks to Monday."

-   **Notification preferences:** Extracted from response patterns.
    "Nick ignores app notifications but responds to SMS within 10
    minutes."

-   **Few-shot example accumulation:** Well-handled corrections become
    few-shot examples in prompt templates. The prompt_template config
    supports an examples_file field pointing to a JSON file of labeled
    examples that gets prepended to the prompt.

**9.4 Preference Application**

Preferences are applied after initial model processing as a
post-processing step. The model's output is the first draft; the
preference engine is the editor.

Application order: model produces structured output → preference engine
checks applicable rules → matching rules override relevant fields →
orchestrator uses the final output for scheduling/routing.

**9.5 Transparency & Control**

All learned preferences are stored as readable, editable entries. The
user can view, edit, disable, or delete any preference at any time.
Example display:

> Active Preferences:
>
> 1\. Car/vehicle tasks → domain: personal (learned from 5 corrections)
>
> 2\. Tasks from \[boss\] → priority: 4 minimum (learned from 3
> corrections)
>
> 3\. Never schedule personal tasks before 10am (learned from 8
> reschedules)
>
> \[edit\] \[disable\] \[delete\]

This transparency is a deliberate design choice. The system adapts in a
way that is inspectable and reversible, which builds trust over time. If
a rule causes corrections in the opposite direction, it is auto-disabled
and flagged for user review.

**10. Input Channels & Task Capture**

The task capture system must be frictionless. The user's primary failure
mode is not writing tasks down, so every channel must accept natural
language with zero required structure.

**10.1 Input Channel Matrix**

  --------------- ---------------------- --------------- ----------------------
  **Channel**     **Implementation**     **Cost**        **Priority**

  Discord Bot     Bot in dedicated       Free            P0 --- cross-device,
                  server/channel.        (self-hosted)   already installed
                  discord.py with                        
                  message intents.                       
                  Self-hosted on Linux                   
                  server.                                

  SMS / Text      Twilio number. Parsed  \$1--2/mo       P0 --- fastest capture
                  by LLM.                (Twilio)        

  Desktop App     Flutter desktop.       Free            P1 --- primary
  (Chat)          WebSocket to           (self-hosted)   workstation interface
                  orchestrator.                          

  Web/Mobile App  Flutter web/PWA hosted Firebase free   P1 --- mobile access
                  on Firebase.           tier            

  Email           Dedicated email alias. Free            P2 --- capture from
  Forwarding      Forwarded emails                       email threads
                  parsed for tasks.                      
  --------------- ---------------------- --------------- ----------------------

**10.2 Discord Integration Detail**

A dedicated Donna category in the existing Linux server alert Discord
server with multiple channels:

-   #donna-tasks: Task capture and responses. Multi-turn PM Agent
    interrogations use Discord threads on the original task message.
    Thread ID provides natural context association.

-   #donna-digest: Morning and evening digests. Clean, chronological
    record.

-   #donna-agents: Agent activity notifications, completion summaries,
    cost per task.

-   #donna-debug: System health alerts, cost warnings, error
    notifications, circuit breaker status.

A full bot (not just webhooks) is required for bidirectional
communication --- Donna sends messages AND reads user responses. Use
discord.py with the message intent enabled so the bot can read replies
and thread responses.

Discord's 2000-character message limit is handled via message splitting
or embeds (which support richer formatting and up to 6000 characters
across fields). Morning digests use embeds for structured presentation.

**10.3 Conversation Context Management**

Multi-turn interactions (PM Agent interrogation, clarification requests)
require context tracking across messages. The approach differs by
channel.

**10.3.1 Discord: Thread-Based Context**

When an agent needs follow-up information, it opens a Discord thread on
the original task message. The user replies in-thread, and the bot
associates responses by thread ID. No custom conversation context store
needed for this channel. Threads provide natural grouping, history, and
context.

**10.3.2 SMS/Email: Conversation Context Store**

SMS (Twilio) has no thread concept. When Donna sends a PM Agent question
via SMS and the user responds hours later, the system needs to route
that response correctly. A conversation_context table in SQLite tracks
active interrogations:

  -------------------- ---------- -------------------------------------------
  **Field**            **Type**   **Description**

  id                   UUID       Context identifier

  user_id              String     User being interrogated

  channel              Enum       sms \| email \| slack

  task_id              UUID       Task being interrogated

  agent_id             String     Which agent initiated the interrogation

  questions_asked      JSON       Array of questions sent to user

  responses_received   JSON       Array of responses received

  status               Enum       active \| expired \| completed

  created_at           DateTime   When interrogation started

  expires_at           DateTime   Default: 24 hours from creation

  last_activity        DateTime   Last message sent or received
  -------------------- ---------- -------------------------------------------

Routing logic for incoming SMS messages:

-   Check: is there an active conversation context for this user on the
    SMS channel?

-   If yes: route the message to that context's agent for processing.

-   If multiple active contexts exist (rare but possible): ask the user
    to disambiguate: "I have questions about two tasks. Which are you
    responding about: (1) \[task A title\] or (2) \[task B title\]?"

-   If no active context: treat the message as new task input (normal
    parsing pipeline).

Contexts expire after 24 hours of inactivity. On expiration, the agent
re-prompts: "Hey, I still need info about \[task\]. \[original
question\]." This re-prompt creates a new context with a fresh TTL.

For email: use email threading (In-Reply-To headers). If the user
replies to a Donna email, the threading metadata maps directly to the
originating task.

**10.4 Input Parsing Pipeline**

-   Receive raw text from input channel with metadata (source,
    timestamp, user context).

-   LLM parses input into structured task fields (title, deadline,
    domain, priority, etc.).

-   Preference engine applies learned rules to override/adjust parsed
    fields.

-   Deduplication check against existing tasks (Section 5.3). If
    duplicate detected, notify user and ask for merge/update.

-   Complexity assessment. Simple tasks auto-scheduled; complex tasks
    routed for interrogation.

-   Confirmation message sent back on same channel: "Got it. 'Oil
    change' scheduled for Saturday 10am. Priority 2."

**10.5 Proactive Task Capture**

-   **End-of-meeting prompt:** If calendar shows a meeting just ended:
    "Your standup just ended. Any new tasks or action items?"

-   **Evening check-in:** At configurable time (e.g., 7pm): "Anything
    you need to capture before tomorrow?"

-   **Stale task detection:** If a task has been in backlog 7+ days with
    no scheduled time: "This has been sitting unscheduled for a week.
    Should I schedule it or archive it?"

**11. Notification & Escalation System**

**11.1 Notification Types & Channels**

  ---------------- ------------- --------------- -------------------------------
  **Notification   **Channel**   **Timing**      **Content**
  Type**                                         

  Morning Digest   Email         6:30 AM daily   Full day schedule, task list,
                                                 prep results, agent activity,
                                                 carry-overs, system health
                                                 summary

  Task Reminders   App push /    15 min before   Task name, duration, prep
                   Discord       start           materials available

  Overdue Nudge    SMS           30 min after    Direct question: finish or
                                 scheduled end   reschedule?

  Agent            Email + App   When PM agent   Specific targeted questions
  Interrogation                  needs info      with context

  Agent Completion Email         When agent      Summary, thought process,
                                 finishes        output location, cost

  End-of-Day       Email         5:30 PM         Completed, rescheduled, agent
  Digest                         weekdays        activity, daily cost

  Budget Alert     SMS + Email   Daily \$20      Spend breakdown, recommendation
                                 threshold or    to continue/pause
                                 90% monthly     

  Conflict Alert   SMS + App     Immediately on  Description, proposed
                                 detection       resolution options

  Urgent           Phone Call    Critical        Brief TTS message via Twilio
  Escalation       (TTS)         deadline miss   with callback option
                                 or system       
                                 failure         
  ---------------- ------------- --------------- -------------------------------

**11.2 Escalation Tiers**

-   Tier 1 --- App notification / Discord message. Wait 30 minutes.

-   Tier 2 --- SMS text message. Wait 1 hour.

-   Tier 3 --- Email with "ACTION REQUIRED" subject. Wait 2 hours.

-   Tier 4 --- Phone call (TTS). Only for priority 5 tasks or budget
    emergencies. Maximum 1 call per day.

Escalation resets when the user acknowledges any message on any channel.
If the user responds "busy, will handle later," the system backs off for
2 hours.

**12. Service Integrations**

**12.1 Integration Matrix**

  --------------- -------------------- ---------------------------- -----------------------
  **Service**     **Access Level**     **Integration Pattern**      **Tools / Methods**

  Gmail           Read-only (send      Direct API                   email_read,
                  behind feature flag) (google-api-python-client)   email_search,
                                                                    draft_create

  Google Calendar Read-Write           Direct API                   calendar_read,
                  (personal); Read     (google-api-python-client)   calendar_write,
                  (work, family)                                    calendar_delete

  GitHub          Read-Write (feature  MCP (FastMCP)                github_read,
                  branches only)                                    github_write,
                                                                    github_issues

  Notes (Local    Read-Write           MCP (FastMCP)                notes_read, notes_write
  Markdown)                                                         

  Local           Read-Write           MCP (FastMCP)                fs_read, fs_write,
  Filesystem      (sandboxed to                                     fs_list
                  /donna/workspace/)                                

  Discord         Read-Write (Donna    Direct API (discord.py)      discord_send,
                  channels only)                                    discord_read, thread
                                                                    management

  Twilio          Write (outbound      Direct API (twilio-python)   sms_send, phone_call
  (SMS/Voice)     only)                                             

  Web Search      Read                 MCP (FastMCP)                search_web (SearXNG
                                                                    self-hosted or API)

  SQLite Task DB  Read-Write           Direct API (aiosqlite)       Internal orchestrator
                                                                    access, no MCP overhead

  Supabase        Write (sync replica) Direct API (supabase-py)     Background
  Postgres                                                          write-through sync
  --------------- -------------------- ---------------------------- -----------------------

**13. Cost Management & Monitoring**

**13.1 Budget Rules**

  ------------------ ---------------- ------------------------------------
  **Rule**           **Threshold**    **Action**

  Daily Spend Alert  \$20 (20% of     Pause all autonomous agent work.
                     monthly budget)  Notify user via SMS with progress
                                      summary.

  Task Cost          Estimated cost   Notify user before execution.
  Notification       \> \$5 for a     Require approval to proceed.
                     single task      

  Monthly Warning    90% of \$100     Pause work. Send detailed report.
                     monthly budget   Ask for budget increase approval.

  Budget Increase    User approves    Increase for current month only.
  Approved           additional funds Reset to \$100 on the 1st.

  Budget Increase    User denies      Remain paused on agent work.
  Denied                              Continue local LLM + scheduling
                                      (zero API cost when available).
  ------------------ ---------------- ------------------------------------

**13.2 Cost Tracking**

Every Claude API call is tracked via the invocation_log (Section 4.3).
Aggregated metrics include: cost per agent, cost per task, cost per task
type, daily/weekly/monthly totals, and projected monthly spend based on
current velocity.

**Cost optimization loop:** Weekly, the system reviews API calls and
identifies patterns that could be offloaded to the local LLM. Shadow
mode comparison data directly feeds this analysis.

**13.3 Phase 1 Cost Projection**

During Phase 1 (Claude API only, no local LLM), all parsing and
classification runs on Claude. Projected costs based on typical usage:

  ------------------ --------------- ---------------- --------------------
  **Operation**      **Daily Volume  **Tokens/call    **Daily Cost
                     (est.)**        (est.)**         (est.)**

  Task parsing       10--20 tasks    \~500 in / \~200 \$0.10--\$0.30
                                     out              

  Priority           10--20 tasks    \~300 in / \~100 \$0.05--\$0.15
  classification                     out              

  Morning digest     1               \~2000 in /      \$0.02
                                     \~500 out        

  Prep work research 2--5 tasks      \~3000 in /      \$0.30--\$0.75
                                     \~1000 out       

  Agent work (Phase  Variable        Variable         \$1--\$5
  2+)                                                 
  ------------------ --------------- ---------------- --------------------

Phase 1 daily cost (no agents): approximately \$0.50--\$1.20/day, or
\$15--\$36/month. Well within the \$100 budget with substantial headroom
for agent work in later phases.

**14. Observability & Logging Architecture**

Observability is a Phase 1 deliverable, not an afterthought. Every Donna
service emits structured JSON logs to a centralized pipeline. A
dedicated logging database and searchable dashboard ensure that
debugging any issue requires seconds, not hours. The goal: no issue
should ever require SSH and grep.

**14.1 Logging Framework**

All Python services use structlog with JSON output and contextvars for
async context propagation. Every incoming request binds correlation_id,
user_id, channel, and task_id as context variables that automatically
appear in all downstream log entries. This enables full request tracing
across services.

**14.2 Log Levels**

  ----------- --------------------- ----------------------------------------
  **Level**   **When to Use**       **Examples**

  DEBUG       Detailed diagnostics. Full prompt contents, API response
              Off in prod unless    bodies, dedup similarity scores,
              troubleshooting.      scheduler slot evaluation steps,
                                    preference rule matching details

  INFO        Normal operations.    Task created, state transitioned,
              The system is working reminder sent, digest generated,
              correctly.            calendar synced, agent dispatched,
                                    backup completed

  WARNING     Something unexpected  API retry triggered, confidence below
              but the system        threshold, reschedule count high,
              handled it.           preference rule auto-disabled, degraded
                                    mode activated

  ERROR       An operation failed   API call failed after all retries,
              but the system        schema validation rejected, agent timed
              continues running.    out, malformed user input couldn't be
                                    parsed

  CRITICAL    System-level failure  Circuit breaker activated, database
              requiring immediate   corruption detected, orchestrator crash,
              attention.            NVMe space exhausted, all retries
                                    exhausted on critical path
  ----------- --------------------- ----------------------------------------

**14.3 Logging Database**

Dedicated SQLite database (donna_logs.db) on NVMe. Separate from the
task database to avoid contention between high-volume log writes and
task query performance.

**14.3.1 Log Table Schema**

  ---------------- ------------ -------------------------------------------
  **Field**        **Type**     **Purpose**

  id               INTEGER PK   Auto-incrementing row ID

  timestamp        TEXT ISO     When the event occurred (UTC)
                   8601         

  level            TEXT         DEBUG, INFO, WARNING, ERROR, CRITICAL

  service          TEXT         Which service emitted: orchestrator,
                                mcp_server, discord_bot, scheduler,
                                notification, agent_worker, sync

  component        TEXT         Sub-component: input_parser, calendar_sync,
                                state_machine, preference_engine, etc.

  event_type       TEXT         Machine-readable event name (e.g.,
                                task.created, api.call.failed,
                                agent.timeout)

  message          TEXT         Human-readable log message

  correlation_id   TEXT         Unique ID for tracing a single request/task
                                across all services and log entries

  task_id          TEXT?        Associated task UUID

  user_id          TEXT?        User who triggered the action

  agent_id         TEXT?        Agent type if emitted from agent worker

  channel          TEXT?        discord, sms, email, app, system

  duration_ms      INTEGER?     Duration for timed operations (API calls,
                                agent runs, scheduling cycles)

  cost_usd         REAL?        API cost if this is a model call

  error_type       TEXT?        Exception class name for ERROR/CRITICAL

  error_trace      TEXT?        Full Python stack trace for ERROR/CRITICAL

  extra            TEXT (JSON)  Arbitrary additional structured context
  ---------------- ------------ -------------------------------------------

Indexes on: timestamp, level, service, event_type, correlation_id,
task_id, error_type. WAL mode enabled for concurrent read/write.

**14.3.2 Retention Policy**

-   DEBUG logs: 7 days retention (high volume, diagnostic only).

-   INFO logs: 30 days retention.

-   WARNING logs: 90 days retention.

-   ERROR and CRITICAL logs: 1 year retention (never auto-deleted during
    that period).

-   Invocation logs (Section 4.3): permanent retention (needed for cost
    analysis, evaluation, and preference learning).

A nightly cron job prunes expired logs based on level. VACUUM runs
weekly to reclaim disk space.

**14.4 Event Types**

Event types are hierarchical and machine-parseable. The first segment
identifies the domain:

-   task.\*: task.created, task.state_changed, task.dedup_detected,
    task.overdue, task.escalation_triggered

-   api.\*: api.call.started, api.call.completed, api.call.failed,
    api.call.retried, api.circuit_breaker.opened,
    api.circuit_breaker.closed, api.degraded_mode.activated

-   agent.\*: agent.dispatched, agent.progress, agent.completed,
    agent.failed, agent.timeout, agent.interrogation.sent,
    agent.interrogation.response_received

-   scheduler.\*: scheduler.weekly_plan, scheduler.daily_recalc,
    scheduler.slot_assigned, scheduler.conflict_detected,
    scheduler.calendar_sync.completed,
    scheduler.calendar_sync.user_modification

-   notification.\*: notification.sent, notification.failed,
    notification.escalated, notification.acknowledged,
    notification.blackout_blocked

-   preference.\*: preference.correction_logged,
    preference.rule_extracted, preference.rule_applied,
    preference.rule_disabled

-   system.\*: system.startup, system.shutdown, system.health_check,
    system.backup.completed, system.backup.failed,
    system.migration.applied

-   cost.\*: cost.daily_threshold, cost.monthly_warning,
    cost.agent_paused, cost.budget_increase

-   sync.\*: sync.supabase.push, sync.supabase.failed,
    sync.keepalive.sent

**14.5 Per-Service Logging Detail**

**14.5.1 Orchestrator**

Logs every task state transition (from/to/trigger/side effects), every
routing decision (task type → model alias → resolved provider), every
preference rule application, and every cost threshold event. Each
incoming message gets a correlation_id that follows the task through
parsing, scheduling, agent dispatch, and notification.

**14.5.2 FastMCP Server**

Logs every tool invocation with: calling agent, tool name, parameters
(sanitized --- no credentials ever logged), result summary (truncated to
prevent log bloat), and latency. Rate limit hits and authentication
failures logged at WARNING level.

**14.5.3 Agent Workers**

Each agent logs: task received (with context summary), tools used
(invocation count, latency per tool), intermediate reasoning steps
(DEBUG level only), output summary, total duration, and cost. Agent
failures include full error context and the state of work-in-progress so
debugging doesn't require reproducing the failure.

**14.5.4 Notification Service**

Logs every outbound message: channel, recipient identifier (not full
content for privacy), delivery status, escalation tier. Failed
deliveries trigger automatic retry with separate log entries.
Blackout-blocked messages logged at INFO level (not an error, expected
behavior).

**14.5.5 Scheduler**

Logs each scheduling cycle: slots evaluated, conflicts detected, tasks
moved, calendar sync results, sync delta (events
added/modified/deleted). Performance timing on each cycle to detect
scheduling slowdowns as task volume grows.

**14.5.6 Discord Bot**

Logs: messages received (channel, thread context, is_reply), messages
sent (channel, content length), thread creation/closure, connection
status changes, reconnection events. Full message content logged at
DEBUG level only.

**14.6 Log Pipeline**

Phase 1 architecture uses a dual-write approach:

-   Each service writes structured JSON logs to stdout. Docker captures
    stdout via the json-file log driver.

-   Promtail (deployed in donna-monitoring.yml) tails Docker container
    logs and ships them to Loki.

-   Grafana queries Loki for the real-time dashboard (Section 15).

-   Simultaneously, a lightweight log collector module in the
    orchestrator process writes logs to the SQLite log database for
    programmatic access, retention management, and correlation analysis.

This dual-write ensures logs are available both in the real-time Grafana
dashboard (via Loki, optimized for search and visualization) and in the
persistent SQLite store (for long-term queries, automated analysis, and
evaluation data).

**15. Development Dashboard**

The development dashboard is a Phase 1 deliverable deployed via
donna-monitoring.yml. It provides real-time visibility into system
behavior during development and ongoing operations. Built on Grafana +
Loki (both free, open-source, Docker-deployable, minimal resource
footprint).

**15.1 Dashboard Panels**

**15.1.1 System Health Overview**

-   Service status: green/yellow/red indicators for each Docker
    container (orchestrator, MCP server, Discord bot, notification
    service).

-   Last successful operations: timestamp of last calendar sync, last
    Supabase sync, last morning digest, last backup.

-   NVMe disk usage: total capacity, task DB size, log DB size, backups
    size, workspace size.

-   Memory and CPU: per-container resource usage (via Docker stats).

-   Circuit breaker state: open/closed/half-open with timestamp of last
    state change.

**15.1.2 Task Pipeline**

-   Tasks created today/this week (by channel, by domain).

-   State distribution: count of tasks in each state (backlog,
    scheduled, in_progress, blocked, done, cancelled).

-   Average time-to-schedule: from task creation to first scheduled
    slot.

-   Reschedule frequency: tasks rescheduled 3+ times highlighted.

-   Dedup hit rate: duplicate detections per day, false positive
    tracking.

-   Completion velocity: tasks completed per day/week, trend line.

**15.1.3 LLM & Cost**

-   API calls per hour/day (by task type, by model alias).

-   Token usage: input/output breakdown by task type.

-   Cost: daily/weekly/monthly spend, current burn rate, projected
    monthly total, budget remaining.

-   Latency: p50/p95/p99 response times by task type.

-   Error rate: failed API calls, retries triggered, circuit breaker
    activations.

-   Shadow mode comparison: side-by-side quality scores when shadow is
    active (Phase 3+).

**15.1.4 Agent Activity**

-   Active agents: what is currently running, on which task, elapsed
    time vs timeout limit.

-   Completed today/this week: task summaries with cost and duration.

-   Failed today: error summaries with expandable stack traces.

-   Cost per agent: breakdown showing which agent types consume the most
    budget.

**15.1.5 Notifications**

-   Messages sent today (by channel, by notification type).

-   Delivery failures: count and detail (channel, error reason).

-   Escalation events: tasks that required Tier 2+ escalation.

-   User response times: how quickly user acknowledges by channel (feeds
    notification preference learning).

**15.1.6 Error Exploration**

-   Recent errors: filterable table by service, component, event type,
    time range, and severity.

-   Error timeline: visualization of error frequency over time for spike
    detection.

-   Correlation trace: given a correlation_id, show the full lifecycle
    of that request across all services --- from input receipt through
    parsing, scheduling, agent dispatch, and notification.

-   Stack trace viewer: expandable error details with full Python
    traceback.

**15.1.7 Preference Learning**

-   Corrections per week: trend line.

-   Rules extracted: new rules created, rules auto-disabled.

-   Rule survival rate: percentage of rules still active after 30 days.

**15.2 Alerting**

Grafana alerting rules (shipped with donna-monitoring.yml
configuration):

-   Service down: any container unhealthy for \> 5 minutes → Discord
    #donna-debug webhook + SMS via Twilio.

-   High error rate: \> 10 errors in 5 minutes → Discord #donna-debug
    webhook.

-   Circuit breaker opened → Discord #donna-debug + SMS.

-   Budget threshold → Donna's own notification system handles this
    (Section 13). No separate Grafana alert needed.

-   NVMe disk usage \> 80% → Discord #donna-debug.

-   Supabase sync failure \> 1 hour → Discord #donna-debug.

-   No orchestrator heartbeat for 10 minutes → External watchdog
    (Section 17.1.2) handles this.

**15.3 Phase 4: Production Dashboard (Flutter)**

The Flutter app (Phase 4) provides a user-facing dashboard distinct from
the Grafana dev dashboard. It includes: calendar view with Donna-managed
events highlighted, task board (kanban-style), agent activity monitor,
cost summary, completion heatmap (GitHub contribution graph style), and
weekly planning interface. The Flutter app reads from Supabase, not
directly from SQLite.

**16. Data Architecture**

**16.1 Database Strategy**

-   **Primary:** SQLite on NVMe. Two databases: donna_tasks.db (task
    data, corrections, preferences) and donna_logs.db (structured logs,
    invocation logs). WAL mode for both. Sub-millisecond reads.

-   **Replica:** Supabase Postgres. Async write-through sync from
    SQLite. Free tier with keep-alive cron (ping every 3 days to prevent
    7-day inactivity pause). Upgrade to Pro (\$25/month) at Phase 4 or
    multi-user onboarding.

-   **Evaluation:** donna_eval.db. Model session results. Separate to
    avoid cluttering task/log databases.

**16.2 Supabase Sync Strategy**

The orchestrator pushes task changes to Supabase on write (async,
non-blocking). The Flutter app reads from Supabase. If Supabase is down,
the system keeps running --- SQLite is the source of truth. When
Supabase recovers, a full reconciliation sync runs.

Keep-alive: a cron job on the Linux server pings the Supabase REST API
every 3 days with a lightweight query (SELECT 1). This prevents the free
tier's 7-day inactivity pause and costs nothing.

Supabase free tier includes 500MB database storage, which is sufficient
for years of Donna task data. Upgrade to Pro (\$25/month, 8GB included
storage) when onboarding a second user or when the Flutter app goes live
and needs reliable 24/7 access.

**16.3 Backup Strategy**

**16.3.1 Backup Method**

Uses SQLite's .backup API (Python connection.backup()) for consistent
snapshots. Never file copy --- copying a WAL-mode SQLite database during
writes can produce corrupted backups.

**16.3.2 Schedule & Retention**

-   Daily at 3 AM (blackout hours, minimal activity): full backup of
    donna_tasks.db and donna_logs.db.

-   Retention: 7 daily backups, 4 weekly backups (Sunday), 3 monthly
    backups (1st of month).

-   Worst case storage: \~14 backups × 500MB = 7GB. Trivial on 1TB NVMe.

-   Off-server: weekly and monthly backups pushed to cloud storage
    (Google Cloud Storage free tier 5GB, or Backblaze B2 at \$0.005/GB
    --- \~\$0.04/month for expected volume).

**16.3.3 Recovery**

**RPO (Recovery Point Objective):** 24 hours maximum data loss (last
daily backup). Supabase replica provides secondary recovery path for
task data, reducing effective RPO to the sync interval (near-real-time
for write-through sync).

**Recovery procedure:** Stop orchestrator → copy backup to live database
path → restart. Orchestrator detects restored DB (version marker) and
triggers full Supabase re-sync. Documented in RECOVERY.md in the repo.

**Pre-migration backup:** Alembic migration runner automatically creates
a backup before applying any migration. If migration fails, the backup
is the rollback path.

**16.4 Data Classification**

  --------------------- ---------------------- ----------------------------
  **Classification**    **Storage**            **Access**

  Task metadata         SQLite (NVMe) +        Orchestrator, all agents, UI
  (titles, schedules,   Supabase (sync)        
  priorities)                                  

  Task content          SQLite primary,        Relevant agents only
  (descriptions, notes, Supabase sync          
  prep results)                                

  Credentials (API      Linux server only,     MCP server / integration
  keys, OAuth tokens)   encrypted (age/sops)   layer process only

  Agent outputs (code,  Local filesystem       User + relevant agent
  drafts, research)     (sandboxed, NVMe       
                        workspace)             

  Cost/usage logs       SQLite log DB (NVMe)   Orchestrator, dashboard

  System logs           SQLite log DB + Loki   Dev dashboard (Grafana)

  Correction log &      SQLite task DB (NVMe)  Orchestrator, preference
  learned preferences                          engine

  Sensitive personal    Never in               No agent access
  files                 assistant-accessible   
                        paths                  
  --------------------- ---------------------- ----------------------------

**17. Security & Privacy**

-   Principle of least privilege: each agent only has access to the
    tools it needs, defined in the task type registry. The Coding Agent
    cannot read emails. The Drafting Agent cannot modify the calendar.

-   No credentials in agent context: agents request tool calls via MCP
    or orchestrator. They never see raw API keys or tokens.

-   Sandboxed filesystem: agents can only read/write within
    /donna/workspace/. No access to home directory, system files, or
    other project folders.

-   Git safety: all code changes go to feature branches. Main/production
    branches have push protection at GitHub level.

-   Email safety: Gmail API scoped to read-only + draft by default. Send
    scope gated behind feature flag (disabled by default). Enabling
    requires explicit config change + OAuth re-authorization.

-   No external data exfiltration: agents cannot send data to arbitrary
    URLs. MCP server whitelists allowed outbound destinations.

-   Tool validation layer: all model tool call requests are validated by
    the orchestrator before execution. The model proposes; the
    orchestrator disposes.

-   Blackout enforcement: 12:00 AM -- 6:00 AM hard block on outbound
    messages enforced at the notification service level, not agent
    level.

-   Log sanitization: credentials, tokens, and sensitive data are never
    written to logs. API request/response bodies logged only at DEBUG
    level with sensitive fields redacted.

-   NVMe encryption: the dedicated NVMe volume uses LUKS encryption at
    rest. Decryption key stored in TPM or entered at boot.

**18. Resilience & Failure Handling**

**18.1 Health Monitoring**

**18.1.1 Layer 1: Docker Healthchecks**

Each Donna service in the compose files gets a healthcheck directive.
The orchestrator exposes an HTTP /health endpoint (lightweight aiohttp
handler on dedicated port). Docker polls every 30 seconds. Three
consecutive failures trigger container restart (restart:
unless-stopped).

The /health endpoint checks: SQLite reachable, Discord bot connected,
scheduler loop running, last Claude API health-check response \< 10
minutes old. Returns 200 if all pass, 503 with JSON body listing
failures.

**18.1.2 Layer 2: External Watchdog**

A separate lightweight process (Python script or bash cron job) runs
outside Docker. Every 5 minutes, checks docker inspect
\--format=\'{{.State.Health.Status}}\' donna-orchestrator. If the
container is unhealthy or stopped, sends alert via Twilio SMS or Discord
webhook (independent of the Donna bot). Catches the case where Docker
itself cannot restart the container (persistent crash loop, port
conflict, volume mount failure).

**18.1.3 Layer 3: Daily Self-Diagnostic**

Part of morning digest generation. Before generating the digest, the
orchestrator runs a self-check: DB integrity (PRAGMA integrity_check),
NVMe disk space, last successful calendar sync timestamp, last
successful Supabase sync timestamp, pending migration check, budget
status. Any issues are prepended to the morning digest so the user sees
them first thing.

**18.2 Acceptable Failures**

-   Task priority misclassification --- user corrects manually,
    correction feeds preference learning.

-   Duplicate reminders --- minor annoyance, no data loss.

-   Agent produces low-quality code --- user reviews before merging; no
    production impact.

-   Scheduling engine places task at suboptimal time --- user
    reschedules, pattern feeds preference learning.

-   Local LLM misroutes a task to Claude API --- costs slightly more but
    task completes correctly.

**18.3 Unacceptable Failures**

-   Missing a deadline reminder: system must never silently let a
    hard-deadline task expire without escalating to the user.

-   Sending emails to unintended recipients: email sending is
    architecturally blocked (draft-only default, feature flag for send).

-   Deleting files without backup: filesystem operations are
    append/modify only. Deletes require explicit user action.

-   Overwriting code without version control: all code changes are
    branched and stashed before modification.

-   Exceeding budget without notification: cost monitoring runs
    synchronously with every API call. Budget pauses enforced at
    orchestrator level.

-   Contacting user during blackout (12am--6am): notification service
    has a hard block on outbound messages.

-   Agent running indefinitely: configurable timeout. Timeout triggers
    user notification and agent_status = failed.

-   Learned preference causing repeated errors: auto-disabled and
    flagged for user review.

-   Silent service failure: must be detected within 10 minutes via
    Docker healthcheck + external watchdog.

**19. Testing Strategy**

Four testing layers ensure correctness and catch regressions at
appropriate cost levels.

**19.1 Layer 1: Unit Tests (Core Logic)**

Framework: pytest. Target: 90%+ coverage on scheduler time-slot
allocation, state transition validation, preference rule matching, and
dedup scoring. These are pure functions (input → output, no external
dependencies). Tests run in \< 1 second and catch regressions
immediately.

**19.2 Layer 2: Integration Tests (Service Boundaries)**

Test the orchestrator's interaction with SQLite, the MCP server's tool
execution, and the notification service's channel dispatch. Use real
SQLite (in-memory for speed) and mock external APIs (Google Calendar,
Discord, Twilio). Framework: pytest + pytest-asyncio + aioresponses (for
mocking HTTP). Target: every integration module has at least one test
verifying the request validation → execution → response cycle.

**19.3 Layer 3: LLM Output Evaluation**

The offline evaluation harness (Section 4.5) covers LLM output quality.
A small "smoke test" subset (3--5 Tier 1 fixtures per task type) runs as
part of CI to verify the parsing pipeline still works after code
changes. Budget: \~\$0.05 per CI run. Full evaluation runs are triggered
manually for model comparison.

**19.4 Layer 4: End-to-End Scenario Tests**

Simulate full user workflows: send a Discord message → task created in
SQLite → scheduled on Google Calendar → reminder sent at scheduled time
→ user marks complete. These hit real APIs (test Google Calendar, test
Discord channel) and are slow/expensive. Run weekly or before releases,
not on every commit.

**19.5 Test Data Management**

Maintain a tests/fixtures/ directory with sample tasks, calendar states,
user corrections, and expected outputs. Version-controlled alongside the
code. Same fixtures used by unit tests and evaluation harness.

**20. Implementation Phases**

The project is divided into four phases. Each phase builds on the
previous and delivers independently useful functionality. Do not proceed
to the next phase until the current one is stable and actively used
daily.

**Phase 1: Foundation (Weeks 1--4)**

*Goal: Task capture, basic scheduling, reminders, observability. Claude
API only. Solve the day-one problem.*

-   Set up Linux server Docker stack: donna-core.yml with orchestrator,
    integration layer, notification service.

-   Deploy donna-monitoring.yml: Grafana + Loki + Promtail for dev
    dashboard.

-   Build orchestrator service (Python asyncio) with SQLite task DB on
    NVMe including user_id field.

-   Implement structlog across all services; set up dedicated logging
    database (donna_logs.db).

-   Implement model abstraction layer with AnthropicProvider
    (OllamaProvider stubbed for later).

-   Implement structured invocation logging on every model call.

-   Implement API resilience layer: retries, degraded modes, circuit
    breaker, response validation.

-   Implement task lifecycle state machine (config-driven, loaded from
    task_states.yaml).

-   Define initial task types in task_types.yaml: parse_task,
    classify_priority, generate_digest.

-   Implement input parser via Claude API (natural language → task
    schema).

-   Implement task deduplication (two-pass: fuzzy + LLM semantic
    comparison).

-   Set up first input channel: Discord bot with dedicated category,
    channels, and thread-based context.

-   Integrate Google Calendar API (read-write on personal calendar, read
    on work/family).

-   Implement calendar sync strategy: polling, change detection,
    Donna-managed event tagging.

-   Build basic scheduling engine: auto-schedule in available slots,
    respect time blocks.

-   Implement reminder system: Discord notifications at scheduled times.

-   Implement overdue detection and nudge messages.

-   Morning digest via Discord (generated by Claude API in Donna
    persona).

-   Deploy Donna persona system prompt across all communications.

-   Configure Docker healthchecks for all services, external watchdog,
    daily self-diagnostic.

-   Configure Grafana dashboards: System Health, LLM & Cost, Task
    Pipeline, Error Exploration.

-   Set up SQLite backup automation (daily at 3 AM, retention rotation).

-   Set up Supabase project (free tier + keep-alive cron). Background
    write-through sync.

-   Set up Alembic for schema migration. Initial migration creates all
    tables.

-   Begin building tiered evaluation test fixtures (Tier 1 baseline
    through Tier 4 adversarial, version-controlled).

-   Implement unit tests for state machine, scheduler, preference
    matching, dedup logic.

-   Spot-check monitoring disabled (Claude evaluating itself is not
    useful).

**Phase 1 Deliverable:** User texts Donna on Discord to create tasks,
gets a morning digest, receives reminders, and gets nudged about overdue
items. Tasks auto-schedule around calendar events. All model calls
logged with cost tracking. Full observability via Grafana dashboard.
Backup running. Health monitoring active. Calendar sync operational.

**Phase 2: Intelligence & Communication (Weeks 5--7)**

*Goal: Smarter scheduling, multi-channel communication, prep work,
correction logging.*

-   Add SMS input/output via Twilio. Implement conversation context
    store for SMS multi-turn interactions.

-   Add email monitoring (Gmail API read-only) for forwarded tasks and
    calendar invites.

-   Implement dynamic priority escalation algorithm.

-   Implement task dependency chains and auto-rescheduling.

-   Build notification escalation tiers (app → SMS → email → phone).

-   Implement prep work system: flag tasks, define instructions,
    Research Agent executes via Claude API.

-   Build FastMCP server (Python) with initial MCP tools: web search,
    notes, filesystem read.

-   Implement cost tracking dashboard panels in Grafana.

-   Implement correction logging: every user override is recorded.

-   End-of-day digest email.

-   Externalize prompt templates as files; begin few-shot example
    accumulation from corrections.

-   Integration tests for all service boundaries.

**Phase 2 Deliverable:** Multi-channel communication. Prep work runs
before tasks. Costs tracked. Scheduling has priority escalation and
dependencies. Correction data accumulating for preference learning.

**Phase 3: Sub-Agents, Local LLM & Preferences (Weeks 8--11)**

*Goal: Autonomous task execution. Local LLM deployment (requires RTX
3090). Preference learning. Multi-user data model active.*

-   Deploy donna-ollama.yml with RTX 3090; implement OllamaProvider
    behind model interface.

-   Run evaluation test fixtures against local model; validate parsing
    accuracy.

-   Run offline evaluation harness sequentially against candidate local
    models; compare across quantization levels and parameter sizes.

-   Build and run escalation awareness fixtures: validate model knows
    when to hand off vs handle.

-   Build and run instruction following fixtures: validate model can
    execute Claude-generated directives.

-   Enable shadow mode with Claude as secondary model for production
    monitoring on migrated task types.

-   Enable spot-check quality monitoring with initial rate of 0.10--0.20
    for fast signal.

-   Begin local LLM tool use Stage 1 (read-only tools: task_db_read,
    calendar_read).

-   Implement rule extraction from correction log (weekly Claude API
    batch job).

-   Build preference engine: apply learned rules as post-processing on
    model output.

-   Implement preference transparency UI: view, edit, disable, delete
    learned rules.

-   Build agent worker pool with sandboxed execution environments.

-   Implement PM Agent: task decomposition, requirements interrogation,
    work packaging.

-   Implement Coding Agent: sandboxed code generation with git
    integration.

-   Implement Communication/Drafting Agent: email drafts, document
    creation.

-   Build agent activity log and monitoring system.

-   Implement budget controls: daily threshold, task cost approval,
    monthly ceiling.

-   Expand FastMCP server with GitHub tools, additional MCP endpoints.

-   Enable multi-user data paths (user_id scoping on all queries,
    per-user credentials in integration layer).

**Phase 3 Deliverable:** Sub-agents receive tasks, interrogate for
requirements, and produce outputs. Local LLM handles validated task
types with shadow monitoring and spot-check quality audits. Evaluation
harness enables model comparison. Preferences learned from corrections.
Multi-user infrastructure ready.

**Phase 4: UI, Multi-User & Polish (Weeks 12+)**

*Goal: Full dashboard, mobile app, second user onboarding,
optimization.*

-   Build Flutter Web + Android app with chat interface and production
    dashboard.

-   Upgrade Supabase to Pro plan (\$25/month) for reliable multi-user
    access.

-   Implement calendar view, task board (kanban), agent monitor, cost
    dashboard.

-   Implement completion heatmap (GitHub contribution graph style).

-   Push notifications (FCM) for Android.

-   Weekly planning session feature (Monday morning interactive
    scheduling).

-   Proactive task capture prompts (post-meeting, evening check-in,
    stale task detection).

-   Local LLM tool use Stage 2--3 (conditional tool use, write tools
    with guardrails).

-   Onboard second user (dad): per-user preferences, calendar,
    notifications, persona config.

-   Cost optimization analysis: migrate validated task types from Claude
    to local based on evaluation data.

-   Dial back spot-check rate to 0.02--0.05 as confidence in local model
    quality stabilizes.

-   End-to-end scenario tests. Performance tuning and reliability
    hardening.

**Phase 4 Deliverable:** Full-featured application with visual
dashboard, mobile access, refined autonomous workflows, multi-user
support, and optimized cost routing between local and cloud models.

**21. Success Metrics**

  ------------------ ------------------------ ---------------------------------
  **Metric**         **Target (3 months)**    **Measurement**

  Task capture rate  90%+ of action items     Self-reported weekly assessment
                     recorded                 

  Schedule adherence 70%+ tasks completed in  Automated: completed_at vs
                     time slot                scheduled_start

  Reminder           80% response within 30   Automated: reminder sent to
  effectiveness      min                      acknowledgment time

  Agent task         5+ tasks/week delegated  Automated: agent_status =
  completion         and completed            complete count

  Budget efficiency  Under \$100/month with   Automated: cost tracking
                     agents active            dashboard

  Tasks completed    25+ with consistency     Automated: completion heatmap
  per week                                    data

  Preference         80%+ of extracted rules  Automated: rule disable/delete
  learning accuracy  remain active after 30   rate
                     days                     

  Local LLM          50%+ of                  Automated: invocation_log
  migration rate     parsing/classification   model_actual analysis + eval
                     on local model           harness

  Escalation         85%+ precision and       Automated: escalation_awareness
  awareness          recall on escalation     fixture results in model sessions
                     decisions                

  Instruction        90%+ constraint          Automated: instruction_following
  following          compliance when          fixture results in model sessions
                     executing Claude         
                     directives               

  Zero unacceptable  No missed reminders, no  Automated: failure log
  failures           unintended emails, no    monitoring + alerting
                     data loss                

  Mean time to       \< 5 minutes for typical Manual: using Grafana dashboard +
  diagnose           production issues        correlation trace
  ------------------ ------------------------ ---------------------------------

**22. Technology Stack Summary**

  --------------- ---------------------------- ----------------------------------
  **Layer**       **Technology**               **Notes**

  Orchestrator    Python (asyncio)             Core service: routing, scheduling,
                                               state management, preference
                                               engine

  Cloud LLM       Claude API                   Primary LLM for all phases. Sonnet
                  (claude-sonnet-4-20250514)   for cost efficiency; Opus for
                                               critical tasks.

  Local LLM       Ollama + Llama 3.1 8B        Deferred until 3090 acquired.
                  (Q4_K_M) on RTX 3090         Dedicated GPU, no sharing.

  Model Interface Python (AnthropicProvider,   Standardized complete() interface
                  OllamaProvider)              with structured logging

  Agent Framework Python + Claude API tool use Each agent is a Python process
                                               with defined tool access

  Integration     Python (internal API         Direct calls for orchestrator.
  Layer           modules)                     Centralized auth, audit logging.

  MCP Server      Python (FastMCP 3.x,         LLM-facing tools only. CodeMode
                  Streamable HTTP)             for token efficiency. External
                                               client endpoint.

  Task Database   SQLite on NVMe (primary)     WAL mode. Sub-ms reads. user_id on
                                               all tables.

  Log Database    SQLite on NVMe (dedicated)   Structured JSON logs. Separate
                                               from task DB.

  Cloud Replica   Supabase (Postgres)          Free tier + keep-alive → Pro at
                                               Phase 4. Write-through sync.

  Observability   Grafana + Loki + Promtail    Phase 1 deliverable.
                  (Docker)                     donna-monitoring.yml.

  Structured      structlog (JSON,             Async-safe context propagation.
  Logging         contextvars)                 Correlation IDs.

  Web/Mobile App  Flutter (Web + Android)      Single codebase. Firebase Hosting.
                                               FCM push. Phase 4.

  Backend API     Python FastAPI               REST API between Flutter app and
                                               orchestrator

  Notifications   Twilio (SMS/Voice), Gmail    Multi-channel with escalation
                  API, FCM, discord.py         tiers

  Deployment      Docker Compose (multi-file   donna-core, donna-monitoring,
                  homelab pattern)             donna-ollama, donna-app

  Server OS       Ubuntu Linux (always-on home i7-6700K, 32GB. GTX 1080 (Immich).
                  server)                      RTX 3090 (Donna, TBA).

  Storage         1TB NVMe dedicated to Donna  DB, logs, workspace, backups,
                                               config, fixtures, model cache

  Schema          Alembic (SQLAlchemy)         Version-controlled migrations for
  Migration                                    SQLite + Supabase

  Testing         pytest, pytest-asyncio,      4 layers: unit, integration, LLM
                  aioresponses, eval harness   eval, E2E

  Version Control GitHub                       All code, agent outputs on feature
                                               branches

  Secrets         age/sops or environment      Never in code, never in agent
  Management      variables                    context

  Configuration   YAML files (models, routing, Config over code for all
                  task types, states,          extensible behavior
                  preferences)                 
  --------------- ---------------------------- ----------------------------------

*--- End of Specification ---*
