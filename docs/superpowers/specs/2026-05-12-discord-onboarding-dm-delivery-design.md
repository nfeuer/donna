# Discord User Auto-Onboarding & DM Delivery

## Goal

When a new Discord user messages a Donna channel, automatically onboard them by
asking for their name, creating a user profile, then processing their original
message. Additionally, add a DM delivery path so per-user notifications
(automation alerts, price watches) go directly to the requesting user instead of
a shared channel.

## Architecture

Two independent subsystems that share the `users` table:

1. **Onboarding gate** — intercepts unknown Discord users in `on_message`,
   challenges for a name, creates a user row, replays the stashed message.
2. **DM delivery** — new `send_dm` on `BotProtocol`, new `dispatch_dm` on
   `NotificationService`, callers choose DM vs channel at the call site.

## 1. Schema: Make `immich_user_id` Nullable

**Migration**: `ALTER TABLE users ALTER COLUMN immich_user_id` to nullable.
SQLite doesn't support `ALTER COLUMN`, so the migration recreates the table
with the same columns but `immich_user_id VARCHAR(100)` nullable.

Discord-onboarded users get a row with:
- `donna_user_id` — slug derived from Discord username (e.g. `"heavyuser"`)
- `discord_id` — snowflake ID (unique, indexed)
- `name` — what the user told Donna
- `immich_user_id` — `NULL`
- `email` — `NULL` (can be added later via profile update)
- `role` — `"user"` (default)

**New Database method**: `create_discord_user(discord_id, name, discord_username) -> str`
- Generates `donna_user_id` from the Discord username (lowercase, stripped)
- Handles uniqueness collisions by appending a numeric suffix
- Returns the new `donna_user_id`

**New Database method**: `get_discord_id(donna_user_id) -> str | None`
- Reverse lookup for DM delivery: given a donna user, return their Discord ID

## 2. Onboarding Flow

**State**: `DonnaBot._pending_onboarding: dict[str, str]` maps raw Discord
snowflake ID → the user's original message text.

**Position in `on_message`**: After the bot/self filter, before any channel
routing (overdue threads, challenger threads, clarification threads, chat
channel, tasks channel).

**Flow**:

1. `resolve_user_id(discord_id)` returns `None` — user is unknown.
2. If `discord_id` not in `_pending_onboarding`:
   - Stash the original message: `_pending_onboarding[discord_id] = raw_text`
   - Reply: *"Hey! I'm Donna. I don't think we've met — what's your name?"*
   - Return (do not process the message).
3. If `discord_id` is in `_pending_onboarding`:
   - The current message is their name reply.
   - Call `db.create_discord_user(discord_id, name=raw_text, discord_username=message.author.name)`
   - Reply: *"Nice to meet you, {name}! Let me handle that for you."*
   - Pop the stashed message from `_pending_onboarding`.
   - Re-dispatch the stashed message through the normal `on_message` pipeline
     by constructing a synthetic message (or simply calling the dispatcher
     directly with the stashed content and the new user_id).
   - Return.

**Edge cases**:
- User sends multiple messages before replying with name: only the first is
  stashed; subsequent messages get a reminder ("I still need your name first!").
- User sends an empty or whitespace-only name: re-prompt.
- Donna addresses users by name from the `users.name` field in all future
  interactions.

## 3. DM Delivery Path

### BotProtocol

Add one method:

```python
async def send_dm(self, discord_id: str, content: str) -> None: ...
```

### DonnaBot Implementation

```python
async def send_dm(self, discord_id: str, content: str) -> None:
    user = await self.fetch_user(int(discord_id))
    await user.send(content)
```

discord.py handles DM channel creation automatically via `User.send()`.

### NotificationService

New method:

```python
async def dispatch_dm(
    self,
    discord_id: str,
    notification_type: str,
    content: str,
    priority: int = 2,
) -> bool:
```

Same blackout/quiet-hours gating as `dispatch()`. Internally calls
`self._bot.send_dm(discord_id, content)`.

### Routing Rule

Callers choose DM vs channel explicitly:
- **Automation alerts** (price watches, condition triggers) → `dispatch_dm`
  with the requesting user's `discord_id` (looked up via
  `db.get_discord_id(user_id)`).
- **Digests** (morning, EOD, weekly) → `dispatch()` to `#donna-digest` (unchanged).
- **Reminders/nudges** → `dispatch()` to `#donna-tasks` (unchanged for now).

## 4. Files Changed

| File | Change |
|------|--------|
| `alembic/versions/…_make_immich_nullable.py` | New migration |
| `src/donna/tasks/database.py` | `create_discord_user()`, `get_discord_id()` |
| `src/donna/integrations/discord_bot.py` | Onboarding gate in `on_message`, `send_dm()` |
| `src/donna/notifications/bot_protocol.py` | Add `send_dm` to protocol |
| `src/donna/notifications/service.py` | `dispatch_dm()` |

## 5. What This Does NOT Cover

- Profile update commands (email, phone) — future feature.
- DM routing for reminders/nudges — stays in channel for now.
- Immich account linking for Discord-onboarded users — future feature.
- Companion app auth flow — separate design when Flutter work begins.
