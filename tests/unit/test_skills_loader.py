import json
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.loader import SkillLoadError, load_skill_from_directory


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
        VALUES ('c1', 'parse_task', 'parse task', '{}', 'on_message', 'active', '2026-04-15T00:00:00+00:00', 'seed')
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_load_skill_from_directory(db, tmp_path: Path):
    skill_dir = tmp_path / "skills" / "parse_task"
    skill_dir.mkdir(parents=True)

    (skill_dir / "skill.yaml").write_text("""
capability_name: parse_task
version: 1
description: |
  Extract task fields.
inputs:
  schema:
    type: object
    properties: {}
steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/extract_v1.json
final_output: "{{ state.extract }}"
""")
    (skill_dir / "steps").mkdir()
    (skill_dir / "steps" / "extract.md").write_text("Extract the task fields.")
    (skill_dir / "schemas").mkdir()
    (skill_dir / "schemas" / "extract_v1.json").write_text(
        '{"type": "object", "properties": {"title": {"type": "string"}}}'
    )

    skill_id = await load_skill_from_directory(skill_dir, db, initial_state="sandbox")

    cursor = await db.execute("SELECT capability_name, state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row == ("parse_task", "sandbox")

    cursor = await db.execute(
        "SELECT version_number, step_content, output_schemas FROM skill_version WHERE skill_id = ?",
        (skill_id,),
    )
    vrow = await cursor.fetchone()
    assert vrow[0] == 1
    assert json.loads(vrow[1]) == {"extract": "Extract the task fields."}
    assert json.loads(vrow[2]) == {"extract": {"type": "object", "properties": {"title": {"type": "string"}}}}


async def test_load_skill_missing_capability_raises(db, tmp_path: Path):
    skill_dir = tmp_path / "skills" / "nonexistent"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text("""
capability_name: nonexistent
version: 1
description: x
inputs:
  schema: {type: object}
steps: []
final_output: "{}"
""")
    (skill_dir / "steps").mkdir()
    (skill_dir / "schemas").mkdir()

    with pytest.raises(SkillLoadError, match="capability 'nonexistent' not found"):
        await load_skill_from_directory(skill_dir, db, initial_state="sandbox")
