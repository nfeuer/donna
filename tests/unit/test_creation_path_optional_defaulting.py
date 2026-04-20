"""Unit test: AutomationCreationPath fills optional input_schema keys with null."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.automations.creation_flow import AutomationCreationPath
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


@pytest.mark.asyncio
async def test_optional_fields_defaulted_to_none() -> None:
    repo = AsyncMock()
    repo.create.return_value = "aut-123"

    async def _input_schema_lookup(name: str) -> dict:
        return {
            "type": "object",
            "required": ["senders"],
            "properties": {
                "senders": {"type": "array"},
                "query_extras": {"type": ["string", "null"]},
            },
        }

    path = AutomationCreationPath(
        repository=repo,
        capability_input_schema_lookup=_input_schema_lookup,
    )

    draft = DraftAutomation(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["x@y.com"]},
        schedule_cron="0 9 * * *",
        alert_conditions=None,
        target_cadence_cron="0 9 * * *",
        active_cadence_cron="0 9 * * *",
        schedule_human=None,
    )

    await path.approve(draft, name="test")

    call_kwargs = repo.create.call_args.kwargs
    assert call_kwargs["inputs"] == {"senders": ["x@y.com"], "query_extras": None}


@pytest.mark.asyncio
async def test_no_defaulting_when_lookup_absent() -> None:
    """Backward compat: if no lookup is injected, inputs unchanged."""
    repo = AsyncMock()
    repo.create.return_value = "aut-123"

    path = AutomationCreationPath(repository=repo)

    draft = DraftAutomation(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["x@y.com"]},
        schedule_cron="0 9 * * *",
        alert_conditions=None,
        target_cadence_cron="0 9 * * *",
        active_cadence_cron="0 9 * * *",
        schedule_human=None,
    )

    await path.approve(draft, name="test")

    call_kwargs = repo.create.call_args.kwargs
    assert call_kwargs["inputs"] == {"senders": ["x@y.com"]}
