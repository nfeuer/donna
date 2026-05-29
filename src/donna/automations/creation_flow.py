"""AutomationCreationPath — final step of the Discord NL creation flow.

Invoked when the user clicks Approve on an AutomationConfirmationView.
Writes the automation row. Idempotent on (user_id, name) uniqueness — a
second approve returns ``None`` instead of creating a duplicate.

Wave 4: capability-availability guard. Before writing, verify all tools
the capability's skill depends on are registered. If not, raise
MissingToolError so the caller can DM an actionable error.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from donna.automations.cron import CronScheduleCalculator
from donna.automations.repository import AlreadyExistsError
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

logger = structlog.get_logger()


class MissingToolError(Exception):
    """Raised when a capability needs a tool that isn't currently registered."""

    def __init__(self, capability: str, missing: list[str]) -> None:
        super().__init__(
            f"capability {capability!r} requires unregistered tool(s): {missing}"
        )
        self.capability = capability
        self.missing = missing


CapabilityToolLookup = Callable[[str], Awaitable[list[str]]]
CapabilityInputSchemaLookup = Callable[[str], Awaitable[dict[str, Any]]]
CapabilityDefaultAlertsLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
SkillCandidateWriter = Callable[..., Awaitable[str]]


class AutomationCreationPath:
    def __init__(
        self,
        *,
        repository: Any,
        default_min_interval_seconds: int = 300,
        tool_registry: Any | None = None,
        capability_tool_lookup: CapabilityToolLookup | None = None,
        capability_input_schema_lookup: CapabilityInputSchemaLookup | None = None,
        capability_default_alerts_lookup: CapabilityDefaultAlertsLookup | None = None,
        skill_candidate_writer: SkillCandidateWriter | None = None,
    ) -> None:
        self._repo = repository
        self._default_min_interval_seconds = default_min_interval_seconds
        self._tool_registry = tool_registry
        self._capability_tool_lookup = capability_tool_lookup
        self._capability_input_schema_lookup = capability_input_schema_lookup
        self._capability_default_alerts_lookup = capability_default_alerts_lookup
        self._skill_candidate_writer = skill_candidate_writer

    async def approve(self, draft: DraftAutomation, *, name: str) -> str | None:
        """Create the automation row. Returns its id or ``None`` on duplicate."""
        # Wave 3 bug-fix: the automation table has NOT NULL + FK on
        # capability_name. Drafts from the novelty/polling path have
        # capability_name=None (no registry match). We substitute the
        # seeded "claude_native" placeholder capability so the FK holds.
        capability_name = draft.capability_name or "claude_native"

        # Capability-availability guard: only when wired (preserves
        # backward-compat for tests that construct without registry).
        if (
            self._tool_registry is not None
            and self._capability_tool_lookup is not None
            and draft.capability_name  # placeholder has no tool requirements
        ):
            required = await self._capability_tool_lookup(draft.capability_name)
            available = set(self._tool_registry.list_tool_names())
            missing = [t for t in required if t not in available]
            if missing:
                logger.warning(
                    "automation_creation_missing_tools",
                    capability=draft.capability_name,
                    missing=missing,
                )
                raise MissingToolError(draft.capability_name, missing)

        # F-W4-K: default optional input_schema keys to None so skill.yaml
        # templates under StrictUndefined don't need `is defined and` guards.
        inputs = dict(draft.inputs or {})
        if (
            self._capability_input_schema_lookup is not None
            and draft.capability_name
        ):
            try:
                schema = await self._capability_input_schema_lookup(draft.capability_name)
                required_keys = set(schema.get("required", []) or [])
                props = (schema.get("properties") or {}).keys()
                for key in props:
                    if key not in required_keys and key not in inputs:
                        inputs[key] = None
            except Exception:
                logger.exception("capability_input_schema_lookup_failed")

        alert_conditions = draft.alert_conditions
        if (
            not alert_conditions
            and self._capability_default_alerts_lookup is not None
            and draft.capability_name
        ):
            try:
                defaults = await self._capability_default_alerts_lookup(
                    draft.capability_name
                )
                if defaults:
                    alert_conditions = defaults
            except Exception:
                logger.exception("capability_default_alerts_lookup_failed")
        if not alert_conditions:
            alert_conditions = {}

        alert_channels = getattr(draft, "notification_channels", None) or [
            "discord_dm"
        ]

        next_run_at: datetime | None = None
        schedule_expr = draft.active_cadence_cron or draft.schedule_cron
        if schedule_expr:
            try:
                next_run_at = CronScheduleCalculator().next_run(
                    expression=schedule_expr, after=datetime.now(UTC),
                )
            except Exception:
                logger.exception("automation_creation_next_run_calc_failed")

        try:
            automation_id = await self._repo.create(
                user_id=draft.user_id,
                name=name,
                description=None,
                capability_name=capability_name,
                inputs=inputs,
                trigger_type="on_schedule",
                schedule=draft.schedule_cron,
                alert_conditions=alert_conditions,
                alert_channels=alert_channels,
                max_cost_per_run_usd=None,
                min_interval_seconds=self._default_min_interval_seconds,
                created_via="discord",
                target_cadence_cron=draft.target_cadence_cron,
                active_cadence_cron=draft.active_cadence_cron,
                next_run_at=next_run_at,
            )
            logger.info(
                "automation_created_via_discord",
                user_id=draft.user_id,
                name=name,
                capability=draft.capability_name,
                target_cadence=draft.target_cadence_cron,
                active_cadence=draft.active_cadence_cron,
            )
            if draft.skill_candidate and self._skill_candidate_writer is not None:
                try:
                    candidate_id = await self._skill_candidate_writer(
                        capability_name,
                        None,
                        0.0,
                        1,
                        None,
                        draft.skill_candidate_reasoning,
                    )
                    logger.info(
                        "skill_candidate_queued_from_automation",
                        automation_id=automation_id,
                        candidate_id=candidate_id,
                        reasoning=draft.skill_candidate_reasoning,
                    )
                except Exception:
                    logger.exception(
                        "skill_candidate_queue_failed",
                        automation_id=automation_id,
                    )
            return str(automation_id) if automation_id is not None else None
        except AlreadyExistsError:
            logger.info(
                "automation_creation_already_exists",
                user_id=draft.user_id,
                name=name,
            )
            return None
