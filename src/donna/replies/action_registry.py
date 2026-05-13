"""Action registry for the Universal Reply Handler.

Loads action definitions from config, validates LLM-proposed actions,
and renders action descriptions for the LLM prompt.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.config import ReplyActionsConfig

logger = structlog.get_logger()


class ActionRegistry:
    """Validates and describes available reply actions.

    Args:
        config: Parsed ReplyActionsConfig from reply_actions.yaml.
    """

    def __init__(self, config: ReplyActionsConfig) -> None:
        self._config = config

    def validate_action(self, action: dict[str, Any]) -> list[str]:
        """Validate a single proposed action. Returns list of error strings (empty = valid)."""
        errors: list[str] = []
        name = action.get("action", "")
        if name not in self._config.actions:
            errors.append(f"Unknown action: {name!r}")
            return errors

        defn = self._config.actions[name]
        provided = action.get("params", {})

        for param_name, param_def in defn.params.items():
            if param_def.from_context:
                continue
            if param_def.default is not None or param_def.optional:
                continue
            if param_name not in provided:
                errors.append(f"Missing required param {param_name!r} for action {name!r}")

        return errors

    def validate_actions(
        self, actions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate a list of proposed actions. Returns (valid_actions, all_errors)."""
        valid: list[dict[str, Any]] = []
        all_errors: list[str] = []
        for action in actions:
            errors = self.validate_action(action)
            if errors:
                all_errors.extend(errors)
                logger.warning(
                    "action_validation_failed",
                    action=action.get("action"),
                    errors=errors,
                )
            else:
                valid.append(action)
        return valid, all_errors

    def inject_context(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Fill from_context params that weren't explicitly provided by the LLM."""
        name = action.get("action", "")
        if name not in self._config.actions:
            return action

        defn = self._config.actions[name]
        params = dict(action.get("params", {}))

        for param_name, param_def in defn.params.items():
            if param_def.from_context and param_name not in params and param_name in context:
                params[param_name] = context[param_name]

        return {**action, "params": params}

    def render_for_llm(self) -> str:
        """Render action descriptions for the LLM system prompt."""
        lines: list[str] = ["Available actions:"]
        for name, defn in self._config.actions.items():
            lines.append(f"\n- {name}: {defn.description}")
            if defn.params:
                lines.append("  Parameters:")
                for pname, pdef in defn.params.items():
                    if pdef.from_context:
                        continue
                    req = "required" if (pdef.default is None and not pdef.optional) else "optional"
                    desc = pdef.description or pdef.type
                    enum_str = f" (one of: {', '.join(pdef.enum)})" if pdef.enum else ""
                    lines.append(f"    - {pname} ({pdef.type}, {req}){enum_str}: {desc}")
        return "\n".join(lines)

    def get_action_def(self, name: str) -> Any | None:
        """Return the ActionDef for a name, or None."""
        return self._config.actions.get(name)
