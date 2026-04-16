"""ValidationExecutor — offline fixture validation, mocked tools, real local LLM.

See spec §6.1. Implements the ``executor.execute`` protocol consumed by
:func:`donna.skills.fixtures.validate_against_fixtures`. Per call it
constructs a fresh :class:`MockToolRegistry` keyed from the fixture's
``tool_mocks`` blob and a :class:`ValidationRunSink` that absorbs
persistence calls.

Never writes to production tables. Used by AutoDrafter fixture validation
(§6.5) and Evolver gates 2/3/4 (§6.6).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from donna.config import SkillSystemConfig
from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.mock_tool_registry import MockToolRegistry
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink

logger = structlog.get_logger()


class ValidationExecutor:
    """SkillExecutor-compatible class for offline fixture validation."""

    def __init__(
        self,
        model_router: Any,
        config: SkillSystemConfig,
    ) -> None:
        self._router = model_router
        self._config = config

    async def execute(
        self,
        *,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
        tool_mocks: dict | None = None,
        **_ignored_kwargs: Any,
    ) -> SkillRunResult:
        inner = self._build_inner_executor(tool_mocks)
        try:
            return await asyncio.wait_for(
                inner.execute(skill=skill, version=version,
                              inputs=inputs, user_id=user_id),
                timeout=self._config.validation_per_run_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "validation_run_timeout",
                skill_id=skill.id, version_id=version.id,
                timeout_s=self._config.validation_per_run_timeout_s,
            )
            raise

    def _build_inner_executor(self, tool_mocks: dict | None) -> SkillExecutor:
        tool_registry = MockToolRegistry.from_mocks(tool_mocks)
        sink = ValidationRunSink()
        return SkillExecutor(
            model_router=self._router,
            tool_registry=tool_registry,
            run_sink=sink,
        )
