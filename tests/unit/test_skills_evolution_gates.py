"""Tests for EvolutionGates — TDD (tests written before implementation).

Spec §6.6: four validation gates a proposed new skill version must pass.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.evolution_gates import (
    EvolutionGates,
    GateResult,
    run_structural_gate,
)


def _valid_new_version() -> dict:
    return {
        "yaml_backbone": (
            "capability_name: demo\n"
            "version: 2\n"
            "steps:\n"
            "  - name: extract\n"
            "    kind: llm\n"
            "    prompt: steps/extract.md\n"
            "    output_schema: schemas/extract_v1.json\n"
        ),
        "step_content": {"extract": "Extract: {{ inputs.text }}"},
        "output_schemas": {
            "extract": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            }
        },
    }


def test_structural_gate_passes_on_valid_version():
    result = run_structural_gate(_valid_new_version())
    assert result.passed is True
    assert result.details["yaml_parsed"] is True


def test_structural_gate_fails_on_missing_step_content():
    bad = _valid_new_version()
    del bad["step_content"]["extract"]
    result = run_structural_gate(bad)
    assert result.passed is False
    assert "missing step_content" in (result.failure_reason or "")


def test_structural_gate_fails_on_bad_yaml():
    bad = _valid_new_version()
    bad["yaml_backbone"] = "this: is: not: valid: yaml: ::::"
    result = run_structural_gate(bad)
    assert result.passed is False


def test_structural_gate_fails_on_missing_output_schema():
    bad = _valid_new_version()
    del bad["output_schemas"]["extract"]
    result = run_structural_gate(bad)
    assert result.passed is False


def test_structural_gate_fails_on_invalid_jsonschema():
    bad = _valid_new_version()
    bad["output_schemas"]["extract"] = {"type": "not-a-real-type"}
    result = run_structural_gate(bad)
    assert result.passed is False


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            skill_version_id TEXT, status TEXT NOT NULL,
            state_object TEXT NOT NULL, final_output TEXT,
            user_id TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, tool_result_cache TEXT
        );
        CREATE TABLE skill_fixture (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            case_name TEXT NOT NULL, input TEXT NOT NULL,
            expected_output_shape TEXT, source TEXT NOT NULL,
            captured_run_id TEXT, created_at TEXT NOT NULL,
            tool_mocks TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_targeted_gate_pass_rate_above_threshold(db):
    for i in range(5):
        await db.execute(
            "INSERT INTO skill_run (id, skill_id, status, state_object, "
            "user_id, started_at) VALUES (?, 's1', 'succeeded', "
            "'{\"inputs\": {\"text\": \"x\"}}', 'u', '2026-01-01')",
            (f"r{i}",),
        )
    await db.commit()

    executor = MagicMock()
    results = [MagicMock(status="succeeded")] * 4 + [MagicMock(status="escalated")]
    executor.execute = AsyncMock(side_effect=results)

    config = SkillSystemConfig(evolution_targeted_case_pass_rate=0.80)
    gates = EvolutionGates(db, config, executor)

    result = await gates.run_targeted_case_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
        targeted_case_ids=[f"r{i}" for i in range(5)],
    )
    assert result.passed is True
    assert result.details["pass_rate"] == 0.8


async def test_targeted_gate_fails_below_threshold(db):
    for i in range(5):
        await db.execute(
            "INSERT INTO skill_run (id, skill_id, status, state_object, "
            "user_id, started_at) VALUES (?, 's1', 'succeeded', '{}', "
            "'u', '2026-01-01')",
            (f"r{i}",),
        )
    await db.commit()

    executor = MagicMock()
    executor.execute = AsyncMock(
        side_effect=[MagicMock(status="succeeded")] * 2 + [MagicMock(status="failed")] * 3
    )

    config = SkillSystemConfig(evolution_targeted_case_pass_rate=0.80)
    gates = EvolutionGates(db, config, executor)

    result = await gates.run_targeted_case_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
        targeted_case_ids=[f"r{i}" for i in range(5)],
    )
    assert result.passed is False
    assert result.details["pass_rate"] == 0.4


async def test_fixture_regression_gate(db):
    for i in range(10):
        await db.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, created_at) VALUES "
            "(?, 's1', ?, '{\"x\": 1}', NULL, 'seed', '2026-01-01')",
            (f"f{i}", f"case{i}"),
        )
    await db.commit()

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="succeeded"))

    config = SkillSystemConfig(evolution_fixture_regression_pass_rate=0.95)
    gates = EvolutionGates(db, config, executor)
    result = await gates.run_fixture_regression_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
    )
    assert result.passed is True
    assert result.details["pass_rate"] == 1.0


async def test_recent_success_gate_requires_all_succeed(db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        await db.execute(
            "INSERT INTO skill_run (id, skill_id, status, state_object, "
            "user_id, started_at) VALUES (?, 's1', 'succeeded', '{}', "
            "'u', ?)",
            (f"r{i}", now),
        )
    await db.commit()

    executor = MagicMock()
    executor.execute = AsyncMock(
        side_effect=[MagicMock(status="succeeded")] * 2 + [MagicMock(status="failed")]
    )

    config = SkillSystemConfig(evolution_recent_success_count=3)
    gates = EvolutionGates(db, config, executor)
    result = await gates.run_recent_success_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
    )
    assert result.passed is False
    assert result.details["pass_rate"] < 1.0
