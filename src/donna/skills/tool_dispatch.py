"""Tool dispatcher — resolves Jinja args and runs tools with retry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog

from donna.skills._render import render_value
from donna.skills.tool_registry import (
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolRegistry,
)

logger = structlog.get_logger()


class ToolInvocationError(Exception):
    """Raised when a tool invocation fails (including after retries)."""


# Valid values for a tool invocation's ``on_failure`` DSL field (F-W2-D).
# - ``escalate`` (default): raise ToolInvocationError; executor consults triage
#   or escalates to Claude.
# - ``continue``: swallow the error, log it, and return ``{store_as: {"tool_error": ...}}``
#   so downstream steps can observe the failure.
# - ``fail_step``: raise StepFailedError; executor marks the step terminally
#   failed, skips remaining steps with no escalation.
# - ``fail_skill``: raise SkillFailedError; executor aborts the whole run
#   with status=failed, no escalation.
ON_FAILURE_VALUES = frozenset(
    ("escalate", "continue", "fail_step", "fail_skill")
)


@dataclass(slots=True)
class ToolInvocationSpec:
    """A single tool call declared in a skill YAML step."""
    tool: str
    args: dict[str, Any]
    store_as: str = "result"
    retry: dict[str, Any] = field(default_factory=dict)
    on_failure: str = "escalate"

    def __post_init__(self) -> None:
        if self.on_failure not in ON_FAILURE_VALUES:
            raise ValueError(
                f"invalid on_failure={self.on_failure!r}; "
                f"expected one of {sorted(ON_FAILURE_VALUES)}"
            )


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def run_invocation(
        self,
        spec: ToolInvocationSpec,
        state: dict,
        inputs: dict,
        allowed_tools: list[str],
    ) -> dict:
        """Run a single tool invocation; return {store_as_key: tool_result}.

        If the invocation fails (after retries) the ``spec.on_failure`` DSL
        value determines the behavior:

        - ``escalate`` (default): raise ``ToolInvocationError``.
        - ``continue``: log and return ``{spec.store_as: {"tool_error": ...}}``.
        - ``fail_step``: raise ``StepFailedError``.
        - ``fail_skill``: raise ``SkillFailedError``.
        """
        try:
            return await self._dispatch_with_retry(
                spec=spec, state=state, inputs=inputs,
                allowed_tools=allowed_tools,
            )
        except ToolInvocationError as exc:
            return self._apply_on_failure(spec, exc)

    async def _dispatch_with_retry(
        self,
        spec: ToolInvocationSpec,
        state: dict,
        inputs: dict,
        allowed_tools: list[str],
    ) -> dict:
        try:
            resolved_args = render_value(
                spec.args,
                context={"state": state, "inputs": inputs},
                preserve_types=False,
            )
        except jinja2.UndefinedError as exc:
            raise ToolInvocationError(f"arg render failed: {exc}") from exc

        max_attempts = int(spec.retry.get("max_attempts", 1))
        backoff_s = spec.retry.get("backoff_s", [0])

        last_err: Exception | None = None

        for attempt in range(max_attempts):
            try:
                result = await self._registry.dispatch(
                    tool_name=spec.tool,
                    args=resolved_args,
                    allowed_tools=allowed_tools,
                )
                if attempt > 0:
                    logger.info("tool_retry_succeeded", tool=spec.tool, attempt=attempt + 1)
                return {spec.store_as: result}
            except (ToolNotAllowedError, ToolNotFoundError) as exc:
                # Permission errors don't benefit from retry.
                raise ToolInvocationError(str(exc)) from exc
            except Exception as exc:
                last_err = exc
                if attempt + 1 < max_attempts:
                    wait = backoff_s[attempt] if attempt < len(backoff_s) else backoff_s[-1]
                    logger.info(
                        "tool_retry_scheduled",
                        tool=spec.tool, attempt=attempt + 1,
                        wait_s=wait, error=str(exc),
                    )
                    if wait > 0:
                        await asyncio.sleep(wait)

        logger.warning(
            "tool_invocation_failed",
            tool=spec.tool, attempts=max_attempts,
            error=str(last_err) if last_err else "unknown",
        )
        raise ToolInvocationError(str(last_err)) from last_err

    def _apply_on_failure(
        self, spec: ToolInvocationSpec, exc: ToolInvocationError,
    ) -> dict:
        """Translate a ToolInvocationError into the configured on_failure action."""
        on_failure = spec.on_failure
        if on_failure == "continue":
            logger.warning(
                "tool_failure_continue",
                tool=spec.tool, store_as=spec.store_as, error=str(exc),
            )
            return {spec.store_as: {"tool_error": str(exc)}}
        if on_failure == "fail_step":
            # Import lazily to avoid an executor <-> dispatcher import cycle.
            from donna.skills.executor import StepFailedError
            raise StepFailedError(step_name=spec.store_as, cause=exc) from exc
        if on_failure == "fail_skill":
            from donna.skills.executor import SkillFailedError
            raise SkillFailedError(step_name=spec.store_as, cause=exc) from exc
        # Default: escalate — re-raise so the executor's triage/escalation
        # path handles it as before.
        raise exc
