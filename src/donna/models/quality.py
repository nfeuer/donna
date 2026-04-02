"""Spot-check quality monitoring for local LLM outputs.

Samples a configurable percentage of local model invocations and queues
them for Claude-as-judge evaluation. Low-scoring outputs are flagged
for user review.

See docs/model-layer.md — Spot-Check Quality Monitoring.
"""

from __future__ import annotations

import random
from typing import Any

import structlog

from donna.config import QualityMonitoringConfig

logger = structlog.get_logger()


class SpotCheckSampler:
    """Decides whether a given invocation should be spot-checked.

    Uses probabilistic sampling at the configured rate.
    """

    def __init__(self, config: QualityMonitoringConfig) -> None:
        self._rate = config.spot_check_rate
        self._enabled = config.enabled

    def should_sample(self) -> bool:
        """Return True if this invocation should be queued for spot-check."""
        if not self._enabled:
            return False
        return random.random() < self._rate


class ClaudeJudge:
    """Batch evaluator that uses Claude to score local model outputs.

    Queries invocation_log for queued spot-check entries, sends each to
    Claude with a judge prompt, and writes quality_score back.
    Low-scoring outputs create a Donna task for user review.
    """

    def __init__(
        self,
        config: QualityMonitoringConfig,
        judge_complete: Any,  # Callable to invoke the judge model
        flag_threshold: float | None = None,
    ) -> None:
        self._config = config
        self._judge_complete = judge_complete
        self._flag_threshold = flag_threshold or config.flag_threshold

    async def evaluate_single(
        self, prompt: str, model_output: dict[str, Any], judge_prompt_template: str
    ) -> float:
        """Score a single model output using Claude-as-judge.

        Args:
            prompt: The original prompt sent to the local model.
            model_output: The parsed JSON output from the local model.
            judge_prompt_template: Template with {{ original_prompt }} and
                {{ model_output }} placeholders.

        Returns:
            Quality score between 0.0 and 1.0.
        """
        import json

        rendered = judge_prompt_template.replace(
            "{{ original_prompt }}", prompt
        ).replace("{{ model_output }}", json.dumps(model_output, indent=2))

        result, _ = await self._judge_complete(rendered)
        score = float(result.get("quality_score", 0.0))
        score = max(0.0, min(1.0, score))

        if score < self._flag_threshold:
            logger.warning(
                "quality_below_threshold",
                score=score,
                threshold=self._flag_threshold,
            )

        return score
