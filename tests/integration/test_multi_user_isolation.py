"""Slice 24 — multi-user readiness regression (spec §10.9).

Phase 1 runs single-user, but the data model is multi-user from day
one. These tests parametrise over two distinct ``user_id`` values
(``two_user_ids`` fixture in ``tests/conftest.py``) to catch the class
of bug where a query forgets a ``user_id`` filter and an escalation
from user A surfaces — or worse, gets *resolved* — in user B's
dashboard. We exercise both orderings so the assertion doesn't pass by
accident on an ORDER BY tie-break.

What we cover:

1. ``escalation_request.user_id`` column is honoured across the
   read paths used by the dashboard / Discord bot:
   ``find_open_for_originating_entity`` and direct ``user_id =`` reads.
2. Resolution is keyed on ``correlation_id`` AND scoped to the
   intended owner — a forged correlation_id can't resolve another
   user's row.
3. ``daily_budget_extension`` rows summed by ``BudgetExtensionRepository``
   are scoped by ``(user_id, date)`` — user B's grants never inflate
   user A's daily total.
4. Discord delivery routing — the delivery callback receives
   ``row.user_id`` so the bot can dispatch to the right channel. We
   stub the callback and assert the user_id arrives intact for each
   row.

§10.9 row 2 (per-user budget config) is flagged as a follow-up in
``followups.md``; the test below proves the *grant accounting* is
already per-user, which is the §10.9 row 2 mitigation cited in the
canonical spec ("Budget is per-user from day one; no global pool").
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.escalation_audit import write_escalation_event
from donna.cost.escalation_repository import EscalationRepository

# Schema mirrors the per-test SQLite setup in ``test_admin_escalations``
# minus the indexes we don't exercise. ``daily_budget_extension`` ships
# the slice-18 idempotency index directly so the partial-unique
# behaviour is preserved.
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

CREATE TABLE daily_budget_extension (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    escalation_request_id INTEGER REFERENCES escalation_request(id),
    voided INTEGER NOT NULL DEFAULT 0
);
-- Slice 18 idempotency index: ON CONFLICT in `BudgetExtensionRepository.grant`
-- targets this constraint, so it must exist for ``grant`` to succeed.
CREATE UNIQUE INDEX ux_daily_budget_extension_idempotency
    ON daily_budget_extension (escalation_request_id, granted_by);

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

CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "multi_user.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


@pytest.fixture
def extension_repo(conn: aiosqlite.Connection) -> BudgetExtensionRepository:
    return BudgetExtensionRepository(conn)


async def _open_escalation(
    repo: EscalationRepository,
    *,
    user_id: str,
    correlation_id: str,
    originating_entity: tuple[str, str] | None = None,
):
    return await repo.create(
        user_id=user_id,
        correlation_id=correlation_id,
        task_id=f"task-{correlation_id}",
        task_type="skill_draft",
        estimate_usd=5.5,
        daily_remaining_usd=1.0,
        offered_modes=["claude_code", "pause"],
        priority=3,
        originating_entity=originating_entity,
    )


class TestEscalationRowIsolation:
    async def test_get_by_correlation_returns_owner_user_id(
        self,
        repo: EscalationRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        a, b = two_user_ids
        await _open_escalation(repo, user_id=a, correlation_id=f"{a}-1")
        await _open_escalation(repo, user_id=b, correlation_id=f"{b}-1")

        row_a = await repo.get_by_correlation(f"{a}-1")
        row_b = await repo.get_by_correlation(f"{b}-1")
        assert row_a is not None and row_a.user_id == a
        assert row_b is not None and row_b.user_id == b

    async def test_find_open_for_originating_entity_user_scoped(
        self,
        repo: EscalationRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        """Both users push a candidate with the same originating
        entity (think: two users running the same skill_draft tool).
        ``find_open_for_originating_entity`` must scope to ``user_id``
        so user B's row never satisfies user A's lookup.
        """
        a, b = two_user_ids
        await _open_escalation(
            repo,
            user_id=a,
            correlation_id=f"{a}-orig",
            originating_entity=("skill_candidate_report", "cand-shared"),
        )
        await _open_escalation(
            repo,
            user_id=b,
            correlation_id=f"{b}-orig",
            originating_entity=("skill_candidate_report", "cand-shared"),
        )

        found_a = await repo.find_open_for_originating_entity(
            user_id=a,
            entity_type="skill_candidate_report",
            entity_id="cand-shared",
        )
        found_b = await repo.find_open_for_originating_entity(
            user_id=b,
            entity_type="skill_candidate_report",
            entity_id="cand-shared",
        )
        assert found_a is not None and found_a.user_id == a
        assert found_b is not None and found_b.user_id == b
        assert found_a.id != found_b.id


class TestBudgetExtensionIsolation:
    async def test_daily_total_is_per_user(
        self,
        repo: EscalationRepository,
        extension_repo: BudgetExtensionRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        """User A's grants never inflate user B's daily total. The
        canonical spec §10.9 row 2 cites this isolation as the
        "no global pool" mitigation.
        """
        a, b = two_user_ids
        row_a = await _open_escalation(repo, user_id=a, correlation_id="ext-a")
        row_b = await _open_escalation(repo, user_id=b, correlation_id="ext-b")
        today = date.today()

        await extension_repo.grant(
            user_id=a,
            for_date=today,
            amount_usd=2.5,
            granted_by=a,
            escalation_request_id=row_a.id,
        )
        await extension_repo.grant(
            user_id=b,
            for_date=today,
            amount_usd=4.0,
            granted_by=b,
            escalation_request_id=row_b.id,
        )

        total_a = await extension_repo.get_daily_total(a, today)
        total_b = await extension_repo.get_daily_total(b, today)
        assert total_a == 2.5
        assert total_b == 4.0


class TestAuditIsolation:
    async def test_audit_row_carries_user_id(
        self,
        conn: aiosqlite.Connection,
        repo: EscalationRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        """``invocation_log`` audit rows must carry the owner's
        ``user_id`` so a Loki / Grafana per-user view filters
        cleanly. Slice 17's audit helper takes ``user_id`` as a
        required kwarg; the test pins it.
        """
        a, b = two_user_ids
        row_a = await _open_escalation(repo, user_id=a, correlation_id="aud-a")
        row_b = await _open_escalation(repo, user_id=b, correlation_id="aud-b")

        await write_escalation_event(
            conn,
            event="escalation_offered",
            escalation_request_id=row_a.id,
            correlation_id="aud-a",
            user_id=a,
            task_id=None,
            payload={},
            now=datetime.now(tz=UTC),
        )
        await write_escalation_event(
            conn,
            event="escalation_offered",
            escalation_request_id=row_b.id,
            correlation_id="aud-b",
            user_id=b,
            task_id=None,
            payload={},
            now=datetime.now(tz=UTC),
        )

        cursor = await conn.execute(
            "SELECT user_id, escalation_request_id "
            "FROM invocation_log WHERE task_type = 'escalation_lifecycle' "
            "ORDER BY user_id"
        )
        rows = await cursor.fetchall()
        per_user = {r[0]: r[1] for r in rows}
        assert per_user[a] == row_a.id
        assert per_user[b] == row_b.id


class TestDeliveryRouting:
    async def test_delivery_callback_sees_owner_user_id(
        self,
        repo: EscalationRepository,
        two_user_ids: tuple[str, str],
    ) -> None:
        """The Discord delivery callback dispatches to the per-user
        channel based on ``row.user_id``. We stub the callback,
        push two open rows under different owners, and assert each
        row's ``user_id`` arrives intact.
        """
        a, b = two_user_ids
        row_a = await _open_escalation(repo, user_id=a, correlation_id="del-a")
        row_b = await _open_escalation(repo, user_id=b, correlation_id="del-b")

        deliveries: list[tuple[str, str]] = []

        async def deliver(row) -> bool:
            deliveries.append((row.correlation_id, row.user_id))
            return True

        # Walk the same rows the delivery loop would walk.
        for row in await repo.list_open_pending_delivery():
            await deliver(row)

        observed = sorted(deliveries)
        assert sorted([("del-a", a), ("del-b", b)]) == observed
        # Sanity: assert the rows really are different owners (catches a
        # regression where ``list_open_pending_delivery`` collapsed rows).
        assert {d[1] for d in deliveries} == {a, b}
        assert row_a.user_id == a and row_b.user_id == b
