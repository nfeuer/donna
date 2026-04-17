"""Regression test for F-W1-C: executor passes only declared kwargs to ModelRouter.complete."""

from __future__ import annotations

import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.models.router import ModelRouter
from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow


@pytest.mark.asyncio
async def test_executor_calls_router_with_only_declared_kwargs() -> None:
    router = MagicMock(spec=ModelRouter)
    router.complete = AsyncMock(
        return_value=(
            {"result": "ok"},
            MagicMock(invocation_id="inv", cost_usd=0.0, latency_ms=1),
        )
    )

    executor = SkillExecutor(model_router=router)
    now = datetime(2026, 1, 1)
    skill = SkillRow(
        id="s1",
        capability_name="cap",
        current_version_id="v1",
        state="sandbox",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=now,
        updated_at=now,
    )
    version = SkillVersionRow(
        id="v1",
        skill_id="s1",
        version_number=1,
        yaml_backbone=(
            "steps:\n"
            "  - name: parse\n"
            "    kind: llm\n"
        ),
        step_content={"parse": "parse"},
        output_schemas={"parse": {"type": "object"}},
        created_by="test",
        changelog=None,
        created_at=now,
    )

    await executor.execute(skill=skill, version=version, inputs={}, user_id="test")

    assert router.complete.call_count >= 1
    # Extract the kwargs passed to complete() — MagicMock.call_args.kwargs only
    # contains kwargs, not positional args. Executor uses kwargs throughout.
    call_kwargs = router.complete.call_args.kwargs
    allowed = set(inspect.signature(ModelRouter.complete).parameters) - {"self"}
    extras = set(call_kwargs) - allowed
    assert not extras, f"executor passed unsupported kwargs to router: {extras}"
