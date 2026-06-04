"""Tests for Discord user onboarding database methods."""
from __future__ import annotations

import aiosqlite
import pytest

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

    from unittest.mock import MagicMock

    from donna.tasks.state_machine import StateMachine
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
                "SELECT donna_user_id, name, discord_id,"
                " immich_user_id, email"
                " FROM users WHERE donna_user_id = ?",
                (user_id,),
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "alice99"
        assert row[1] == "Alice"
        assert row[2] == "123456789"
        assert row[3] is None
        assert row[4] is None

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


class TestLinkOwnerDiscordId:
    """Bug 2: link a Discord ID to an existing owner row instead of forking
    a brand-new identity via create_discord_user."""

    async def _insert_owner(self, db, *, discord_id=None):
        await db.connection.execute(
            "INSERT INTO users (donna_user_id, email, name, role, discord_id)"
            " VALUES (?, ?, ?, ?, ?)",
            ("nick", "nick@x.com", "Nick", "admin", discord_id),
        )
        await db.connection.commit()

    async def test_links_discord_id_to_existing_owner(self, db):
        await self._insert_owner(db)
        linked = await db.link_owner_discord_id("nick", "209121227925618688")
        assert linked is True
        assert await db.resolve_user_id("209121227925618688") == "nick"

    async def test_idempotent_when_already_linked(self, db):
        await self._insert_owner(db, discord_id="209121227925618688")
        linked = await db.link_owner_discord_id("nick", "209121227925618688")
        assert linked is True
        assert await db.resolve_user_id("209121227925618688") == "nick"

    async def test_returns_false_when_owner_row_missing(self, db):
        linked = await db.link_owner_discord_id("nobody", "123")
        assert linked is False

    async def test_does_not_steal_discord_id_from_another_user(self, db):
        # A forked user already owns this discord_id.
        await db.create_discord_user(
            discord_id="555", name="Forked", discord_username="forked"
        )
        await self._insert_owner(db)
        linked = await db.link_owner_discord_id("nick", "555")
        assert linked is False
        # The forked row keeps ownership; the owner row stays unlinked.
        assert await db.resolve_user_id("555") == "forked"


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
