"""Smoke tests for capability and skill API routes.

These are lightweight tests that verify the routes exist and return
correct shapes. Full integration tests are in test_skill_system_phase_1_e2e.py.
"""

import json
from pathlib import Path

import aiosqlite
import pytest

from donna.capabilities.models import SELECT_CAPABILITY, row_to_capability
from donna.skills.models import SELECT_SKILL, SELECT_SKILL_VERSION, row_to_skill, row_to_skill_version
from donna.skills.database import SkillDatabase


@pytest.fixture
async def db(tmp_path: Path):
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
        VALUES ('c1', 'parse_task', 'desc', '{"type":"object"}', 'on_message', 'active', '2026-04-15', 'seed')
    """)
    await conn.execute("""
        INSERT INTO skill (id, capability_name, current_version_id, state, requires_human_gate, baseline_agreement, created_at, updated_at)
        VALUES ('s1', 'parse_task', 'v1', 'sandbox', 0, NULL, '2026-04-15', '2026-04-15')
    """)
    await conn.execute("""
        INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, step_content, output_schemas, created_by, changelog, created_at)
        VALUES ('v1', 's1', 1, 'yaml: x', '{"extract":"md"}', '{"extract":{}}', 'human', NULL, '2026-04-15')
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_skill_database_get_by_capability(db):
    sdb = SkillDatabase(db)
    skill = await sdb.get_by_capability("parse_task")
    assert skill is not None
    assert skill.capability_name == "parse_task"
    assert skill.state == "sandbox"


async def test_skill_database_get_by_capability_missing(db):
    sdb = SkillDatabase(db)
    skill = await sdb.get_by_capability("nonexistent")
    assert skill is None


async def test_skill_database_get_version(db):
    sdb = SkillDatabase(db)
    version = await sdb.get_version("v1")
    assert version is not None
    assert version.version_number == 1
    assert version.step_content == {"extract": "md"}


async def test_skill_database_list_all(db):
    sdb = SkillDatabase(db)
    skills = await sdb.list_all()
    assert len(skills) == 1


async def test_skill_database_list_all_with_state_filter(db):
    sdb = SkillDatabase(db)
    sandboxed = await sdb.list_all(state="sandbox")
    assert len(sandboxed) == 1
    trusted = await sdb.list_all(state="trusted")
    assert len(trusted) == 0
