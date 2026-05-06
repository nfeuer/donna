"""Slice 25 — :class:`ReEscalationCoordinator` regression battery.

Pins the catch-and-re-fire loop that closes spec §10.6 row 1
(re-estimate + re-escalation on token cap). The coordinator's
contract is:

  * Computes a new estimate (previous × multiplier, clamped to monthly
    headroom; floored by the parent's granted extension when known).
  * Walks the parent chain and short-circuits when ``max_re_escalation_depth``
    would be exceeded — emits ``re_escalation_token_limited`` and
    returns a synthetic cancel outcome that the router translates back
    to the original :class:`TokenLimitReachedError`.
  * Honours its own ``max_in_flight_attempts`` so a misconfigured
    persisted cap can't drive unbounded recursion.
  * Threads ``parent_escalation_id`` to the gate so the new row is
    persisted as a chain link.

Tests use a fake gate that records :meth:`fire_and_wait` calls and
returns a configurable outcome; the gate's real chain-cap path is
covered by ``test_escalation_gate_chain_cap.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    BudgetExtensionConfig,
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.escalation_gate import GateOutcome
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.re_escalation_coordinator import ReEscalationCoordinator
from donna.models.router import TokenLimitReachedError

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
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    output TEXT,
    is_shadow INTEGER DEFAULT 0,
    spot_check_queued INTEGER DEFAULT 0,
    user_id TEXT NOT NULL,
    escalation_request_id INTEGER
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "coord.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


def _config(
    *,
    max_depth: int = 5,
    multiplier: float = 2.0,
    monthly_ceiling: float = 1000.0,
) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(
            max_re_escalation_depth=max_depth,
            re_escalation_estimate_multiplier=multiplier,
        ),
        budget_extension=BudgetExtensionConfig(
            hard_monthly_ceiling_usd=monthly_ceiling,
        ),
    )


def _ext_repo(*, monthly_total: float = 0.0) -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=monthly_total)
    return stub


def _gate_returning(outcome: GateOutcome) -> MagicMock:
    fake = MagicMock()
    fake.fire_and_wait = AsyncMock(return_value=outcome)
    return fake


async def _seed_parent(
    repo: EscalationRepository,
    *,
    user_id: str = "nick",
    correlation_id: str = "parent",
    estimate_usd: float = 5.0,
    parent: int | None = None,
):
    return await repo.create(
        user_id=user_id,
        correlation_id=correlation_id,
        task_id=None,
        task_type="chat_escalation",
        estimate_usd=estimate_usd,
        daily_remaining_usd=0.0,
        offered_modes=["api_extended", "pause"],
        priority=2,
        parent_escalation_id=parent,
    )


def _outcome(
    *, mode: str, escalation_request_id: int = 99, correlation_id: str = "child"
) -> GateOutcome:
    return GateOutcome(
        fired=True,
        mode=mode,  # type: ignore[arg-type]
        resolved_by="user",
        escalation_request_id=escalation_request_id,
        correlation_id=correlation_id,
        extension_amount_usd=10.0 if mode == "api_extended" else None,
    )


class TestRecover:
    async def test_happy_path_grants_new_extension(
        self,
        repo: EscalationRepository,
    ) -> None:
        parent = await _seed_parent(repo, estimate_usd=5.0)
        gate = _gate_returning(
            _outcome(mode="api_extended", escalation_request_id=999)
        )
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(),
        )
        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=5.0,
        )
        assert decision.outcome.mode == "api_extended"
        assert decision.chain_capped is False
        # Re-estimate = 5.0 × 2.0 = 10.0; ceiling 1000 doesn't clamp.
        assert decision.new_estimate_usd == pytest.approx(10.0)
        # Gate received parent_escalation_id.
        kwargs = gate.fire_and_wait.await_args.kwargs
        assert kwargs["parent_escalation_id"] == parent.id

    async def test_chain_cap_short_circuit(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        # Build a chain whose tip is already at depth 5 (cap).
        parent_id: int | None = None
        for i in range(6):
            row = await _seed_parent(
                repo,
                correlation_id=f"chain-{i}",
                parent=parent_id,
            )
            parent_id = row.id
        assert parent_id is not None

        gate = _gate_returning(_outcome(mode="api_extended"))
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(max_depth=5),
        )

        last_correlation = (await repo.get(parent_id)).correlation_id  # type: ignore[union-attr]
        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent_id,
                correlation_id=last_correlation,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
        )
        assert decision.chain_capped is True
        assert decision.outcome.mode == "cancel"
        # Gate was never asked to fire — the coordinator pre-empted.
        gate.fire_and_wait.assert_not_called()
        # Audit row landed.
        cursor = await conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE escalation_request_id = ? AND task_type = 'escalation_lifecycle'",
            (parent_id,),
        )
        events = [json.loads(r[0]) for r in await cursor.fetchall()]
        assert any(e["event"] == "re_escalation_token_limited" for e in events)

    async def test_user_picks_pause_surfaces_outcome(
        self,
        repo: EscalationRepository,
    ) -> None:
        """When recovery resolves to a non-recoverable mode, the
        coordinator returns the gate's outcome unchanged so the router
        can re-raise as an EscalationDecisionError."""
        parent = await _seed_parent(repo)
        gate = _gate_returning(_outcome(mode="pause"))
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(),
        )
        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
        )
        assert decision.outcome.mode == "pause"
        assert decision.chain_capped is False

    async def test_in_flight_cap_exhaustion(
        self,
        repo: EscalationRepository,
    ) -> None:
        parent = await _seed_parent(repo)
        gate = _gate_returning(_outcome(mode="api_extended"))
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(),
            max_in_flight_attempts=2,
        )

        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
            attempts_remaining=0,
        )
        assert decision.chain_capped is True
        assert decision.outcome.mode == "cancel"
        gate.fire_and_wait.assert_not_called()

    async def test_estimate_clamped_to_monthly_headroom(
        self,
        repo: EscalationRepository,
    ) -> None:
        parent = await _seed_parent(repo)
        gate = _gate_returning(_outcome(mode="api_extended"))
        # Monthly total 95 of 100 → headroom 5. Multiplier × previous (5.0) = 10
        # would breach, so clamp to 5.
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(monthly_total=95.0),
            manual_escalation_config=_config(monthly_ceiling=100.0),
        )
        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
        )
        # Clamped: candidate 10, headroom 5, floor previous_estimate 5.
        assert decision.new_estimate_usd == pytest.approx(5.0)

    async def test_extension_floor_overrides_lower_previous(
        self,
        repo: EscalationRepository,
    ) -> None:
        """When the parent's granted extension was higher than the
        previous_estimate (e.g. user already approved something bigger),
        the new estimate must use that as a floor."""
        parent = await _seed_parent(repo, estimate_usd=5.0)
        gate = _gate_returning(_outcome(mode="api_extended"))
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(multiplier=2.0),
        )
        await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=3.0,
            previous_extension_usd=8.0,
        )
        kwargs = gate.fire_and_wait.await_args.kwargs
        # floor = max(3.0, 8.0) = 8.0; × 2.0 = 16.0
        assert kwargs["estimate_usd"] == pytest.approx(16.0)

    async def test_gate_chain_cap_returned_outcome_marks_capped(
        self,
        repo: EscalationRepository,
    ) -> None:
        """If the gate itself returns the chain-cap synthetic cancel
        (id == parent_id), the coordinator marks the decision capped
        even though it didn't pre-empt."""
        parent = await _seed_parent(repo)
        # Gate returns synthetic cancel keyed off the parent's id.
        gate = _gate_returning(
            GateOutcome(
                fired=True,
                mode="cancel",
                resolved_by="system",
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            )
        )
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(max_depth=99),  # coord won't pre-empt
        )
        decision = await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
        )
        assert decision.chain_capped is True

    async def test_audit_carries_root_correlation_for_user_resolved_chain(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """When the user resolves the recovery to a non-recoverable
        mode, the coordinator emits ``re_escalation_token_limited`` so
        the chain has an explicit termination marker."""
        parent = await _seed_parent(repo)
        gate = _gate_returning(
            _outcome(mode="chat", escalation_request_id=parent.id + 100)
        )
        coord = ReEscalationCoordinator(
            gate=gate,
            repo=repo,
            extension_repo=_ext_repo(),
            manual_escalation_config=_config(),
        )
        await coord.recover(
            token_error=TokenLimitReachedError(
                escalation_request_id=parent.id,
                correlation_id=parent.correlation_id,
            ),
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            priority=2,
            originating_entity=None,
            target_paths=None,
            base_sha=None,
            original_prompt="hello",
            previous_estimate_usd=5.0,
            previous_extension_usd=None,
        )
        cursor = await conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE escalation_request_id = ? AND task_type = 'escalation_lifecycle'",
            (parent.id,),
        )
        events = [json.loads(r[0]) for r in await cursor.fetchall()]
        token_limited = [
            e for e in events if e["event"] == "re_escalation_token_limited"
        ]
        assert len(token_limited) == 1
        assert token_limited[0]["last_outcome_mode"] == "chat"
