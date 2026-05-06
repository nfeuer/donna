"""Slice 24 — fills the §10 regression-test gaps the audit flagged.

The §10 rows below were either uncovered or covered only by implicit
proximity. Slice 24 closes the visible holes per the canonical spec
§11 acceptance ("each mitigation has a corresponding fixture").

| Row | Failure | Test in this file |
|-----|---------|-------------------|
| §10.1 row 5 | Wrong-account button click | ``TestOwnerMismatch`` |
| §10.6 row 5 | Hard monthly ceiling reached | ``TestMonthlyCeilingDisablesExtension`` |
| §10.8 row 1 | Vault data posted raw to Discord | ``TestVaultNamesNotValuesInPrompt`` |
| §10.10 row 6 | ``extension_granted`` audit event | ``TestExtensionGrantedAudit`` |

The deferred / out-of-scope rows are tracked in
``docs/superpowers/specs/followups.md`` per slice 24's drift checklist.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord
import pytest
from sqlalchemy import create_engine

from donna.config import (
    BudgetExtensionConfig,
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
    PromptDeliveryConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import EVENT_EXTENSION_GRANTED
from donna.cost.escalation_chat_prompt import ChatPromptBuilder
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tracker import CostSummary, CostTracker
from donna.integrations.discord_views import (
    BudgetEscalationView,
    _ModeButton,
)
from donna.tasks.database import Database
from donna.tasks.db_models import Base


def _config(**overrides) -> ManualEscalationConfig:
    base_kwargs: dict = dict(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(task_approval_threshold_usd=2.0),
        prompt_delivery=PromptDeliveryConfig(),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=20.0,
            hard_monthly_ceiling_usd=100.0,
        ),
    )
    base_kwargs.update(overrides)
    return ManualEscalationConfig(**base_kwargs)


@pytest.fixture
async def db(tmp_path: Path, state_machine):
    db_path = tmp_path / "section_10.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def conn(db: Database) -> aiosqlite.Connection:
    return db.connection


# ---------------------------------------------------------------------
# §10.1 row 5 — wrong-account approval is rejected
# ---------------------------------------------------------------------


class TestOwnerMismatch:
    """The button callback must short-circuit when ``interaction.user.id``
    differs from the view's configured owner. Without this gate, anyone
    in the channel could resolve another user's escalation. The
    rejection path emits ``escalation_owner_mismatch`` so Loki can
    surface attempted leaks per spec §10.1 row 5.
    """

    @pytest.mark.asyncio
    async def test_button_rejects_non_owner_click(self) -> None:
        gate = MagicMock()
        gate.record_user_resolution = AsyncMock()
        gate.grant_budget_extension = AsyncMock()

        view = BudgetEscalationView(
            correlation_id="corr-owner-1",
            offered_modes=["api_extended", "pause", "cancel"],
            owner_discord_id=10001,
            gate=gate,
            estimate_usd=4.0,
        )

        # Pull the api_extended button off the view so we can drive it
        # directly without the discord runtime.
        button = next(
            child
            for child in view.children
            if isinstance(child, _ModeButton)
            and child._mode == "api_extended"
        )
        # Construct a fake non-owner interaction.
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock()
        interaction.user.id = 10002  # NOT owner_discord_id
        interaction.response = AsyncMock()
        interaction.response.send_message = AsyncMock()

        # ``self.view`` is a read-only property bound by discord.py at
        # render time — patch the descriptor for the test so the
        # callback's ``self.view`` lookup returns our test view.
        from unittest.mock import patch

        with patch.object(
            type(button), "view", new=property(lambda _self: view)
        ):
            await button.callback(interaction)

        # Owner check rejected the click — neither path called.
        gate.record_user_resolution.assert_not_called()
        gate.grant_budget_extension.assert_not_called()
        # User got an ephemeral rejection.
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------
# §10.6 row 5 — hard monthly ceiling reached
# ---------------------------------------------------------------------


class TestMonthlyCeilingDisablesExtension:
    """When today's grant would push month-to-date past
    ``hard_monthly_ceiling_usd``, the gate must refuse to grant.
    YAML-only — ``hard_monthly_ceiling_usd`` is not dashboard-mutable
    (defense in depth, §10.7 row 4)."""

    @pytest.mark.asyncio
    async def test_grant_refused_when_monthly_ceiling_blown(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        repo = EscalationRepository(conn)
        resolver = DashboardSettingResolver(repo)
        extension_repo = BudgetExtensionRepository(conn)

        # Pre-existing grants for the current month already saturate
        # the monthly ceiling.
        today = date.today()
        await repo.create(
            user_id="nick",
            correlation_id="prior",
            task_id=None,
            task_type="weekly_review",
            estimate_usd=99.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended"],
            priority=3,
        )
        prior = await repo.get_by_correlation("prior")
        assert prior is not None
        await extension_repo.grant(
            user_id="nick",
            for_date=today,
            amount_usd=99.0,
            granted_by="nick",
            escalation_request_id=prior.id,
            now=datetime.now(tz=UTC),
        )

        # Now try to open + grant a new one — should refuse.
        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )
        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=_config(),
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=AsyncMock(return_value=True),
            extension_repo=extension_repo,
        )

        new_row = await repo.create(
            user_id="nick",
            correlation_id="new-1",
            task_id=None,
            task_type="weekly_review",
            estimate_usd=5.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended", "pause"],
            priority=3,
        )
        _ = new_row  # silence linter

        granted = await gate.grant_budget_extension(
            correlation_id="new-1",
            granted_by="nick",
        )
        # Granting blew the ceiling so the gate refused — None == no row.
        assert granted is None
        # And no new daily_budget_extension row landed.
        cur = await conn.execute(
            "SELECT count(*) FROM daily_budget_extension WHERE voided = 0"
        )
        rows = (await cur.fetchone())[0]
        assert rows == 1  # only the pre-seeded one


# ---------------------------------------------------------------------
# §10.8 row 1 — vault data privacy regression
# ---------------------------------------------------------------------


class TestVaultNamesNotValuesInPrompt:
    """Spec §10.8 row 1 — task contents posted to Discord must
    reference vault entries by *name*, not *value*. The deterministic
    summary fallback path (used whenever the local Ollama
    summariser is down) is the contract we pin: it must NEVER echo
    ``prompt_body`` content into the Discord-bound summary, no
    matter what the body contains.

    The slice-20 ``ChatPromptBuilder._deterministic_summary`` only
    interpolates ``task_type`` + ``estimate_usd`` — the regression
    here pins that the body itself does not flow into the summary
    surface. A future templating change that accidentally pulled
    ``prompt_body`` into the summary string would fail this guard.
    """

    @pytest.mark.asyncio
    async def test_deterministic_summary_does_not_echo_prompt_body(
        self,
        tmp_path: Path,
    ) -> None:
        # Force the fallback path: the router can't even fetch the
        # summariser template, so ``_generate_summary`` returns the
        # deterministic template defined inline.
        router = MagicMock()
        router.get_prompt_template = MagicMock(
            side_effect=RuntimeError("ollama down")
        )
        router.complete = AsyncMock(side_effect=RuntimeError("ollama down"))

        builder = ChatPromptBuilder(
            router=router,
            project_root=Path(__file__).resolve().parents[2],
            config=PromptDeliveryConfig(),
            workspace_root=tmp_path / "workspace",
        )

        secret_value = "sk-ant-api03-FAKE-TEST-VALUE-NEVER-SHIP"
        forged_body = (
            "Original prompt referenced ``vault.api_keys.openai`` "
            f"and ALSO leaked: {secret_value}"
        )
        row = MagicMock()
        row.id = 1
        row.user_id = "nick"
        row.task_type = "chat_escalation"
        row.estimate_usd = 3.0
        row.correlation_id = "vault-1"
        row.task_id = "task-1"

        # Force the private generator to return the deterministic
        # fallback by routing through the public interface with a
        # broken router (the interface's caller would normally write
        # to disk + DB, but we test only the summary contract here).
        summary = await builder._generate_summary(
            row=row, original_prompt=forged_body
        )
        assert summary is not None
        # The forged secret never appears in the rendered summary —
        # only ``task_type`` and ``estimate_usd`` interpolate.
        assert secret_value not in summary
        for forbidden in ("sk-ant-", "AKIA", "AIza", "ghp_", "xoxb-"):
            assert forbidden not in summary
        # Positive check: the deterministic template's stable phrasing
        # is what got rendered, so the contract is what we expect.
        assert "chat_escalation request" in summary


# ---------------------------------------------------------------------
# §10.10 row 6 — extension_granted audit row
# ---------------------------------------------------------------------


class TestExtensionGrantedAudit:
    """``grant_budget_extension`` writes an ``extension_granted``
    audit row carrying ``escalation_request_id``. Without this audit
    row, the slice-19 timeline hides the moment the user approved the
    extension and the cost charged against today never matches the
    grant moment."""

    @pytest.mark.asyncio
    async def test_grant_emits_audit_event(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        repo = EscalationRepository(conn)
        resolver = DashboardSettingResolver(repo)
        extension_repo = BudgetExtensionRepository(conn)

        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )
        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=_config(),
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=AsyncMock(return_value=True),
            extension_repo=extension_repo,
        )

        await repo.create(
            user_id="nick",
            correlation_id="aud-grant",
            task_id="t",
            task_type="weekly_review",
            estimate_usd=4.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended"],
            priority=3,
        )
        granted = await gate.grant_budget_extension(
            correlation_id="aud-grant",
            granted_by="nick",
        )
        assert granted is not None

        # Inspect the audit row.
        row = await repo.get_by_correlation("aud-grant")
        assert row is not None

        cur = await conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE escalation_request_id = ? "
            "AND task_type = 'escalation_lifecycle'",
            (row.id,),
        )
        events = []
        for r in await cur.fetchall():
            payload = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            events.append(payload)
        granted_events = [e for e in events if e.get("event") == EVENT_EXTENSION_GRANTED]
        assert len(granted_events) == 1
        ev = granted_events[0]
        assert ev["amount_usd"] == pytest.approx(4.0)
        assert ev["granted_by"] == "nick"
        assert "extension_id" in ev
