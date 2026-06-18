"""Central registry of skill-layer tools.

Phase 2: tools are registered at app startup via :meth:`ToolRegistry.register`.
Skills declare per-step allowlists in YAML; the executor calls
:meth:`ToolRegistry.dispatch` with the allowlist, which enforces that the tool is
permitted on the step.

R3 (§7.2 resolution — `docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md`)
makes the tool-validation seam load-bearing (CLAUDE.md principle #6 — *models
propose, the orchestrator validates and executes*):

1. **Per-tool parameter validation.** Each tool may register a declarative
   JSON-schema (sourced from ``schemas/tools/<tool>.json`` via
   :mod:`donna.skills.tool_param_schemas`). Before a handler runs, the resolved
   args are validated against that schema. Invalid args **fail closed** — they
   raise :class:`ParameterValidationError` and the handler is never called.
2. **Caller-identity audit.** ``dispatch`` accepts optional ``task_type`` and
   ``agent_name`` and includes them on the structured ``tool_executed`` /
   ``tool_dispatch_*`` logs. These are an audit trail, not a second gate — the
   per-step allowlist remains the access decision.

**No-schema policy (deliberate).** Production tools are all schema'd (see
``register_default_tools`` in :mod:`donna.skills.tools`), so the no-schema branch
never fires in production. When a tool *without* a registered schema is
dispatched (only ad-hoc/test registrations), the registry does **not** silently
skip validation: it logs and emits a fallback alert
(``event_type="fallback_activated"`` via :func:`donna.skills.alerting.emit_fallback_alert`)
and then proceeds. This is the explicitly-permitted "allow a no-schema path"
branch from the R3 brief — audited and observable, never silent. Invalid *args*
against a *present* schema always fail closed (raise).

Tools are async callables accepting keyword arguments and returning a
JSON-serializable dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
import structlog

from donna.skills.alerting import FallbackAlert, emit_fallback_alert

logger = structlog.get_logger()


ToolCallable = Callable[..., Awaitable[dict[str, Any]]]


class ToolNotFoundError(Exception):
    """Raised when a skill asks for a tool that isn't registered."""


class ToolNotAllowedError(Exception):
    """Raised when a skill step tries to dispatch a tool not in its allowlist."""


class ParameterValidationError(Exception):
    """Raised when a tool call's arguments fail their per-tool JSON schema.

    This is a **deterministic** failure — the same args will always fail the
    same schema — so the dispatcher must not retry it as if it were transient.
    The handler is never invoked when this is raised (fail-closed).
    """

    def __init__(self, tool_name: str, errors: list[str]) -> None:
        self.tool_name = tool_name
        self.errors = errors
        super().__init__(
            f"tool {tool_name!r} arguments failed schema validation: "
            + "; ".join(errors)
        )


class ToolRegistry:
    """Registers, validates, and dispatches skill-layer tools.

    Attributes:
        _tools: Map of tool name -> async handler.
        _schemas: Map of tool name -> JSON schema for its caller-supplied args.
        _fallback_alert: Optional notifier used when the deliberate no-schema
            path is hit (see module docstring); ``None`` still logs the event.
    """

    def __init__(self, fallback_alert: FallbackAlert | None = None) -> None:
        self._tools: dict[str, ToolCallable] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._fallback_alert = fallback_alert

    def register(
        self,
        name: str,
        callable_: ToolCallable,
        *,
        param_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool handler and (optionally) its parameter schema.

        Args:
            name: Tool name; must match the allowlist entries in skill YAML.
            callable_: Async handler accepting keyword args, returning a dict.
            param_schema: JSON schema (draft-07) validating the caller-supplied
                args. Injected dependencies bound via ``functools.partial``
                (e.g. ``client``/``store``) are not caller-supplied and must be
                omitted from the schema. Production registrations always pass a
                schema; ``None`` selects the deliberate, audited no-schema path
                described in the module docstring.

        Returns:
            None.
        """
        if name in self._tools:
            logger.info("tool_overwritten", name=name)
        self._tools[name] = callable_
        if param_schema is not None:
            self._schemas[name] = param_schema
        else:
            # An overwrite that drops the schema must not leave a stale one.
            self._schemas.pop(name, None)

    def clear(self) -> None:
        """Remove every registered tool and schema.

        Intended for test teardown (via the autouse fixture in
        tests/conftest.py) and boot-time reinitialization. Not thread-safe;
        do not call from request-serving code paths.

        Returns:
            None.
        """
        self._tools.clear()
        self._schemas.clear()

    def list_tool_names(self) -> list[str]:
        """Return the names of every registered tool.

        Returns:
            A list of registered tool names.
        """
        return list(self._tools.keys())

    def has_schema(self, name: str) -> bool:
        """Return whether a parameter schema is registered for ``name``.

        Args:
            name: Tool name to check.

        Returns:
            ``True`` if a parameter schema is registered, else ``False``.
        """
        return name in self._schemas

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        allowed_tools: list[str],
        *,
        task_type: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate and execute a tool call.

        Order of checks (all before the handler runs):

        1. **Allowlist** (the access gate): ``tool_name`` must be in
           ``allowed_tools`` or :class:`ToolNotAllowedError` is raised.
        2. **Registration**: the tool must be registered or
           :class:`ToolNotFoundError` is raised.
        3. **Parameter schema** (fail-closed): if a schema is registered, ``args``
           are validated against it; on failure :class:`ParameterValidationError`
           is raised and the handler is **not** called. If no schema is
           registered, the deliberate no-schema path (log + fallback alert, then
           proceed) is taken — see the module docstring.

        Args:
            tool_name: Tool to dispatch.
            args: Caller-supplied keyword arguments for the handler.
            allowed_tools: The per-step allowlist; the access decision.
            task_type: Caller identity for the audit log (skill task type).
            agent_name: Caller identity for the audit log (capability/skill name).

        Returns:
            The handler's result dict.

        Raises:
            ToolNotAllowedError: If the tool is not in ``allowed_tools``.
            ToolNotFoundError: If the tool is not registered.
            ParameterValidationError: If ``args`` fail a registered schema.
        """
        if tool_name not in allowed_tools:
            raise ToolNotAllowedError(
                f"tool {tool_name!r} not in step allowlist {allowed_tools}"
            )
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"tool {tool_name!r} not registered")

        await self._validate_params(
            tool_name=tool_name, args=args,
            task_type=task_type, agent_name=agent_name,
        )

        tool = self._tools[tool_name]
        result = await tool(**args)
        logger.info(
            "tool_executed",
            tool=tool_name,
            task_type=task_type,
            agent_name=agent_name,
        )
        return result

    async def _validate_params(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        task_type: str | None,
        agent_name: str | None,
    ) -> None:
        """Validate ``args`` against the tool's registered schema.

        Args:
            tool_name: Tool being dispatched.
            args: Caller-supplied keyword arguments.
            task_type: Caller identity for audit logging.
            agent_name: Caller identity for audit logging.

        Returns:
            None.

        Raises:
            ParameterValidationError: If a schema is registered and ``args`` do
                not conform to it.
        """
        schema = self._schemas.get(tool_name)
        if schema is None:
            # Deliberate, audited no-schema path. Never silent: log + alert,
            # then proceed (the no-schema branch the R3 brief permits). In
            # production every registered tool has a schema, so this never fires.
            await emit_fallback_alert(
                self._fallback_alert,
                component="skills_tool_registry",
                error=f"tool {tool_name!r} dispatched with no registered param schema",
                fallback="parameter validation skipped for this call",
                context={
                    "tool": tool_name,
                    "task_type": task_type,
                    "agent_name": agent_name,
                },
            )
            return

        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(
            validator.iter_errors(args), key=lambda e: list(e.absolute_path)
        )
        if errors:
            messages = [
                f"{'.'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                for e in errors
            ]
            logger.warning(
                "tool_param_validation_failed",
                tool=tool_name,
                task_type=task_type,
                agent_name=agent_name,
                error_count=len(messages),
                errors=messages,
            )
            raise ParameterValidationError(tool_name, messages)
