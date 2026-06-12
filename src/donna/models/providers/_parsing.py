"""Shared JSON response parsing for LLM providers.

Both Anthropic and Ollama providers return text that may contain
JSON wrapped in markdown fences. This module extracts clean JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from donna.models.types import CompletionMetadata

# Regex to strip markdown code fences from LLM output.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


class ResponseParseError(ValueError):
    """A billed completion was received but its body was not valid JSON.

    Carries the call's :class:`~donna.models.types.CompletionMetadata` so the
    router can still log the (already-billed) spend to ``invocation_log`` with
    an ``interrupted`` marker even though parsing failed — a parse failure must
    not drop real token spend from the budget ledger (model-layer critique #2).
    """

    def __init__(self, message: str, *, metadata: CompletionMetadata) -> None:
        super().__init__(message)
        self.metadata = metadata


def parse_json_response(text: str) -> dict[str, Any]:
    """Extract JSON from LLM text response, stripping markdown fences if present."""
    stripped = text.strip()
    match = _JSON_FENCE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    return cast(dict[str, Any], json.loads(stripped))
