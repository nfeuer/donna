"""Universal Reply Handler — confidence-gated pipeline for user replies.

Layer 1 (FastPath): Config-driven keyword matching with a complexity
gate that prevents misclassification of multi-intent replies.

Layer 2 (LLM): Local LLM classifies complex replies, proposes actions,
and drafts a response in Donna's persona.

Plan-and-confirm: LLM-proposed actions are persisted and require
user confirmation before execution.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import structlog

from donna.config import ReplyIntentsConfig

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class FastPathResult:
    """Result from fast-path keyword matching."""

    intent: str
    action: str
    confirm: bool


class FastPath:
    """Layer 1: keyword matching with complexity gate.

    Args:
        config: Parsed ReplyIntentsConfig.
    """

    def __init__(self, config: ReplyIntentsConfig) -> None:
        self._config = config
        self._fp = config.fast_path

    def is_simple(self, reply: str) -> bool:
        """Check whether a reply passes the complexity gate."""
        if len(reply) > self._fp.max_length:
            return False

        lower = reply.lower()
        for signal in self._fp.multi_intent_signals:
            if signal in lower:
                return False

        if "," in reply and len(reply.split(",")) > 2:
            return False

        matched_intents: list[str] = []
        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                matched_intents.append(name)

        if len(matched_intents) != 1:
            return False

        return True

    def match(self, reply: str) -> FastPathResult | None:
        """Try to match a reply to a single intent. Returns None if no match or complex."""
        lower = reply.lower()
        if not self.is_simple(reply):
            return None

        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                return FastPathResult(
                    intent=name,
                    action=intent.action,
                    confirm=intent.confirm,
                )
        return None

    def is_plan_confirm(self, reply: str) -> bool:
        """Check if reply is confirming a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.confirm_keywords)

    def is_plan_reject(self, reply: str) -> bool:
        """Check if reply is rejecting a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.reject_keywords)
