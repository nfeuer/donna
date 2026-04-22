"""Tests for EvolutionInputBuilder — TDD (tests written before implementation)."""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.evolution_input import EvolutionInputBuilder


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT, input_schema TEXT, trigger_type TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, embedding BLOB
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT NOT NULL UNIQUE,
            current_version_id TEXT, state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            version_number INTEGER NOT NULL, yaml_backbone TEXT NOT NULL,
            step_content TEXT NOT NULL, output_schemas TEXT NOT NULL,
            created_by TEXT NOT NULL, changelog TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            skill_version_id TEXT, task_id TEXT, automation_run_id TEXT,
            status TEXT NOT NULL, total_latency_ms INTEGER,
            total_cost_usd REAL, state_object TEXT NOT NULL,
            tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE skill_divergence (
            id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
            shadow_invocation_id TEXT NOT NULL,
            overall_agreement REAL NOT NULL,
            diff_summary TEXT, flagged_for_evolution INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE skill_fixture (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            case_name TEXT NOT NULL, input TEXT NOT NULL,
            expected_output_shape TEXT, source TEXT NOT NULL,
            captured_run_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE skill_evolution_log (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            from_version_id TEXT NOT NULL, to_version_id TEXT,
            triggered_by TEXT NOT NULL, claude_invocation_id TEXT,
            diagnosis TEXT, targeted_case_ids TEXT,
            validation_results TEXT, outcome TEXT NOT NULL,
            at TEXT NOT NULL
        );
        CREATE TABLE correction_log (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            user_id TEXT NOT NULL, task_type TEXT NOT NULL,
            task_id TEXT NOT NULL, input_text TEXT NOT NULL,
            field_corrected TEXT NOT NULL, original_value TEXT NOT NULL,
            corrected_value TEXT NOT NULL, rule_extracted TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _seed_minimal_skill(db):
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'parse_task', 'Parse a task', '{}', 'on_message', "
        "'active', ?, 'seed')",
        (now,),
    )
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('s1', 'parse_task', 'v1', 'degraded', 0, 0.9, ?, ?)",
        (now, now),
    )
    await db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES "
        "('v1', 's1', 1, 'capability_name: parse_task\\nversion: 1\\nsteps: []\\n', "
        "'{}', '{}', 'seed', 'v1', ?)",
        (now,),
    )
    await db.commit()


async def test_builder_assembles_all_sections(db):
    await _seed_minimal_skill(db)
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'nick', ?)",
        (now,),
    )
    await db.execute(
        "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
        "overall_agreement, diff_summary, created_at) VALUES "
        "('d1', 'r1', 'inv1', 0.4, '{\"diff\": \"mismatch\"}', ?)",
        (now,),
    )
    await db.commit()

    config = SkillSystemConfig(evolution_min_divergence_cases=1)
    builder = EvolutionInputBuilder(db, config)
    package = await builder.build(skill_id="s1")

    assert package["capability"]["name"] == "parse_task"
    assert package["current_version"]["id"] == "v1"
    assert len(package["divergence_cases"]) == 1
    assert package["divergence_cases"][0]["agreement"] == 0.4
    assert package["correction_log"] == []
    assert package["prior_evolution_log"] == []
    assert package["fixture_library"] == []
    assert "stats" in package
    assert package["stats"]["baseline_agreement"] == 0.9


async def test_builder_caps_divergence_cases_at_max(db):
    await _seed_minimal_skill(db)
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'nick', ?)",
        (now,),
    )
    for i in range(40):
        await db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.3, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await db.commit()

    config = SkillSystemConfig(evolution_max_divergence_cases=30)
    builder = EvolutionInputBuilder(db, config)
    package = await builder.build(skill_id="s1")
    assert len(package["divergence_cases"]) == 30


async def test_builder_raises_when_insufficient_divergence(db):
    await _seed_minimal_skill(db)
    config = SkillSystemConfig(evolution_min_divergence_cases=5)
    builder = EvolutionInputBuilder(db, config)
    with pytest.raises(ValueError, match="insufficient divergence"):
        await builder.build(skill_id="s1")


async def test_builder_skill_not_found(db):
    config = SkillSystemConfig()
    builder = EvolutionInputBuilder(db, config)
    with pytest.raises(LookupError, match="skill not found"):
        await builder.build(skill_id="missing")


async def test_builder_includes_correction_log_rows(db):
    await _seed_minimal_skill(db)
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'nick', ?)",
        (now,),
    )
    for i in range(3):
        await db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.3, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    for i in range(3):
        await db.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
            "task_id, input_text, field_corrected, original_value, "
            "corrected_value) VALUES (?, ?, 'nick', 'parse_task', "
            "?, 'input', 'title', 'x', 'y')",
            (f"c{i}", now, f"t{i}"),
        )
    await db.commit()

    config = SkillSystemConfig(evolution_min_divergence_cases=1)
    builder = EvolutionInputBuilder(db, config)
    package = await builder.build(skill_id="s1")
    assert len(package["correction_log"]) == 3
    assert package["correction_log"][0]["field_corrected"] == "title"
