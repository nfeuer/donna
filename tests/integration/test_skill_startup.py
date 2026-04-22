from pathlib import Path

import aiosqlite
import pytest

from donna.skills.startup import initialize_skill_system


@pytest.fixture
async def db_with_seed_caps(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            input_schema TEXT, trigger_type TEXT, default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active', embedding BLOB,
            created_at TEXT, created_by TEXT, notes TEXT
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT UNIQUE,
            current_version_id TEXT, state TEXT, requires_human_gate INTEGER,
            baseline_agreement REAL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT, version_number INTEGER,
            yaml_backbone TEXT, step_content TEXT, output_schemas TEXT,
            created_by TEXT, changelog TEXT, created_at TEXT
        );
    """)
    await conn.execute("""
        INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by)
        VALUES ('seed-parse_task', 'parse_task', 'Extract structured task fields',
                '{"type": "object", "properties": {"raw_text": {"type": "string"}}}',
                'on_message', 'active', '2026-04-15T00:00:00+00:00', 'seed')
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.slow
async def test_initialize_fills_embeddings_and_loads_skills(db_with_seed_caps, tmp_path):
    skills_dir = Path("skills")
    await initialize_skill_system(db_with_seed_caps, skills_dir)

    cursor = await db_with_seed_caps.execute(
        "SELECT embedding FROM capability WHERE name = 'parse_task'"
    )
    row = await cursor.fetchone()
    assert row[0] is not None
    assert len(row[0]) == 384 * 4

    cursor = await db_with_seed_caps.execute(
        "SELECT state, current_version_id FROM skill WHERE capability_name = 'parse_task'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "sandbox"
    assert row[1] is not None


@pytest.mark.slow
async def test_initialize_is_idempotent(db_with_seed_caps, tmp_path):
    skills_dir = Path("skills")
    await initialize_skill_system(db_with_seed_caps, skills_dir)
    await initialize_skill_system(db_with_seed_caps, skills_dir)

    cursor = await db_with_seed_caps.execute("SELECT COUNT(*) FROM skill WHERE capability_name = 'parse_task'")
    count = (await cursor.fetchone())[0]
    assert count == 1


@pytest.mark.slow
async def test_initialize_returns_registry_with_default_tools(db_with_seed_caps, tmp_path):
    skills_dir = Path("skills")
    registry = await initialize_skill_system(db_with_seed_caps, skills_dir)

    assert registry is not None
    assert "web_fetch" in registry.list_tool_names()
