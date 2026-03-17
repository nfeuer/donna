# Slice 3: Discord Bot & Task Capture

> **Goal:** Stand up the Discord bot as the first real input channel. Users send natural language messages in #donna-tasks, Donna parses them into tasks, stores them in SQLite, and confirms back on the same channel.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/notifications.md` — Discord integration detail, channel layout, thread-based context
- `docs/task-system.md` — Input parsing pipeline, task schema

## What to Build

1. **Implement the Discord bot** (`src/donna/integrations/discord_bot.py`):
   - Uses `discord.py` with message intents enabled
   - Listens for messages in the configured `#donna-tasks` channel
   - Ignores bot's own messages and messages from other bots
   - On new message: generates a `correlation_id`, binds it to structlog context, and passes raw text to the input parser (Slice 2)

2. **Wire the full capture pipeline:**
   - Raw Discord message → input parser → task CRUD (Slice 1) → confirmation message
   - Confirmation format: "Got it. '[title]' — [domain], priority [N]. Scheduled: pending."
   - If parsing confidence < 0.7: ask for clarification instead of auto-creating

3. **Implement Discord channel routing:**
   - Task capture messages → `#donna-tasks`
   - System/debug messages → `#donna-debug`
   - Channel IDs loaded from environment variables

4. **Error handling:**
   - If the API call fails (circuit breaker open, retries exhausted): send a degraded response to `#donna-tasks`: "Captured your message. I'll parse it properly when my brain comes back online."
   - Store raw text as task title with `_parse_error` flag for re-processing later

5. **Write tests:**
   - Unit test: mock `discord.py` message objects, verify the pipeline calls the parser and creates a task
   - Unit test: verify low-confidence parse triggers clarification flow
   - Unit test: verify degraded mode stores raw text on API failure

## Acceptance Criteria

- [ ] Bot connects to Discord and shows as online
- [ ] Message in `#donna-tasks` triggers task parsing
- [ ] Parsed task is created in SQLite with all inferred fields
- [ ] Confirmation message appears in `#donna-tasks` with task summary
- [ ] Low-confidence parse asks for clarification instead of auto-creating
- [ ] API failure → raw text stored as task, degraded confirmation sent
- [ ] Messages in other channels are ignored
- [ ] Bot's own messages are ignored (no infinite loops)
- [ ] `correlation_id` traces the message from Discord through parsing to DB write
- [ ] Environment variables configure channel IDs (not hardcoded)

## Not in Scope

- No thread-based PM interrogation yet
- No deduplication check
- No scheduling (tasks go to backlog)
- No morning digest
- No SMS/email channels

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/notifications.md`, `docs/task-system.md`
