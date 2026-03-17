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
| Discord Bot | Direct API (discord.py) | Bidirectional messaging. Bot framework handles natively. |
| Gmail API | Direct API (Python client) | Orchestrator reads/drafts with known scopes. |
| Twilio SMS/Voice | Direct API (Python client) | Outbound notifications with fixed parameters. |
| Supabase Sync | Direct API (supabase-py) | Background sync with fixed schema. |
| GitHub | MCP (FastMCP) | Coding Agent explores repos/issues dynamically. |
| Web Search | MCP (FastMCP) | Research Agent discovers and invokes search dynamically. |
| Filesystem (sandboxed) | MCP (FastMCP) | Agents discover and navigate files dynamically. |
| Notes (Local Markdown) | MCP (FastMCP) | Agents discover and read notes dynamically. |

## Integration Modules

```
src/donna/integrations/
├── calendar.py      ← Google Calendar (read-write personal, read all)
├── gmail.py         ← Gmail (read + draft; send behind feature flag)
├── github.py        ← GitHub (MCP-wrapped, read-write feature branches only)
├── filesystem.py    ← Sandboxed to /donna/workspace/
├── discord_bot.py   ← Send/read in Donna channels
├── twilio_sms.py    ← SMS and voice (outbound only)
├── notes.py         ← Local markdown notes
├── search.py        ← Web search (SearXNG or API)
└── mcp_wrapper.py   ← FastMCP Streamable HTTP for external clients
```

Each module: centralized auth, audit logging to logging DB, rate limiting, access control per agent via task type config.

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
| Google Calendar | Read-Write (personal); Read (work, family) | Direct API | `calendar_read`, `calendar_write`, `calendar_delete` |
| GitHub | Read-Write (feature branches only) | MCP (FastMCP) | `github_read`, `github_write`, `github_issues` |
| Notes | Read-Write | MCP (FastMCP) | `notes_read`, `notes_write` |
| Filesystem | Read-Write (sandboxed to `/donna/workspace/`) | MCP (FastMCP) | `fs_read`, `fs_write`, `fs_list` |
| Discord | Read-Write (Donna channels only) | Direct API | `discord_send`, `discord_read`, thread management |
| Twilio | Write (outbound only) | Direct API | `sms_send`, `phone_call` |
| Web Search | Read | MCP (FastMCP) | `search_web` (SearXNG or API) |
| SQLite Task DB | Read-Write | Direct API | Internal orchestrator access |
| Supabase | Write (sync replica) | Direct API | Background write-through sync |

## Adopt Before Building

Before implementing custom MCP tools, evaluate existing open-source servers (e.g., `google-calendar-mcp`, GitHub MCP server). If a community server covers 80%+ of needs, adopt and extend. FastMCP's composability supports mounting external servers alongside custom tools.
