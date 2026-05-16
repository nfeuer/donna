"""Action registry and execution for Donna chat."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable, Awaitable

import structlog
import yaml

from donna.chat.types import ActionContext, ActionDefinition, ActionResult

logger = structlog.get_logger()

ActionHandler = Callable[[dict[str, Any], ActionContext], Awaitable[ActionResult]]


class ActionRegistry:
    """Loads and manages chat action definitions."""

    def __init__(self, actions: dict[str, ActionDefinition]) -> None:
        self._actions = actions
        self._handlers: dict[str, ActionHandler] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> ActionRegistry:
        if not path.exists():
            logger.warning("chat_actions_config_not_found", path=str(path))
            return cls({})
        raw = yaml.safe_load(path.read_text()) or {}
        actions_raw = raw.get("actions", {})
        actions: dict[str, ActionDefinition] = {}
        for name, defn in actions_raw.items():
            actions[name] = ActionDefinition(
                name=name,
                description=defn.get("description", ""),
                domain=defn.get("domain", ""),
                safety=defn.get("safety", "read"),
                handler=defn.get("handler", ""),
                parameters=defn.get("parameters", {}),
            )
        logger.info("action_registry_loaded", count=len(actions))
        return cls(actions)

    def match(
        self,
        domain: str | None = None,
        action_hint: str | None = None,
    ) -> ActionDefinition | None:
        if action_hint and action_hint in self._actions:
            return self._actions[action_hint]
        if domain:
            matches = [a for a in self._actions.values() if a.domain == domain]
            if len(matches) == 1:
                return matches[0]
        return None

    def get(self, name: str) -> ActionDefinition | None:
        return self._actions.get(name)

    def list(self) -> list[ActionDefinition]:
        return list(self._actions.values())

    def list_for_domain(self, domain: str) -> list[ActionDefinition]:
        return [a for a in self._actions.values() if a.domain == domain]

    def _resolve_handler(self, action: ActionDefinition) -> ActionHandler:
        if action.name in self._handlers:
            return self._handlers[action.name]
        module_path, func_name = action.handler.rsplit(".", 1)
        module = importlib.import_module(module_path)
        handler = getattr(module, func_name)
        self._handlers[action.name] = handler
        return handler

    async def execute(
        self,
        action_name: str,
        params: dict[str, Any],
        context: ActionContext,
    ) -> ActionResult:
        action = self._actions.get(action_name)
        if action is None:
            return ActionResult(success=False, error=f"Unknown action: {action_name}")
        try:
            handler = self._resolve_handler(action)
            return await handler(params, context)
        except Exception as exc:
            logger.error(
                "action_execution_failed",
                action=action_name,
                error=str(exc),
            )
            return ActionResult(success=False, error=str(exc))

    def format_pending_action(self, action_name: str, params: dict[str, Any]) -> str:
        return json.dumps({"action": action_name, "params": params})

    @staticmethod
    def parse_pending_action(raw: str) -> tuple[str, dict[str, Any]]:
        data = json.loads(raw)
        return data["action"], data["params"]
