"""AutoDrafter's fixture output includes tool_mocks; it's persisted to DB.

Task 17 of Wave 1. Covers two cuts:

1. ``AutoDrafter._extract_draft_payload`` threads a ``tool_mocks`` field
   from Claude's response through the parsed fixtures data.
2. The module-level ``_persist_fixture`` helper writes ``tool_mocks`` as
   JSON into the new ``skill_fixture.tool_mocks`` column.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest


def test_extract_draft_payload_reads_tool_mocks() -> None:
    """Parsed fixture items keep the tool_mocks key for downstream use."""
    from donna.skills.auto_drafter import AutoDrafter

    parsed = {
        "skill_yaml": "steps: []\n",
        "step_prompts": {"s": "prompt"},
        "output_schemas": {"s": {"type": "object"}},
        "fixtures": [
            {
                "case_name": "case_a",
                "input": {"url": "https://x"},
                "expected_output_shape": {"type": "object"},
                "tool_mocks": {
                    'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
                },
            },
        ],
    }

    skill_yaml, _step_prompts, _output_schemas, fixtures = (
        AutoDrafter._extract_draft_payload(parsed)
    )
    assert skill_yaml is not None
    assert fixtures is not None
    assert fixtures[0]["tool_mocks"] == {
        'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
    }


def test_extract_draft_payload_tolerates_missing_tool_mocks() -> None:
    """Fixtures that omit tool_mocks (pure-LLM skills) are still valid."""
    from donna.skills.auto_drafter import AutoDrafter

    parsed = {
        "skill_yaml": "steps: []\n",
        "step_prompts": {"s": "prompt"},
        "output_schemas": {"s": {"type": "object"}},
        "fixtures": [
            {
                "case_name": "case_a",
                "input": {"text": "hi"},
                "expected_output_shape": {"type": "object"},
            },
        ],
    }

    _, _, _, fixtures = AutoDrafter._extract_draft_payload(parsed)
    assert fixtures is not None
    assert "tool_mocks" not in fixtures[0] or fixtures[0].get("tool_mocks") is None


def test_prompt_requests_tool_mocks_field() -> None:
    """Claude prompt explicitly asks for tool_mocks alongside input/shape."""
    from donna.skills.auto_drafter import AutoDrafter

    drafter = AutoDrafter.__new__(AutoDrafter)
    capability = {
        "name": "web_summarize",
        "description": "Summarize a URL",
        "input_schema": json.dumps({"type": "object"}),
    }
    prompt = AutoDrafter._build_prompt(drafter, capability, samples=[])
    assert "tool_mocks" in prompt


@pytest.mark.asyncio
async def test_persist_fixture_writes_tool_mocks_to_db(tmp_path: Path) -> None:
    """Persisting a fixture stores tool_mocks as JSON in the skill_fixture row."""
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE skill (
                id TEXT PRIMARY KEY,
                capability_name TEXT NOT NULL UNIQUE,
                current_version_id TEXT,
                state TEXT NOT NULL,
                requires_human_gate INTEGER NOT NULL DEFAULT 0,
                baseline_agreement REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE skill_fixture (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                case_name TEXT NOT NULL,
                input TEXT NOT NULL,
                expected_output_shape TEXT,
                source TEXT NOT NULL,
                captured_run_id TEXT,
                created_at TEXT NOT NULL,
                tool_mocks TEXT
            );
            """
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES ('s1', 'cap', 'draft', 0, "
            "datetime('now'), datetime('now'))"
        )
        await conn.commit()

        from donna.skills.auto_drafter import _persist_fixture
        fixture_id = await _persist_fixture(
            conn=conn,
            skill_id="s1",
            case_name="c1",
            input_={"url": "https://x"},
            expected_output_shape={"type": "object"},
            tool_mocks={'web_fetch:{"url":"https://x"}': {"status": 200}},
            source="claude_generated",
        )
        await conn.commit()
        assert fixture_id

        cursor = await conn.execute(
            "SELECT tool_mocks, input, expected_output_shape, source, "
            "captured_run_id FROM skill_fixture WHERE skill_id = 's1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert json.loads(row[0]) == {
            'web_fetch:{"url":"https://x"}': {"status": 200},
        }
        assert json.loads(row[1]) == {"url": "https://x"}
        assert json.loads(row[2]) == {"type": "object"}
        assert row[3] == "claude_generated"
        assert row[4] is None


@pytest.mark.asyncio
async def test_persist_fixture_null_tool_mocks(tmp_path: Path) -> None:
    """tool_mocks=None stores a NULL column (pure-LLM skill case)."""
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE skill (
                id TEXT PRIMARY KEY,
                capability_name TEXT NOT NULL UNIQUE,
                current_version_id TEXT,
                state TEXT NOT NULL,
                requires_human_gate INTEGER NOT NULL DEFAULT 0,
                baseline_agreement REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE skill_fixture (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                case_name TEXT NOT NULL,
                input TEXT NOT NULL,
                expected_output_shape TEXT,
                source TEXT NOT NULL,
                captured_run_id TEXT,
                created_at TEXT NOT NULL,
                tool_mocks TEXT
            );
            """
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES ('s2', 'cap2', 'draft', 0, "
            "datetime('now'), datetime('now'))"
        )
        await conn.commit()

        from donna.skills.auto_drafter import _persist_fixture
        await _persist_fixture(
            conn=conn,
            skill_id="s2",
            case_name="c_null",
            input_={"text": "x"},
            expected_output_shape=None,
            tool_mocks=None,
            source="claude_generated",
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT tool_mocks, expected_output_shape FROM skill_fixture "
            "WHERE skill_id = 's2'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None
