# Tool Integration Architecture

> Split from Donna Project Spec v3.0 — Sections 3.2, 12

## The MCP Context Cost Problem

MCP servers dump full tool schemas into the LLM context on connection. 20–90+ tools = 30,000–150,000+ tokens before any query. Against a $100/month budget, this overhead is unacceptable for internal integrations where the orchestrator (not the LLM) makes the call.

Mitigations exist (Claude Tool Search ~85% reduction, FastMCP CodeMode ~1,000 tokens), but MCP still adds unnecessary serialization overhead for orchestrator-to-service calls.

## Hybrid Strategy

Two tiers based on **who is making the call**:

### Tier 1: Internal Python API (Primary)

All orchestrator-to-service integration uses thin Python modules. Orchestrator calls functions directly — no protocol overhead, no schema in context. LLM outputs structured JSON; orchestrator maps fields to API calls. **Zero tokens for tool definitions.**

### Tier 2: MCP Endpoint (LLM-Facing + External Clients)

MCP via FastMCP 3.x when agents need dynamic tool discovery during reasoning (Research Agent deciding which tools to use, Coding Agent exploring a repo). Also maintained as Streamable HTTP endpoint for Flutter app, Claude Desktop, and future clients.

## Decision Framework

| Integration | Pattern | Rationale |
|------------|---------|-----------|
| Google Calendar API | Direct API (Python client) | Orchestrator calls with known params. No discovery needed. |
| SQLite Task DB | Direct API (aiosqlite) | Internal data store. MCP wrapper = pure overhead. |
| Discord Bot | Direct API (discord.py) | Bidirectional messaging. Bot framework handles natively. Modular: commands, views, agent feed, drafts. |
| Gmail API | Direct API (Python client) | Orchestrator reads/drafts with known scopes. |
| Email Parser | Direct API (Python) | Forwarded-email parsing and task creation pipeline. |
| Twilio SMS | Direct API (Python client) | Outbound SMS with rate limiting and blackout hours. |
| Twilio Voice | Direct API (Python client) | Outbound TTS phone calls for Tier 4 escalation. Rate-limited to 1/day. |
| SMS Router | Direct API (Python) | Inbound SMS conversation routing with context TTL. |
| Supabase Sync | Direct API (supabase-py) | Background write-through sync with recovery queue. |
| Obsidian Vault | Direct API (Python) | Read/write markdown notes with git-backed audit trail. |
| Git Repo | Direct API (subprocess) | Vault commit/revert via git subprocess — no GitPython dependency. |

## Integration Modules

```
src/donna/integrations/
├── __init__.py
├── calendar.py              ← Google Calendar (read-write personal, read all)
├── gmail.py                 ← Gmail (read + draft; send behind feature flag)
├── email_parser.py          ← Forwarded-email parser → InputParser pipeline
├── discord_bot.py           ← Core DonnaBot: message listener, outbound, overdue routing
├── discord_commands.py      ← Slash commands: /tasks, /done, /cancel, /reschedule, /edit, etc.
├── discord_views.py         ← Interactive UI: buttons, dropdowns, modals (TaskEditModal, approvals)
├── discord_agent_feed.py    ← Agent activity embeds → #donna-agents channel
├── discord_pending_drafts.py← In-memory draft registry (task/automation, 30-min TTL)
├── discord_submit_command.py← /donna submit slash command for chat-mode escalation answers
├── twilio_sms.py            ← Outbound SMS (rate-limited, blackout hours)
├── twilio_voice.py          ← Outbound TTS phone calls (Tier 4 escalation, 1/day limit)
├── sms_router.py            ← Inbound SMS routing: context lookup, disambiguation, new-task fallback
├── supabase_sync.py         ← Async write-through sync to Supabase Postgres
├── vault.py                 ← Obsidian-compatible vault (VaultClient read, VaultWriter mutate)
└── git_repo.py              ← Subprocess git wrapper for vault audit trail (commit, revert, log)
```

Each module: centralized auth, audit logging to logging DB, rate limiting, access control per agent via task type config.

### Discord Module Breakdown

Discord is no longer a single file. The bot is split into six modules:

| Module | Purpose |
|--------|---------|
| `discord_bot.py` | Core `DonnaBot` class — message listener, outbound messaging, overdue thread routing |
| `discord_commands.py` | Guild-registered slash commands with autocomplete (`/tasks`, `/done`, `/cancel`, `/reschedule`, `/next`, `/today`, `/tomorrow`, `/edit`, `/status`) |
| `discord_views.py` | Interactive UI components — `TaskEditModal`, `TaskListPaginationView`, `AgentApprovalView`, buttons, dropdowns |
| `discord_agent_feed.py` | `AgentActivityFeed` — posts agent start/complete/failure embeds to #donna-agents with approval buttons for approvable actions |
| `discord_pending_drafts.py` | `PendingDraftRegistry` — per-user in-memory map of task/automation partial drafts (thread-id keyed, 30-min TTL) |
| `discord_submit_command.py` | `/donna submit` command for chat-mode escalation answer submission (min 50 chars, owner-only, validates via `escalation_submit_service`) |

## FastMCP Server (Python)

Implemented in Python using FastMCP 3.x. Exposes tools agents need during LLM-driven reasoning. CodeMode enabled for token efficiency.

Design principles:
- **Tool granularity:** Each action is a separate tool for fine-grained access control per agent and task type.
- **Centralized auth:** All OAuth tokens and API keys in MCP server config, never passed to agents.
- **Audit logging:** Every tool invocation logged with timestamp, calling agent, parameters, result.
- **Rate limiting:** Per-tool limits to prevent runaway agents.
- **Tool registry as config:** Adding a new tool = implementation + config entry. Orchestrator discovers tools at startup.

## Integration Access Matrix

| Service | Access Level | Pattern | Tools/Methods |
|---------|-------------|---------|---------------|
| Gmail | Read-only (send behind flag) | Direct API | `email_read`, `email_search`, `draft_create` |
| Email Parser | Read (forwarded inbox) | Direct API | Forwarded-email detection → InputParser pipeline |
| Google Calendar | Read-Write (personal); Read (work, family) | Direct API | `calendar_read`, `calendar_write`, `calendar_delete` |
| Discord | Read-Write (Donna channels + DMs) | Direct API | `discord_send`, `discord_read`, `discord_dm`, thread management, slash commands, interactive views |
| Twilio SMS | Write (outbound only) | Direct API | `sms_send` (rate-limited, blackout hours) |
| Twilio Voice | Write (outbound only) | Direct API | `phone_call` (Tier 4 escalation, 1/day) |
| SMS Router | Read-Write (inbound routing) | Direct API | Context lookup, disambiguation, new-task fallback (24h sliding + 72h hard TTL) |
| Obsidian Vault | Read-Write | Direct API | `vault_read`, `vault_write`, `vault_list`, `vault_link`, `vault_undo_last` |
| Git Repo | Write (vault audit trail) | Direct API | `commit`, `revert`, `log` — subprocess-based, no GitPython |
| SQLite Task DB | Read-Write | Direct API | Internal orchestrator access |
| Supabase | Write (sync replica) | Direct API | Background write-through sync with recovery queue |

## Adopt Before Building

Before implementing custom MCP tools, evaluate existing open-source servers (e.g., `google-calendar-mcp`, GitHub MCP server). If a community server covers 80%+ of needs, adopt and extend. FastMCP's composability supports mounting external servers alongside custom tools.

## Slice 15 — CalendarMirror gains `attendees`

Migration `c9d1e3f5a7b2_add_calendar_mirror_attendees.py` adds a nullable
`attendees TEXT` column to `calendar_mirror`. `calendar.py::_parse_event`
reads `items[i].attendees` from the Google Calendar API payload and
normalises each entry to `{name, email}` (name = `displayName` with
email local-part as a fallback). `calendar_sync.py::_update_mirror`
JSON-encodes the list on upsert. The meeting-note skill in Slice 15
consumes this column to resolve attendee wikilinks into
`[[People/{name}]]` or `[[{name}]]`.

See `docs/domain/memory-vault/templates.md` for the full skill flow.
