"""Shared JSON response parsing for LLM providers.

Both Anthropic and Ollama providers return text that may contain
JSON wrapped in markdown fences. This module extracts clean JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Regex to strip markdown code fences from LLM output.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def parse_json_response(text: str) -> dict[str, Any]:
    """Extract JSON from LLM text response, stripping markdown fences if present."""
    stripped = text.strip()
    match = _JSON_FENCE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    return json.loads(stripped)
