"""Shared types for the model abstraction layer.

See docs/model-layer.md for the complete interface specification.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class CompletionMetadata:
    """Metadata returned alongside every LLM completion.

    Captures latency, token usage, cost, and the actual model used.
    Logged to invocation_log on every call.
    """

    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model_actual: str
    is_shadow: bool = False
