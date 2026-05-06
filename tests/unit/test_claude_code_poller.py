"""Unit tests for ClaudeCodePoller (slice 21).

Drives a real :class:`donna.integrations.git_repo.GitRepo` against a
tmp_path and a stub :class:`ManualValidationRouter` so we can exercise
every transition in :class:`ClaudeCodePoller` without touching
``ValidationExecutor`` (which needs a live model gateway).

Realizes acceptance for docs/superpowers/specs/manual-escalation.md
§5.3 (poller flow), §10.3 row 2 (branch-not-found), §10.3 row 4
(SHA mismatch), §10.4 row 1 (validation failure feedback), §10.4 row 2
(iteration cap → human review).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from donna.cost.claude_code_poller import (
    EVENT_BRANCH_NOT_FOUND,
    EVENT_FAILED,
    EVENT_ITERATION_LIMIT_REACHED,
    EVENT_VALIDATED,
    ClaudeCodePoller,
)
from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.manual_validation_router import ValidationOutcome
from donna.integrations.git_repo import GitRepo

_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER REFERENCES escalation_request(id),
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER REFERENCES escalation_request(id)
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "poller.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
async def host_repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(root=tmp_path / "host")
    await repo.init_if_missing()
    (repo.root / "README.md").write_text("# repo\n")
    await repo.commit(["README.md"], "initial")
    return repo


@dataclasses.dataclass
class StubRouter:
    """Stand-in for ManualValidationRouter — returns canned outcomes."""

    outcome: ValidationOutcome | None = None
    raised: Exception | None = None
    calls: list[Any] = dataclasses.field(default_factory=list)

    async def validate(
        self,
        row: Any,
        *,
        branch: str,
        diff_paths: list[str],
        actor_id: str | None = None,
    ) -> ValidationOutcome:
        self.calls.append(
            {"correlation_id": row.correlation_id, "branch": branch,
             "diff_paths": list(diff_paths), "actor_id": actor_id}
        )
        if self.raised is not None:
            raise self.raised
        assert self.outcome is not None
        return self.outcome


@dataclasses.dataclass
class FeedbackCollector:
    messages: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    async def __call__(self, row: Any, message: str) -> None:
        self.messages.append((row.correlation_id, message))


async def _seed_submitted_row(
    conn: aiosqlite.Connection,
    *,
    correlation_id: str,
    branch: str | None = "escalation/abc-foo",
    target_paths: dict[str, str] | None = None,
    base_sha: str | None = None,
    iteration: int = 1,
    sha: str | None = None,
) -> int:
    target_paths = target_paths or {"skill": "skills/foo/**", "fixtures": "fixtures/foo/**"}
    payload = {"mode": "claude_code", "branch": branch}
    if sha:
        payload["sha"] = sha
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_type, estimate_usd, daily_remaining_usd,
            offered_modes, mode, status, iteration, created_at, priority,
            branch_name, base_sha, target_paths, result, originating_entity_type,
            originating_entity_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "nick", correlation_id, "skill_auto_draft", 6.0, 1.0,
            json.dumps(["claude_code", "pause", "cancel"]),
            "claude_code", "submitted", iteration,
            "2026-05-06T12:00:00+00:00", 2, branch, base_sha,
            json.dumps(target_paths),
            json.dumps(payload),
            "skill_candidate_report",
            "cand-1",
        ),
    )
    await conn.commit()
    rid = cur.lastrowid
    assert rid is not None
    return int(rid)


async def _audit_events(conn: aiosqlite.Connection, request_id: int) -> list[str]:
    cursor = await conn.execute(
        "SELECT output FROM invocation_log WHERE escalation_request_id = ? "
        "AND task_type = ? ORDER BY timestamp ASC",
        (request_id, ESCALATION_TASK_TYPE),
    )
    rows = await cursor.fetchall()
    events: list[str] = []
    for r in rows:
        try:
            payload = json.loads(r[0])
            events.append(payload.get("event"))
        except Exception:
            events.append("(unparseable)")
    return events


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_branch_missing_posts_feedback_and_keeps_status(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-1", branch="escalation/missing-branch"
    )
    fb = FeedbackCollector()
    poller = ClaudeCodePoller(
        repository=repo,
        host_repo=host_repo,
        validation_router=StubRouter(),
        feedback=fb,
    )
    stats = await poller.tick_once()
    assert stats.processed == 1
    assert stats.branch_missing == 1

    # Status stays at submitted so the next push picks it up.
    cur = await conn.execute(
        "SELECT status FROM escalation_request WHERE id = ?", (rid,)
    )
    assert (await cur.fetchone())[0] == "submitted"
    assert any("not found" in m for _, m in fb.messages)
    assert EVENT_BRANCH_NOT_FOUND in await _audit_events(conn, rid)


async def test_in_scope_passing_validation_marks_validated(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    base_sha = await host_repo.head()
    assert base_sha is not None
    # Branch with files entirely under the scope.
    await host_repo._run(["checkout", "-b", "escalation/abc-foo"])
    (host_repo.root / "skills").mkdir()
    (host_repo.root / "skills" / "foo").mkdir()
    (host_repo.root / "skills" / "foo" / "skill.yaml").write_text("capability_name: foo\n")
    await host_repo.commit(["skills/foo/skill.yaml"], "add foo")
    await host_repo._run(["checkout", "main"])

    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-pass",
        branch="escalation/abc-foo", base_sha=base_sha,
    )
    fb = FeedbackCollector()
    router = StubRouter(
        outcome=ValidationOutcome(
            passed=True, skill_id="skill-1", pass_rate=1.0,
            matched_files=["skills/foo/skill.yaml"], failures=[],
        )
    )
    poller = ClaudeCodePoller(
        repository=repo,
        host_repo=host_repo,
        validation_router=router,
        base_ref="main",
        feedback=fb,
    )
    stats = await poller.tick_once()
    assert stats.validated == 1

    cur = await conn.execute(
        "SELECT status, validation_result FROM escalation_request WHERE id = ?",
        (rid,),
    )
    status, result_json = await cur.fetchone()
    assert status == "validated"
    payload = json.loads(result_json)
    assert payload["passed"] is True
    assert payload["skill_id"] == "skill-1"
    assert EVENT_VALIDATED in await _audit_events(conn, rid)


async def test_out_of_scope_diff_marks_failed(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    base_sha = await host_repo.head()
    assert base_sha is not None
    await host_repo._run(["checkout", "-b", "escalation/abc-foo"])
    (host_repo.root / "out_of_scope.py").write_text("nope\n")
    await host_repo.commit(["out_of_scope.py"], "add out-of-scope")
    await host_repo._run(["checkout", "main"])

    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-scope",
        branch="escalation/abc-foo", base_sha=base_sha,
    )
    fb = FeedbackCollector()
    router = StubRouter()  # never reached
    poller = ClaudeCodePoller(
        repository=repo, host_repo=host_repo,
        validation_router=router, base_ref="main", feedback=fb,
    )
    stats = await poller.tick_once()
    assert stats.failed == 1

    cur = await conn.execute(
        "SELECT status, validation_result FROM escalation_request WHERE id = ?",
        (rid,),
    )
    status, result_json = await cur.fetchone()
    assert status == "failed"
    assert "out_of_scope.py" in result_json
    assert router.calls == []  # validator not invoked
    assert any("outside declared scope" in m for _, m in fb.messages)
    assert EVENT_FAILED in await _audit_events(conn, rid)


async def test_failing_validation_marks_failed_and_posts_feedback(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    base_sha = await host_repo.head()
    assert base_sha is not None
    await host_repo._run(["checkout", "-b", "escalation/abc-foo"])
    (host_repo.root / "skills").mkdir()
    (host_repo.root / "skills" / "foo").mkdir()
    (host_repo.root / "skills" / "foo" / "skill.yaml").write_text("x")
    await host_repo.commit(["skills/foo/skill.yaml"], "add foo")
    await host_repo._run(["checkout", "main"])

    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-fail",
        branch="escalation/abc-foo", base_sha=base_sha,
    )
    fb = FeedbackCollector()
    router = StubRouter(
        outcome=ValidationOutcome(
            passed=False, skill_id="skill-2", pass_rate=0.5,
            matched_files=["skills/foo/skill.yaml"],
            failures=[{"case_name": "alpha", "reason": "regex mismatch"}],
            reason="pass rate below threshold",
        )
    )
    poller = ClaudeCodePoller(
        repository=repo, host_repo=host_repo,
        validation_router=router, base_ref="main", feedback=fb,
    )
    stats = await poller.tick_once()
    assert stats.failed == 1

    cur = await conn.execute(
        "SELECT status FROM escalation_request WHERE id = ?", (rid,)
    )
    assert (await cur.fetchone())[0] == "failed"
    assert any("Manual build failed" in m for _, m in fb.messages)
    assert any("alpha" in m for _, m in fb.messages)
    assert EVENT_FAILED in await _audit_events(conn, rid)


async def test_sha_mismatch_marks_failed(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    base_sha = await host_repo.head()
    assert base_sha is not None
    await host_repo._run(["checkout", "-b", "escalation/abc-foo"])
    (host_repo.root / "skills").mkdir()
    (host_repo.root / "skills" / "foo").mkdir()
    (host_repo.root / "skills" / "foo" / "skill.yaml").write_text("x")
    await host_repo.commit(["skills/foo/skill.yaml"], "add foo")
    await host_repo._run(["checkout", "main"])

    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-shamis",
        branch="escalation/abc-foo", base_sha=base_sha,
        sha="0123456deadbeef",  # not the actual tip
    )
    fb = FeedbackCollector()
    poller = ClaudeCodePoller(
        repository=repo, host_repo=host_repo,
        validation_router=StubRouter(), base_ref="main", feedback=fb,
    )
    await poller.tick_once()

    cur = await conn.execute(
        "SELECT status, validation_result FROM escalation_request WHERE id = ?",
        (rid,),
    )
    status, result_json = await cur.fetchone()
    assert status == "failed"
    assert "branch SHA changed" in result_json


async def test_iteration_cap_promotes_to_human_review(
    conn: aiosqlite.Connection,
    host_repo: GitRepo,
) -> None:
    repo = EscalationRepository(conn)
    rid = await _seed_submitted_row(
        conn, correlation_id="cc-cap", iteration=3,
    )
    # Move directly to failed state to simulate post-validation failure
    # at iteration cap (the cap-sweep is what we're testing).
    await conn.execute(
        "UPDATE escalation_request SET status = 'failed' WHERE id = ?",
        (rid,),
    )
    await conn.commit()

    fb = FeedbackCollector()
    poller = ClaudeCodePoller(
        repository=repo, host_repo=host_repo,
        validation_router=StubRouter(),
        manual_iteration_limit=3,
        feedback=fb,
    )
    stats = await poller.tick_once()
    assert stats.iteration_capped == 1

    cur = await conn.execute(
        "SELECT status, human_review FROM escalation_request WHERE id = ?",
        (rid,),
    )
    status, hr = await cur.fetchone()
    assert status == "cancelled"
    assert hr == 1
    assert any("human review" in m.lower() for _, m in fb.messages)
    assert EVENT_ITERATION_LIMIT_REACHED in await _audit_events(conn, rid)


async def test_inactive_when_dependencies_missing() -> None:
    """Fail-soft: poller's run() exits cleanly without host_repo/router."""
    poller = ClaudeCodePoller(
        repository=None,  # type: ignore[arg-type]
        host_repo=None,
        validation_router=None,
    )
    # Should return immediately, not loop.
    await poller.run()
