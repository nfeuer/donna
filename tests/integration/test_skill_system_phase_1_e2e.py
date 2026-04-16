"""Phase 1 end-to-end integration test.

Verifies the Phase 1 handoff contract:
  H1: capability table has three seeds with embeddings
  H2: CapabilityRegistry.semantic_search returns top-k with scores
  H3: CapabilityMatcher returns HIGH confidence for clear matches
  H4: three seed skills exist in sandbox state
  H5: every skill has a current_version_id with valid step_content
  H6: SkillDatabase can look up skills by capability name
"""

import json
from pathlib import Path

import aiosqlite
import pytest

from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence
from donna.capabilities.registry import CapabilityRegistry
from donna.skills.database import SkillDatabase
from donna.skills.startup import initialize_skill_system


@pytest.fixture
async def initialized_db(tmp_path: Path):
    db_path = tmp_path / "donna_e2e.db"
    conn = await aiosqlite.connect(str(db_path))

    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            input_schema TEXT, trigger_type TEXT, default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active', embedding BLOB,
            created_at TEXT, created_by TEXT, notes TEXT
        );
        CREATE INDEX ix_capability_status ON capability(status);

        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT UNIQUE,
            current_version_id TEXT, state TEXT, requires_human_gate INTEGER,
            baseline_agreement REAL, created_at TEXT, updated_at TEXT
        );
        CREATE INDEX ix_skill_state ON skill(state);

        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT, version_number INTEGER,
            yaml_backbone TEXT, step_content TEXT, output_schemas TEXT,
            created_by TEXT, changelog TEXT, created_at TEXT
        );

        CREATE TABLE skill_state_transition (
            id TEXT PRIMARY KEY, skill_id TEXT, from_state TEXT, to_state TEXT,
            reason TEXT, actor TEXT, actor_id TEXT, at TEXT, notes TEXT
        );
    """)

    seeds = [
        ("seed-parse_task", "parse_task",
         "Extract structured task fields from a natural language message",
         json.dumps({"type": "object", "properties": {"raw_text": {"type": "string"}, "user_id": {"type": "string"}}, "required": ["raw_text", "user_id"]}),
         "on_message"),
        ("seed-dedup_check", "dedup_check",
         "Determine whether two task candidates represent the same work item",
         json.dumps({"type": "object", "properties": {"task_a": {"type": "object"}, "task_b": {"type": "object"}}, "required": ["task_a", "task_b"]}),
         "on_message"),
        ("seed-classify_priority", "classify_priority",
         "Assign a priority level (1-5) to a task based on content and deadline",
         json.dumps({"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "deadline": {"type": ["string", "null"]}}, "required": ["title"]}),
         "on_message"),
    ]
    for cap_id, name, desc, schema, trigger in seeds:
        await conn.execute(
            """INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, 'active', '2026-04-15T00:00:00+00:00', 'seed')""",
            (cap_id, name, desc, schema, trigger),
        )
    await conn.commit()

    await initialize_skill_system(conn, Path("skills"))

    yield conn
    await conn.close()


@pytest.mark.slow
@pytest.mark.integration
async def test_h1_capabilities_have_embeddings(initialized_db):
    """H1: capability table has three seeds with embeddings."""
    cursor = await initialized_db.execute("SELECT name, embedding FROM capability")
    rows = await cursor.fetchall()
    assert len(rows) == 3
    for name, embedding in rows:
        assert embedding is not None, f"capability {name} missing embedding"
        assert len(embedding) == 384 * 4, f"capability {name} wrong embedding size"


@pytest.mark.slow
@pytest.mark.integration
async def test_h2_semantic_search_works(initialized_db):
    """H2: CapabilityRegistry.semantic_search returns top-k with scores."""
    registry = CapabilityRegistry(initialized_db)
    results = await registry.semantic_search("extract task info from this message", k=3)
    assert len(results) == 3
    assert results[0][0].name == "parse_task"
    assert -1.0 <= results[0][1] <= 1.0


@pytest.mark.slow
@pytest.mark.integration
async def test_h3_matcher_high_confidence(initialized_db):
    """H3: CapabilityMatcher returns HIGH confidence for a clear match."""
    registry = CapabilityRegistry(initialized_db)
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("parse task fields from natural language message")
    assert result.confidence == MatchConfidence.HIGH
    assert result.best_match is not None
    assert result.best_match.name == "parse_task"


@pytest.mark.slow
@pytest.mark.integration
async def test_h4_skills_in_sandbox(initialized_db):
    """H4: three seed skills exist in sandbox state."""
    cursor = await initialized_db.execute("SELECT capability_name, state FROM skill ORDER BY capability_name")
    rows = await cursor.fetchall()
    assert len(rows) == 3
    for name, state in rows:
        assert state == "sandbox", f"skill {name} expected sandbox, got {state}"


@pytest.mark.slow
@pytest.mark.integration
async def test_h5_skills_have_versions(initialized_db):
    """H5: every skill has a current_version_id with valid step_content."""
    cursor = await initialized_db.execute("""
        SELECT s.capability_name, v.version_number, v.step_content, v.output_schemas
        FROM skill s
        JOIN skill_version v ON s.current_version_id = v.id
        ORDER BY s.capability_name
    """)
    rows = await cursor.fetchall()
    assert len(rows) == 3
    for name, version_num, step_content, output_schemas in rows:
        assert version_num == 1
        content = json.loads(step_content)
        schemas = json.loads(output_schemas)
        assert len(content) >= 1, f"skill {name} has no step content"
        assert len(schemas) >= 1, f"skill {name} has no output schemas"


@pytest.mark.slow
@pytest.mark.integration
async def test_h6_skill_database_lookup(initialized_db):
    """H6: SkillDatabase can look up skills by capability name."""
    sdb = SkillDatabase(initialized_db)
    for cap_name in ["parse_task", "dedup_check", "classify_priority"]:
        skill = await sdb.get_by_capability(cap_name)
        assert skill is not None, f"SkillDatabase returned None for {cap_name}"
        assert skill.state == "sandbox"
        assert skill.current_version_id is not None

        version = await sdb.get_version(skill.current_version_id)
        assert version is not None
        assert version.version_number == 1
