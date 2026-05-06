"""Unit tests for ManualValidationRouter._validate_tool (slice 22)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from donna.cost.escalation_repository import EscalationRequestRow
from donna.cost.manual_validation_router import (
    ManualValidationRouter,
)
from donna.cost.tool_lint import ToolLintConfig
from donna.cost.tool_request_repository import ToolRequestRepository

_SCHEMA = """
CREATE TABLE tool_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    proposed_signature TEXT,
    rationale TEXT,
    blocking_capability_id TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'speculative',
    detection_point TEXT,
    snoozed_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_branch TEXT,
    escalation_request_id INTEGER,
    last_pinged_at TEXT
);
CREATE UNIQUE INDEX ix_tool_request_open_user_tool
    ON tool_request(user_id, tool_name) WHERE status = 'open';
"""

_TOOL_SRC_GOOD = (
    "from typing import Any\n"
    "\n"
    "requires_rebuild = False\n"
    "default_timeout_seconds = 5\n"
    "\n"
    "async def foo(x: Any) -> Any:\n"
    "    return x\n"
)
_TEST_SRC_GOOD = (
    "from donna.skills.tool_test_kit import is_inert_at_import\n"
    "\n"
    "def test_no_io_at_import():\n"
    "    is_inert_at_import('donna.skills.tools.foo')\n"
)
_AGENTS_YAML_GOOD = (
    "agents:\n"
    "  - name: writer\n"
    "    tools:\n"
    "      - foo\n"
)


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await c.executescript(_SCHEMA)
        await c.commit()
        yield c


@pytest.fixture
def host_repo():
    """Mock GitRepo where show_file returns from a static dict."""
    files: dict[str, str] = {
        "src/donna/skills/tools/foo.py": _TOOL_SRC_GOOD,
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
        "config/agents.yaml": _AGENTS_YAML_GOOD,
    }
    repo = AsyncMock()

    async def _show_file(branch, path):
        if path not in files:
            from donna.integrations.git_repo import GitRepoError
            raise GitRepoError(f"missing {path}")
        return files[path]

    repo.show_file = _show_file
    repo.files = files
    return repo


def _row(*, originating_id: str | None = "1") -> EscalationRequestRow:
    return EscalationRequestRow(
        id=10,
        user_id="nick",
        correlation_id="corr-1",
        task_id=None,
        task_type="tool_request_fulfillment",
        estimate_usd=0.0,
        daily_remaining_usd=10.0,
        offered_modes=["claude_code"],
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        iteration=1,
        status="submitted",
        created_at=datetime.now(tz=UTC),
        priority=3,
        delivery_status="sent",
        delivery_attempts=1,
        last_delivery_attempt_at=None,
        originating_entity_type="tool_request",
        originating_entity_id=originating_id,
        target_paths={
            "tool": "src/donna/skills/tools/foo.py",
            "tool_test": "tests/skills/tools/test_foo.py",
        },
    )


async def _setup_open_request(conn) -> int:
    repo = ToolRequestRepository(conn)
    from donna.cost.tool_gap import (
        DETECTION_AUTOMATION_CREATE,
        SEVERITY_HIGH,
        ToolGap,
    )
    res = await repo.record(
        ToolGap(
            tool_name="foo",
            user_id="nick",
            severity=SEVERITY_HIGH,
            blocking_capability_id="news_check",
            rationale="cap blocked",
            proposed_signature=None,
            detection_point=DETECTION_AUTOMATION_CREATE,
        )
    )
    return res.row.id


@pytest.mark.asyncio
async def test_validate_tool_passes_clean_branch(conn, host_repo):
    request_id = await _setup_open_request(conn)
    repo = ToolRequestRepository(conn)
    router = ManualValidationRouter(
        conn=conn,
        host_repo=host_repo,
        executor_factory=lambda: None,
        lifecycle=None,  # not exercised on tool path
        tool_request_repo=repo,
        tool_lint_config=ToolLintConfig(),
        host_repo_path=None,  # disable subprocess smoke
        run_tool_import_smoke=False,
    )
    row = _row(originating_id=str(request_id))
    outcome = await router.validate(
        row,
        branch="b/foo",
        diff_paths=list(host_repo.files.keys()),
        actor_id="user-discord",
    )
    assert outcome.passed is True
    refreshed = await repo.get(request_id)
    assert refreshed.status == "completed"
    assert refreshed.resolved_branch == "b/foo"


@pytest.mark.asyncio
async def test_validate_tool_lint_failure_keeps_request_open(conn, host_repo):
    request_id = await _setup_open_request(conn)
    repo = ToolRequestRepository(conn)
    # Mutate the tool source on the fake host_repo to inject a violation.
    host_repo.files["src/donna/skills/tools/foo.py"] = (
        "import anthropic\n"
        "requires_rebuild = False\n"
        "default_timeout_seconds = 5\n"
    )
    router = ManualValidationRouter(
        conn=conn,
        host_repo=host_repo,
        executor_factory=lambda: None,
        lifecycle=None,
        tool_request_repo=repo,
        tool_lint_config=ToolLintConfig(),
        run_tool_import_smoke=False,
    )
    row = _row(originating_id=str(request_id))
    outcome = await router.validate(
        row, branch="b/foo", diff_paths=list(host_repo.files.keys())
    )
    assert outcome.passed is False
    assert any("anthropic" in f["case_name"] or "anthropic" in f["reason"]
               for f in outcome.failures)
    refreshed = await repo.get(request_id)
    assert refreshed.status == "open"  # stays open for iteration


@pytest.mark.asyncio
async def test_validate_tool_unknown_request_id(conn, host_repo):
    repo = ToolRequestRepository(conn)
    router = ManualValidationRouter(
        conn=conn,
        host_repo=host_repo,
        executor_factory=lambda: None,
        lifecycle=None,
        tool_request_repo=repo,
        tool_lint_config=ToolLintConfig(),
        run_tool_import_smoke=False,
    )
    outcome = await router.validate(
        _row(originating_id="999"),
        branch="b/foo",
        diff_paths=list(host_repo.files.keys()),
    )
    assert outcome.passed is False
    assert "not found" in (outcome.reason or "")


@pytest.mark.asyncio
async def test_validate_tool_wrong_originating_entity_type(conn, host_repo):
    repo = ToolRequestRepository(conn)
    router = ManualValidationRouter(
        conn=conn,
        host_repo=host_repo,
        executor_factory=lambda: None,
        lifecycle=None,
        tool_request_repo=repo,
        tool_lint_config=ToolLintConfig(),
        run_tool_import_smoke=False,
    )
    bad = _row(originating_id="1")
    bad = EscalationRequestRow(
        **{
            **{f.name: getattr(bad, f.name) for f in bad.__dataclass_fields__.values()},
            "originating_entity_type": "skill",
        }
    )
    outcome = await router.validate(
        bad, branch="b/foo", diff_paths=list(host_repo.files.keys())
    )
    assert outcome.passed is False
    assert "originating_entity must be" in (outcome.reason or "")
