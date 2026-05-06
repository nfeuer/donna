"""Slice 25 — :meth:`EscalationRepository.find_chain_depth` regression.

The recursive-CTE chain walk underpins both
:meth:`EscalationGate.fire_and_wait`'s depth-cap enforcement and
:class:`ReEscalationCoordinator`'s pre-fire fast-fail. A wrong walk
produces either runaway recursion (depth never converges) or
prematurely capped chains (the test grid below would catch both).

Realises docs/superpowers/specs/manual-escalation.md §10.6 row 1
(re-estimate + re-escalation) and §12 Q5 (depth cap).
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from donna.cost.escalation_repository import EscalationRepository

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
CREATE INDEX ix_escalation_request_parent_escalation_id
    ON escalation_request(parent_escalation_id);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "chain.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


async def _seed(
    repo: EscalationRepository,
    *,
    user_id: str,
    correlation_id: str,
    parent: int | None = None,
):
    return await repo.create(
        user_id=user_id,
        correlation_id=correlation_id,
        task_id=None,
        task_type="chat_escalation",
        estimate_usd=1.0,
        daily_remaining_usd=0.0,
        offered_modes=["api_extended", "pause", "cancel"],
        priority=2,
        parent_escalation_id=parent,
    )


class TestFindChainDepth:
    async def test_root_row_depth_zero(self, repo: EscalationRepository) -> None:
        row = await _seed(repo, user_id="nick", correlation_id="root")
        assert await repo.find_chain_depth(row.id) == 0

    async def test_direct_child_depth_one(self, repo: EscalationRepository) -> None:
        parent = await _seed(repo, user_id="nick", correlation_id="p")
        child = await _seed(
            repo, user_id="nick", correlation_id="c", parent=parent.id
        )
        assert await repo.find_chain_depth(child.id) == 1

    async def test_long_chain_walks_correct_depth(
        self, repo: EscalationRepository
    ) -> None:
        prev_id: int | None = None
        ids: list[int] = []
        for i in range(7):
            row = await _seed(
                repo, user_id="nick", correlation_id=f"link-{i}", parent=prev_id
            )
            ids.append(row.id)
            prev_id = row.id

        # Tip is depth 6 (zero-indexed off root).
        assert await repo.find_chain_depth(ids[-1]) == 6
        # Mid-chain is depth 3.
        assert await repo.find_chain_depth(ids[3]) == 3
        # Root remains depth 0.
        assert await repo.find_chain_depth(ids[0]) == 0

    async def test_null_parent_treated_as_root(
        self, repo: EscalationRepository
    ) -> None:
        row = await _seed(repo, user_id="nick", correlation_id="solo", parent=None)
        assert await repo.find_chain_depth(row.id) == 0

    async def test_nonexistent_id_returns_zero(
        self, repo: EscalationRepository
    ) -> None:
        # Defensive — recursive CTE seeded with no row yields no walk.
        assert await repo.find_chain_depth(9999) == 0

    async def test_chain_owned_by_one_user_does_not_leak(
        self,
        repo: EscalationRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        """Slice 24 multi-user fixture — chain walk respects user_id.

        Both users seed independent chains; the walk follows the FK
        graph naturally. The assertion is that user A's tip-of-chain
        depth doesn't pick up user B's separate chain.
        """
        a, b = two_user_ids
        a_root = await _seed(repo, user_id=a, correlation_id=f"{a}-root")
        a_child = await _seed(
            repo, user_id=a, correlation_id=f"{a}-child", parent=a_root.id
        )
        # User B's chain is unrelated.
        b_root = await _seed(repo, user_id=b, correlation_id=f"{b}-root")
        b_child = await _seed(
            repo, user_id=b, correlation_id=f"{b}-child", parent=b_root.id
        )

        assert await repo.find_chain_depth(a_child.id) == 1
        assert await repo.find_chain_depth(b_child.id) == 1

    async def test_max_walk_caps_runaway(
        self, repo: EscalationRepository
    ) -> None:
        """Defensive — `max_walk` bounds the recursive CTE."""
        prev_id: int | None = None
        ids: list[int] = []
        for i in range(10):
            row = await _seed(
                repo,
                user_id="nick",
                correlation_id=f"capped-{i}",
                parent=prev_id,
            )
            ids.append(row.id)
            prev_id = row.id
        # max_walk=2 should stop the walk early; result is capped at 2.
        capped = await repo.find_chain_depth(ids[-1], max_walk=2)
        assert capped == 2
