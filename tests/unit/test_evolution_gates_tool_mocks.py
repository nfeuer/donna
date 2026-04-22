"""F-W1-B regression: EvolutionGates thread tool_mocks through all three gates.

Without these fixes, any skill with tool steps permanently fails gates 2/3/4
because ValidationExecutor -> MockToolRegistry deny-closes unmocked tool calls.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.evolution_gates import EvolutionGates


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


class _CapturingExecutor:
    """Executor mock that records every call's kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        from donna.skills.executor import SkillRunResult
        return SkillRunResult(status="succeeded", final_output={"ok": True})


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


async def test_fixture_regression_gate_passes_tool_mocks_from_fixture_row(db) -> None:
    mocks_expected = {
        'web_fetch:{"url":"https://example.com"}': {"status": 200, "body": "OK"},
    }
    await db.execute(
        "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
        "expected_output_shape, source, created_at, tool_mocks) "
        "VALUES ('f1', 's1', 'case1', ?, NULL, 'human_written', "
        "'2026-01-01', ?)",
        (
            json.dumps({"url": "https://example.com"}),
            json.dumps(mocks_expected),
        ),
    )
    await db.commit()

    executor = _CapturingExecutor()
    config = SkillSystemConfig(evolution_fixture_regression_pass_rate=0.95)
    gates = EvolutionGates(db, config, executor)

    result = await gates.run_fixture_regression_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
    )
    assert result.passed is True
    assert len(executor.calls) == 1
    assert executor.calls[0].get("tool_mocks") == mocks_expected


async def test_fixture_regression_gate_handles_null_tool_mocks(db) -> None:
    """A fixture without tool_mocks yields tool_mocks=None (not a crash)."""
    await db.execute(
        "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
        "expected_output_shape, source, created_at, tool_mocks) "
        "VALUES ('f2', 's1', 'case2', '{}', NULL, 'seed', "
        "'2026-01-01', NULL)",
    )
    await db.commit()

    executor = _CapturingExecutor()
    config = SkillSystemConfig(evolution_fixture_regression_pass_rate=0.95)
    gates = EvolutionGates(db, config, executor)

    await gates.run_fixture_regression_gate(
        new_version=_valid_new_version(), skill_id="s1",
    )
    assert len(executor.calls) == 1
    assert executor.calls[0].get("tool_mocks") is None


async def test_targeted_case_gate_synthesizes_mocks_from_cache(db) -> None:
    cache = {
        "c1": {
            "tool": "web_fetch",
            "args": {"url": "https://example.com"},
            "result": {"status": 200, "body": "captured"},
        }
    }
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, status, state_object, "
        "user_id, started_at, tool_result_cache) VALUES "
        "('r1', 's1', 'succeeded', ?, 'u', '2026-01-01', ?)",
        (
            json.dumps({"inputs": {"url": "https://example.com"}}),
            json.dumps(cache),
        ),
    )
    await db.commit()

    executor = _CapturingExecutor()
    config = SkillSystemConfig(evolution_targeted_case_pass_rate=0.80)
    gates = EvolutionGates(db, config, executor)

    await gates.run_targeted_case_gate(
        new_version=_valid_new_version(),
        skill_id="s1",
        targeted_case_ids=["r1"],
    )
    assert len(executor.calls) == 1
    mocks = executor.calls[0].get("tool_mocks")
    assert mocks is not None
    # fingerprint-keyed; assert the result matches the captured payload
    assert any(v == {"status": 200, "body": "captured"} for v in mocks.values())
    assert any("web_fetch" in k for k in mocks)


async def test_recent_success_gate_synthesizes_mocks_from_cache(db) -> None:
    now = datetime.now(UTC).isoformat()
    cache = {
        "c1": {
            "tool": "web_fetch",
            "args": {"url": "https://example.com"},
            "result": {"status": 200, "body": "OK"},
        }
    }
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, status, state_object, "
        "user_id, started_at, tool_result_cache) VALUES "
        "('r1', 's1', 'succeeded', ?, 'u', ?, ?)",
        (
            json.dumps({"inputs": {"url": "https://example.com"}}),
            now,
            json.dumps(cache),
        ),
    )
    await db.commit()

    executor = _CapturingExecutor()
    config = SkillSystemConfig(evolution_recent_success_count=1)
    gates = EvolutionGates(db, config, executor)

    await gates.run_recent_success_gate(
        new_version=_valid_new_version(), skill_id="s1",
    )
    assert len(executor.calls) >= 1
    mocks = executor.calls[0].get("tool_mocks")
    assert mocks is not None
    assert any("web_fetch" in k for k in mocks)


async def test_recent_success_gate_tolerates_null_cache(db) -> None:
    """A run without tool_result_cache yields tool_mocks={} (not a crash)."""
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, status, state_object, "
        "user_id, started_at, tool_result_cache) VALUES "
        "('r_empty', 's1', 'succeeded', '{}', 'u', ?, NULL)",
        (now,),
    )
    await db.commit()

    executor = _CapturingExecutor()
    config = SkillSystemConfig(evolution_recent_success_count=1)
    gates = EvolutionGates(db, config, executor)

    await gates.run_recent_success_gate(
        new_version=_valid_new_version(), skill_id="s1",
    )
    assert len(executor.calls) == 1
    assert executor.calls[0].get("tool_mocks") == {}
