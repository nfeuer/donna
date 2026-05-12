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
