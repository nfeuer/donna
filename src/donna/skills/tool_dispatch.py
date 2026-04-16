"""Tool dispatcher — resolves Jinja args and runs tools with retry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog

from donna.skills.tool_registry import (
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolRegistry,
)

logger = structlog.get_logger()


class ToolInvocationError(Exception):
    """Raised when a tool invocation fails (including after retries)."""


@dataclass(slots=True)
class ToolInvocationSpec:
    """A single tool call declared in a skill YAML step."""
    tool: str
    args: dict[str, Any]
    store_as: str = "result"
    retry: dict[str, Any] = field(default_factory=dict)


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._jinja = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def run_invocation(
        self,
        spec: ToolInvocationSpec,
        state: dict,
        inputs: dict,
        allowed_tools: list[str],
    ) -> dict:
        """Run a single tool invocation; return {store_as_key: tool_result}."""
        try:
            resolved_args = self._render_args(spec.args, state=state, inputs=inputs)
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

    def _render_args(self, args: dict, state: dict, inputs: dict) -> dict:
        return {k: self._render_value(v, state=state, inputs=inputs) for k, v in args.items()}

    def _render_value(self, value: Any, state: dict, inputs: dict) -> Any:
        if isinstance(value, str):
            return self._jinja.from_string(value).render(state=state, inputs=inputs)
        if isinstance(value, dict):
            return self._render_args(value, state=state, inputs=inputs)
        if isinstance(value, list):
            return [self._render_value(v, state=state, inputs=inputs) for v in value]
        return value
