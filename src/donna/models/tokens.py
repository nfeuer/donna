"""Prompt token estimation for router-level budget checks.

Uses a character-based heuristic (len // 4) that matches the ballpark for
English prompts. We compare against the actual `tokens_in` returned by
Ollama and surface drift on the LLM Gateway dashboard.

Upgrade trigger (OOS-11): swap this heuristic for Ollama's /api/tokenize
endpoint when the `context_overflow_escalation` rate exceeds 10%. The
event is emitted from `src/donna/models/router.py:215` whenever a
request falls back off a local alias due to estimated_tokens > budget.
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
