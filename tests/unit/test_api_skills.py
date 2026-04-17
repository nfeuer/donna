"""Tests for skills API routes — including new state-transition and flag endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException

from donna.skills.lifecycle import (
    HumanGateRequiredError,
    IllegalTransitionError,
    SkillNotFoundError,
)


SCHEMA = """
    CREATE TABLE skill (
        id TEXT PRIMARY KEY,
        capability_name TEXT UNIQUE,
        current_version_id TEXT,
        state TEXT,
        requires_human_gate INTEGER,
        baseline_agreement REAL,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE skill_version (
        id TEXT PRIMARY KEY,
        skill_id TEXT,
        version_number INTEGER,
        yaml_backbone TEXT,
        step_content TEXT,
        output_schemas TEXT,
        created_by TEXT,
        changelog TEXT,
        created_at TEXT
    );
    CREATE TABLE skill_state_transition (
        id TEXT PRIMARY KEY,
        skill_id TEXT,
        from_state TEXT,
        to_state TEXT,
        reason TEXT,
        actor TEXT,
        actor_id TEXT,
        at TEXT,
        notes TEXT
    );
    CREATE TABLE skill_run (
        id TEXT PRIMARY KEY,
        skill_id TEXT,
        skill_version_id TEXT,
        status TEXT,
        state_object TEXT,
        user_id TEXT,
        started_at TEXT
    );
    CREATE TABLE skill_divergence (
        id TEXT PRIMARY KEY,
        skill_run_id TEXT,
        shadow_invocation_id TEXT,
        overall_agreement REAL,
        diff_summary TEXT,
        created_at TEXT
    );
"""


@pytest.fixture
async def db_with_skill(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(SCHEMA)
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("s1", "parse_task", "v1", "sandbox", 0, None, "2026-04-01T00:00:00", "2026-04-10T00:00:00"),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("v1", "s1", 1, "yaml: x", '{"extract":"md"}', '{"extract":{}}', "human", None, "2026-04-01T00:00:00"),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_request(conn, lifecycle=None):
    request = MagicMock()
    request.app.state.db.connection = conn
    request.app.state.skill_lifecycle_manager = lifecycle
    return request


# ---------------------------------------------------------------------------
# GET endpoints (smoke tests)
# ---------------------------------------------------------------------------

async def test_list_skills_returns_skill(db_with_skill):
    from donna.api.routes.skills import list_skills

    request = _make_request(db_with_skill)
    result = await list_skills(request=request, state=None, limit=100)

    assert result["count"] == 1
    assert result["skills"][0]["id"] == "s1"


async def test_get_skill_returns_skill_with_version(db_with_skill):
    from donna.api.routes.skills import get_skill

    request = _make_request(db_with_skill)
    result = await get_skill(skill_id="s1", request=request)

    assert result["id"] == "s1"
    assert result["current_version"]["version_number"] == 1


async def test_get_skill_404(db_with_skill):
    from donna.api.routes.skills import get_skill

    request = _make_request(db_with_skill)
    with pytest.raises(HTTPException) as excinfo:
        await get_skill(skill_id="missing", request=request)
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /skills/{id}/state
# ---------------------------------------------------------------------------

async def test_post_state_transition_happy_path(db_with_skill):
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    lifecycle = AsyncMock()
    lifecycle.transition = AsyncMock(return_value=None)

    request = _make_request(db_with_skill, lifecycle=lifecycle)
    body = TransitionRequest(to_state="shadow_primary", reason="gate_passed")

    result = await transition_skill_state(skill_id="s1", body=body, request=request)

    assert result["ok"] is True
    assert result["skill_id"] == "s1"
    assert result["to_state"] == "shadow_primary"
    lifecycle.transition.assert_called_once()


async def test_post_state_transition_503_when_no_config(db_with_skill):
    """Post Task 14: if lifecycle isn't pre-wired the route constructs one on
    demand from ``app.state.skill_system_config``. Only when BOTH are missing
    does the route return 503."""
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    request = _make_request(db_with_skill, lifecycle=None)
    # Explicitly clear the auto-constructed MagicMock attribute.
    request.app.state.skill_system_config = None
    body = TransitionRequest(to_state="shadow_primary", reason="gate_passed")

    with pytest.raises(HTTPException) as excinfo:
        await transition_skill_state(skill_id="s1", body=body, request=request)
    assert excinfo.value.status_code == 503


async def test_post_state_transition_404_missing_skill(db_with_skill):
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    lifecycle = AsyncMock()
    lifecycle.transition = AsyncMock(side_effect=SkillNotFoundError("not found"))

    request = _make_request(db_with_skill, lifecycle=lifecycle)
    body = TransitionRequest(to_state="shadow_primary", reason="gate_passed")

    with pytest.raises(HTTPException) as excinfo:
        await transition_skill_state(skill_id="missing", body=body, request=request)
    assert excinfo.value.status_code == 404


async def test_post_state_transition_400_invalid_state(db_with_skill):
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    lifecycle = AsyncMock()
    request = _make_request(db_with_skill, lifecycle=lifecycle)
    body = TransitionRequest(to_state="not_a_real_state", reason="gate_passed")

    with pytest.raises(HTTPException) as excinfo:
        await transition_skill_state(skill_id="s1", body=body, request=request)
    assert excinfo.value.status_code == 400
    assert "invalid state" in excinfo.value.detail


async def test_post_state_transition_400_illegal_transition(db_with_skill):
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    lifecycle = AsyncMock()
    lifecycle.transition = AsyncMock(
        side_effect=IllegalTransitionError("sandbox → trusted is not permitted")
    )

    request = _make_request(db_with_skill, lifecycle=lifecycle)
    body = TransitionRequest(to_state="trusted", reason="gate_passed")

    with pytest.raises(HTTPException) as excinfo:
        await transition_skill_state(skill_id="s1", body=body, request=request)
    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# POST /skills/{id}/flags/requires_human_gate
# ---------------------------------------------------------------------------

async def test_post_human_gate_toggles_value(db_with_skill):
    from donna.api.routes.skills import HumanGateRequest, set_requires_human_gate

    request = _make_request(db_with_skill)
    body = HumanGateRequest(value=True)

    result = await set_requires_human_gate(skill_id="s1", body=body, request=request)

    assert result["skill_id"] == "s1"
    assert result["requires_human_gate"] is True

    # Verify persisted
    cursor = await db_with_skill.execute(
        "SELECT requires_human_gate FROM skill WHERE id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_post_human_gate_404_missing_skill(db_with_skill):
    from donna.api.routes.skills import HumanGateRequest, set_requires_human_gate

    request = _make_request(db_with_skill)
    body = HumanGateRequest(value=True)

    with pytest.raises(HTTPException) as excinfo:
        await set_requires_human_gate(skill_id="missing", body=body, request=request)
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# AS-4.2: baseline reset on flagged_for_review → trusted (human_approval)
# ---------------------------------------------------------------------------

async def test_post_state_transition_resets_baseline_on_save(db_with_skill):
    """When flagged_for_review → trusted with reason=human_approval,
    baseline_agreement is recomputed from recent divergences."""
    import pytest as _pytest
    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    now = "2026-04-16T00:00:00+00:00"

    # Update the pre-seeded skill to flagged_for_review with baseline 0.95
    await db_with_skill.execute(
        "UPDATE skill SET state = 'flagged_for_review', baseline_agreement = 0.95 WHERE id = 's1'"
    )
    await db_with_skill.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'u', ?)",
        (now,),
    )
    for i in range(10):
        await db_with_skill.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.85, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await db_with_skill.commit()

    lifecycle = AsyncMock()
    lifecycle.transition = AsyncMock(return_value=None)

    request = _make_request(db_with_skill, lifecycle=lifecycle)
    body = TransitionRequest(to_state="trusted", reason="human_approval")

    result = await transition_skill_state(skill_id="s1", body=body, request=request)
    assert result["ok"] is True

    cursor = await db_with_skill.execute(
        "SELECT baseline_agreement FROM skill WHERE id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == _pytest.approx(0.85, abs=0.01)
