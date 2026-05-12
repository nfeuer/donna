# Discord User Onboarding & DM Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-onboard new Discord users by challenging for their name before creating a profile, and add a DM delivery path for per-user automation alerts.

**Architecture:** Onboarding gate intercepts unknown Discord users early in `on_message`, challenges for a name, creates a user row with nullable `immich_user_id`, and replays the stashed message. DM delivery adds `send_dm` to `BotProtocol` and `dispatch_dm` to `NotificationService`.

**Tech Stack:** Python 3.12, aiosqlite, discord.py, Alembic, pytest

**Spec:** `docs/superpowers/specs/2026-05-12-discord-onboarding-dm-delivery-design.md`

---

### Task 1: Alembic Migration — Make `immich_user_id` Nullable

**Files:**
- Create: `alembic/versions/d4e5f6a7b8c9_make_immich_user_id_nullable.py`

SQLite doesn't support `ALTER COLUMN`, so we use Alembic's `batch_alter_table` which recreates the table behind the scenes.

- [ ] **Step 1: Create the migration file**

```python
"""Make immich_user_id nullable for Discord-onboarded users.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b9
Create Date: 2026-05-12
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "immich_user_id",
            existing_type=sa.String(100),
            nullable=True,
        )
        batch_op.drop_constraint("uq_users_immich_user_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_users_immich_user_id", ["immich_user_id"]
        )
    # Also make email nullable — Discord-onboarded users won't have one.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(254),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(254),
            nullable=False,
        )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "immich_user_id",
            existing_type=sa.String(100),
            nullable=False,
        )
```

- [ ] **Step 2: Verify migration applies cleanly**

Run: `python3 -c "from alembic.config import Config; from alembic import command; command.upgrade(Config('alembic.ini'), 'head')"`

Expected: No errors. Verify with: `sqlite3 /mnt/donna/db/donna_tasks.db ".schema users"` — `immich_user_id` should NOT have `NOT NULL`.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/d4e5f6a7b8c9_make_immich_user_id_nullable.py
git commit -m "migration: make immich_user_id and email nullable for Discord-onboarded users"
```

---

### Task 2: Database Methods — `create_discord_user` and `get_discord_id`

**Files:**
- Modify: `src/donna/tasks/database.py` (after `resolve_user_id` at line ~317)
- Test: `tests/unit/test_database_onboarding.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_database_onboarding.py`:

```python
"""Tests for Discord user onboarding database methods."""
from __future__ import annotations

import pytest
import aiosqlite

from donna.tasks.database import Database


@pytest.fixture
async def db(tmp_path):
    """Create a temporary Database with the users table."""
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE users (
                donna_user_id VARCHAR(100) PRIMARY KEY,
                immich_user_id VARCHAR(100),
                email VARCHAR(254),
                name VARCHAR(200),
                role VARCHAR(20) DEFAULT 'user' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                last_login_at DATETIME,
                discord_id VARCHAR(30),
                phone VARCHAR(20)
            )
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX idx_users_discord_id ON users (discord_id)"
        )
        await conn.commit()

    from donna.tasks.state_machine import StateMachine
    from unittest.mock import MagicMock
    sm = MagicMock(spec=StateMachine)
    database = Database(db_path, sm)
    await database.connect()
    yield database
    await database.close()


class TestCreateDiscordUser:
    async def test_creates_user_with_name_and_discord_id(self, db):
        user_id = await db.create_discord_user(
            discord_id="123456789",
            name="Alice",
            discord_username="alice99",
        )
        assert user_id == "alice99"
        row = await (
            await db.connection.execute(
                "SELECT donna_user_id, name, discord_id, immich_user_id, email FROM users WHERE donna_user_id = ?",
                (user_id,),
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "alice99"
        assert row[1] == "Alice"
        assert row[2] == "123456789"
        assert row[3] is None  # immich_user_id nullable
        assert row[4] is None  # email nullable

    async def test_handles_username_collision(self, db):
        await db.create_discord_user(
            discord_id="111",
            name="Bob",
            discord_username="bob",
        )
        user_id = await db.create_discord_user(
            discord_id="222",
            name="Bob Two",
            discord_username="bob",
        )
        assert user_id == "bob_2"

    async def test_lowercases_and_strips_username(self, db):
        user_id = await db.create_discord_user(
            discord_id="333",
            name="Charlie",
            discord_username="  ChArLiE  ",
        )
        assert user_id == "charlie"


class TestGetDiscordId:
    async def test_returns_discord_id_for_known_user(self, db):
        await db.create_discord_user(
            discord_id="999888777",
            name="Dave",
            discord_username="dave",
        )
        result = await db.get_discord_id("dave")
        assert result == "999888777"

    async def test_returns_none_for_unknown_user(self, db):
        result = await db.get_discord_id("nobody")
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_database_onboarding.py -v`
Expected: FAIL — `Database` has no `create_discord_user` or `get_discord_id` methods.

- [ ] **Step 3: Implement `create_discord_user` and `get_discord_id`**

Add these methods to `src/donna/tasks/database.py` after `resolve_user_id` (line ~317):

```python
    async def create_discord_user(
        self,
        discord_id: str,
        name: str,
        discord_username: str,
    ) -> str:
        """Create a new user row for a Discord-onboarded user.

        Args:
            discord_id: Discord snowflake ID.
            name: Display name the user provided.
            discord_username: Discord username, used to derive donna_user_id.

        Returns:
            The generated donna_user_id.
        """
        conn = self.connection
        base_id = discord_username.strip().lower()
        donna_user_id = base_id
        suffix = 2
        while True:
            existing = await (
                await conn.execute(
                    "SELECT 1 FROM users WHERE donna_user_id = ?",
                    (donna_user_id,),
                )
            ).fetchone()
            if existing is None:
                break
            donna_user_id = f"{base_id}_{suffix}"
            suffix += 1

        await conn.execute(
            """INSERT INTO users (donna_user_id, immich_user_id, email, name, discord_id, role)
               VALUES (?, NULL, NULL, ?, ?, 'user')""",
            (donna_user_id, name, discord_id),
        )
        await conn.commit()
        logger.info(
            "discord_user_created",
            donna_user_id=donna_user_id,
            discord_id=discord_id,
            name=name,
        )
        return donna_user_id

    async def get_discord_id(self, donna_user_id: str) -> str | None:
        """Look up discord_id from a donna_user_id."""
        conn = self.connection
        row = await (
            await conn.execute(
                "SELECT discord_id FROM users WHERE donna_user_id = ?",
                (donna_user_id,),
            )
        ).fetchone()
        return row[0] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_database_onboarding.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/tasks/database.py tests/unit/test_database_onboarding.py
git commit -m "feat: add create_discord_user and get_discord_id to Database"
```

---

### Task 3: Onboarding Gate in Discord Bot

**Files:**
- Modify: `src/donna/integrations/discord_bot.py` (lines ~104 for init, ~251-310 for on_message)
- Test: `tests/unit/test_discord_onboarding.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_discord_onboarding.py`:

```python
"""Tests for Discord user auto-onboarding flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.integrations.discord_bot import DonnaBot

TASKS_CHANNEL_ID = 111111111111111111
USER_DISCORD_ID = "999888777666555444"


def _make_message(
    content: str = "Buy milk",
    channel_id: int = TASKS_CHANNEL_ID,
    author_id: str = USER_DISCORD_ID,
    author_name: str = "testuser",
    author_is_bot: bool = False,
) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.channel.id = channel_id
    message.channel.send = AsyncMock()
    message.author.bot = author_is_bot
    message.author.id = author_id
    message.author.name = author_name
    message.create_thread = AsyncMock()
    return message


def _make_bot(database: AsyncMock | None = None) -> DonnaBot:
    parser = AsyncMock()
    db = database or AsyncMock()
    with patch.object(discord.Client, "__init__", return_value=None):
        bot = DonnaBot(
            input_parser=parser,
            database=db,
            tasks_channel_id=TASKS_CHANNEL_ID,
        )
    return bot


class TestOnboardingGate:
    async def test_unknown_user_gets_name_challenge(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg = _make_message(content="Track my package")
        await bot.on_message(msg)

        msg.channel.send.assert_called_once()
        sent = msg.channel.send.call_args[0][0]
        assert "name" in sent.lower()
        assert USER_DISCORD_ID in bot._pending_onboarding
        assert bot._pending_onboarding[USER_DISCORD_ID] == "Track my package"

    async def test_name_reply_creates_user_and_replays(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        db.create_discord_user = AsyncMock(return_value="testuser")
        bot = _make_bot(db)

        # First message — gets challenged
        msg1 = _make_message(content="Track my package")
        await bot.on_message(msg1)

        # Second message — name reply
        db.resolve_user_id = AsyncMock(return_value=None)
        msg2 = _make_message(content="Alice")
        await bot.on_message(msg2)

        db.create_discord_user.assert_called_once_with(
            discord_id=USER_DISCORD_ID,
            name="Alice",
            discord_username="testuser",
        )
        assert USER_DISCORD_ID not in bot._pending_onboarding
        # Confirmation message should mention name
        calls = msg2.channel.send.call_args_list
        assert any("Alice" in str(c) for c in calls)

    async def test_known_user_bypasses_onboarding(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value="nick")
        bot = _make_bot(db)

        msg = _make_message(content="Buy milk")
        await bot.on_message(msg)

        # Should NOT get a name challenge
        if msg.channel.send.called:
            sent = msg.channel.send.call_args[0][0]
            assert "name" not in sent.lower()

    async def test_repeat_messages_while_pending_get_reminder(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg1 = _make_message(content="First message")
        await bot.on_message(msg1)

        msg2 = _make_message(content="")  # empty — not a valid name
        await bot.on_message(msg2)

        # Second call should re-prompt, not create user
        calls = msg2.channel.send.call_args_list
        assert any("name" in str(c).lower() for c in calls)

    async def test_empty_name_reprompts(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg1 = _make_message(content="Track my package")
        await bot.on_message(msg1)

        msg2 = _make_message(content="   ")
        await bot.on_message(msg2)

        db.create_discord_user = AsyncMock()
        db.create_discord_user.assert_not_called()
        assert USER_DISCORD_ID in bot._pending_onboarding
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_discord_onboarding.py -v`
Expected: FAIL — `DonnaBot` has no `_pending_onboarding` attribute.

- [ ] **Step 3: Add `_pending_onboarding` to `__init__`**

In `src/donna/integrations/discord_bot.py`, after line 104 (`self._clarification_threads: set[int] = set()`), add:

```python
        # Maps Discord snowflake ID → stashed original message for onboarding.
        self._pending_onboarding: dict[str, str] = {}
```

- [ ] **Step 4: Add onboarding gate to `on_message`**

In `src/donna/integrations/discord_bot.py`, inside `on_message`, after the bot-filter block (line ~264 `if message.author.bot: return`) and BEFORE all channel routing (line ~267 `if message.channel.id in self.overdue_threads`), insert:

```python
        # --- Onboarding gate: challenge unknown Discord users for their name ---
        discord_id_raw = str(message.author.id)
        if await self._database.resolve_user_id(discord_id_raw) is None:
            raw_text = message.content.strip()
            if discord_id_raw not in self._pending_onboarding:
                self._pending_onboarding[discord_id_raw] = raw_text
                await message.channel.send(
                    "Hey! I'm Donna. I don't think we've met — what's your name?"
                )
                return
            # User is replying with their name.
            if not raw_text:
                await message.channel.send(
                    "I still need your name first! Just type your first name."
                )
                return
            try:
                donna_user_id = await self._database.create_discord_user(
                    discord_id=discord_id_raw,
                    name=raw_text,
                    discord_username=message.author.name,
                )
            except Exception:
                logger.exception("onboarding_create_user_failed", discord_id=discord_id_raw)
                await message.channel.send(
                    "Something went wrong setting up your profile. Try again in a moment."
                )
                return
            stashed = self._pending_onboarding.pop(discord_id_raw)
            await message.channel.send(
                f"Nice to meet you, {raw_text}! Let me handle that for you."
            )
            # Replay the stashed message through the normal pipeline.
            # Build a synthetic message with the original content.
            message.content = stashed
            # Fall through to the rest of on_message — resolve_user_id
            # will now return the new donna_user_id on the next lookup below.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_discord_onboarding.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Run existing bot tests to check for regressions**

Run: `python3 -m pytest tests/unit/test_discord_bot.py tests/unit/test_discord_bot_wave3.py -v`
Expected: All existing tests still PASS. If any fail because `resolve_user_id` is not mocked, update the `_make_bot` helper in those files to mock it returning a valid user_id.

- [ ] **Step 7: Commit**

```bash
git add src/donna/integrations/discord_bot.py tests/unit/test_discord_onboarding.py
git commit -m "feat: add Discord user onboarding gate in on_message"
```

---

### Task 4: BotProtocol — Add `send_dm`

**Files:**
- Modify: `src/donna/notifications/bot_protocol.py` (line 17)
- Modify: `src/donna/integrations/discord_bot.py` (after `send_to_thread` at line ~209)

- [ ] **Step 1: Add `send_dm` to BotProtocol**

In `src/donna/notifications/bot_protocol.py`, add after line 17:

```python
    async def send_dm(self, discord_id: str, content: str) -> None: ...
```

The file should now read:

```python
@runtime_checkable
class BotProtocol(Protocol):
    async def send_message(self, channel_name: str, text: str) -> Any: ...
    async def send_embed(self, channel_name: str, embed: Any) -> Any: ...
    async def send_to_thread(self, thread_id: int, text: str) -> None: ...
    async def send_dm(self, discord_id: str, content: str) -> None: ...
```

- [ ] **Step 2: Implement `send_dm` in DonnaBot**

In `src/donna/integrations/discord_bot.py`, after `send_to_thread` (line ~209), add:

```python
    async def send_dm(self, discord_id: str, content: str) -> None:
        """Send a direct message to a Discord user by their snowflake ID."""
        try:
            user = await self.fetch_user(int(discord_id))
            await user.send(content)
            logger.info("dm_sent", discord_id=discord_id, content_len=len(content))
        except Exception:
            logger.exception("dm_send_failed", discord_id=discord_id)
```

- [ ] **Step 3: Commit**

```bash
git add src/donna/notifications/bot_protocol.py src/donna/integrations/discord_bot.py
git commit -m "feat: add send_dm to BotProtocol and DonnaBot"
```

---

### Task 5: NotificationService — Add `dispatch_dm`

**Files:**
- Modify: `src/donna/notifications/service.py` (after `dispatch` method, line ~154)
- Test: `tests/unit/test_notification_dm.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_notification_dm.py`:

```python
"""Tests for DM delivery via NotificationService.dispatch_dm."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.notifications.service import (
    NOTIF_AUTOMATION_ALERT,
    NotificationService,
)


def _make_calendar_config():
    """Build a minimal CalendarConfig mock with time windows."""
    config = MagicMock()
    config.timezone = "UTC"
    config.time_windows.blackout.start_hour = 0
    config.time_windows.blackout.end_hour = 6
    config.time_windows.quiet_hours.start_hour = 20
    config.time_windows.quiet_hours.end_hour = 24
    return config


def _make_service(bot: AsyncMock | None = None) -> NotificationService:
    bot = bot or AsyncMock()
    return NotificationService(
        bot=bot,
        calendar_config=_make_calendar_config(),
        user_id="nick",
    )


class TestDispatchDm:
    async def test_sends_dm_during_active_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Price dropped below $50!",
            priority=5,
        )

        assert result is True
        bot.send_dm.assert_called_once_with("123456789", "Price dropped below $50!")

    async def test_queues_dm_during_blackout(self):
        bot = AsyncMock()
        service = _make_service(bot)

        # Patch _is_blackout to return True
        service._is_blackout = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Price dropped!",
            priority=2,
        )

        assert result is False
        bot.send_dm.assert_not_called()

    async def test_priority_5_passes_through_quiet_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_quiet = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Urgent alert!",
            priority=5,
        )

        assert result is True
        bot.send_dm.assert_called_once()

    async def test_low_priority_queued_during_quiet_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_quiet = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Non-urgent",
            priority=2,
        )

        assert result is False
        bot.send_dm.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_notification_dm.py -v`
Expected: FAIL — `NotificationService` has no `dispatch_dm` method.

- [ ] **Step 3: Implement `dispatch_dm`**

In `src/donna/notifications/service.py`, after the `dispatch` method (line ~154), add:

```python
    async def dispatch_dm(
        self,
        discord_id: str,
        notification_type: str,
        content: str,
        priority: int = 2,
    ) -> bool:
        """Dispatch a direct message to a Discord user.

        Same blackout/quiet-hours gating as dispatch(). Sends via
        bot.send_dm() instead of a channel.

        Args:
            discord_id: Discord snowflake ID of the recipient.
            notification_type: One of NOTIF_* constants.
            content: Message text.
            priority: 1-5; only priority 5 passes through quiet hours.

        Returns:
            True if sent immediately, False if queued/blocked.
        """
        now = datetime.now(tz=UTC)
        content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:8]

        log = logger.bind(
            notification_type=notification_type,
            discord_id=discord_id,
            priority=priority,
            content_hash=content_hash,
            delivery="dm",
        )

        if self._is_blackout(now):
            log.info("dm_queued_blackout")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False

        if self._is_quiet(now) and priority < 5:
            log.info("dm_queued_quiet_hours")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False

        try:
            await self._bot.send_dm(discord_id, content)
            log.info("dm_sent")
        except Exception:
            log.exception("dm_send_failed")
        return True

    def _enqueue_dm(
        self,
        discord_id: str,
        notification_type: str,
        content: str,
        priority: int,
    ) -> None:
        """Add a DM send coroutine to the deferred queue."""
        async def _send_later() -> None:
            log = logger.bind(
                notification_type=notification_type,
                discord_id=discord_id,
                delivery="dm",
            )
            try:
                await self._bot.send_dm(discord_id, content)
                log.info("dm_sent_from_queue")
            except Exception:
                log.exception("dm_send_from_queue_failed")

        self._queue.append(_send_later)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_notification_dm.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Run existing notification tests for regressions**

Run: `python3 -m pytest tests/unit/ -k notification -v`
Expected: All existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/notifications/service.py tests/unit/test_notification_dm.py
git commit -m "feat: add dispatch_dm to NotificationService for per-user DM delivery"
```
