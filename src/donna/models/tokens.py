"""Prompt token estimation for router-level budget checks.

`estimate_tokens` is the zero-dependency `len // 4` baseline. It is still used
for the escalation gate's deterministic cost *floor*. The Ollama context-budget
gate no longer trusts a constant divisor: `ModelRouter` maintains a
self-calibrating per-task-type EMA of the observed `len(prompt) / tokens_in`
ratio (clamped + safety-scaled) so a dense prompt is not silently truncated by
an under-estimate (design A — see `ModelRouter._estimate_tokens_calibrated` and
the `ollama.token_estimation` config block). The router also runs a post-call
`truncation_suspected` tripwire that feeds the upgrade gauge.

Upgrade trigger (OOS-11): swap the heuristic for Ollama's /api/tokenize endpoint
when the combined `context_overflow_escalation` + `truncation_suspected` rate
exceeds 10% (the tripwire is what makes that trigger able to fire on otherwise
silent truncation).
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
