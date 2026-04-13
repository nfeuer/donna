"""Prompt token estimation for router-level budget checks.

Uses a character-based heuristic (len // 4) that matches the ballpark for
English prompts. We compare against the actual `tokens_in` returned by
Ollama and surface drift on the LLM Gateway dashboard; if drift grows past
the red threshold, upgrade to Ollama's /api/tokenize endpoint.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Rough token-count estimate for a prompt.

    Uses the len(text) // 4 heuristic — zero dependencies, constant-time,
    and close enough for budget checks on modest-sized prompts. Accuracy
    is tracked on the LLM Gateway dashboard via the estimated_tokens_in
    column on invocation_log.
    """
    return len(text) // 4
