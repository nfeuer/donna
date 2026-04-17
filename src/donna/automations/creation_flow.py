"""AutomationCreationPath — final step of the Discord NL creation flow.

Invoked when the user clicks Approve on an AutomationConfirmationView.
Writes the automation row. Idempotent on (user_id, name) uniqueness — a
second approve returns ``None`` instead of creating a duplicate.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.automations.repository import AlreadyExistsError
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

logger = structlog.get_logger()


class AutomationCreationPath:
    """Persist a Discord-approved ``DraftAutomation`` via the repository."""

    def __init__(self, *, repository: Any) -> None:
        self._repo = repository

    async def approve(self, draft: DraftAutomation, *, name: str) -> str | None:
        """Create the automation row. Returns its id or ``None`` on duplicate."""
        # Wave 3 bug-fix: the automation table has NOT NULL + FK on
        # capability_name. Drafts from the novelty/polling path have
        # capability_name=None (no registry match). We substitute the
        # seeded "claude_native" placeholder capability so the FK holds.
        capability_name = draft.capability_name or "claude_native"
        try:
            automation_id = await self._repo.create(
                user_id=draft.user_id,
                name=name,
                description=None,
                capability_name=capability_name,
                inputs=draft.inputs,
                trigger_type="on_schedule",
                schedule=draft.schedule_cron,
                alert_conditions=draft.alert_conditions or {},
                alert_channels=["discord_dm"],
                max_cost_per_run_usd=None,
                min_interval_seconds=300,
                created_via="discord",
                target_cadence_cron=draft.target_cadence_cron,
                active_cadence_cron=draft.active_cadence_cron,
            )
            logger.info(
                "automation_created_via_discord",
                user_id=draft.user_id,
                name=name,
                capability=draft.capability_name,
                target_cadence=draft.target_cadence_cron,
                active_cadence=draft.active_cadence_cron,
            )
            return automation_id
        except AlreadyExistsError:
            logger.info(
                "automation_creation_already_exists",
                user_id=draft.user_id,
                name=name,
            )
            return None
