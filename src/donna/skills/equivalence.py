"""Claude-backed semantic equivalence judge for skill shadow sampling."""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger()


_PROMPT_TEMPLATE = """You are judging whether two skill outputs convey the same information.

Context (what this task represents):
{context}

Output A:
{output_a}

Output B:
{output_b}

Respond with strict JSON matching this shape:
{{"agreement": <float 0.0-1.0>, "rationale": <short string>}}

Where 1.0 means "the two outputs convey the same information completely" and 0.0 means \
"they disagree materially." 0.5 means "partial overlap." Be strict about factual \
equivalence but forgiving about wording and formatting."""


class EquivalenceJudge:
    def __init__(
        self,
        model_router: Any,
        task_type: str = "skill_equivalence_judge",
    ) -> None:
        self._router = model_router
        self._task_type = task_type

    async def judge(
        self,
        output_a: dict,
        output_b: dict,
        context: dict | None = None,
    ) -> float:
        """Compare two skill outputs for semantic equivalence.

        Args:
            output_a: First skill output to compare.
            output_b: Second skill output to compare.
            context: Optional task input / capability description so the judge
                understands what the outputs are supposed to represent.

        Returns:
            Float in [0.0, 1.0] representing semantic agreement.
            Returns 0.0 on any error.
        """
        prompt = _PROMPT_TEMPLATE.format(
            context=json.dumps(context or {}, sort_keys=True, indent=2),
            output_a=json.dumps(output_a, sort_keys=True, indent=2),
            output_b=json.dumps(output_b, sort_keys=True, indent=2),
        )

        try:
            parsed, _metadata = await self._router.complete(
                prompt=prompt,
                task_type=self._task_type,
                task_id=None,
                user_id="system",
            )
        except Exception as exc:
            logger.warning(
                "equivalence_judge_call_failed",
                error=str(exc),
                task_type=self._task_type,
            )
            return 0.0

        agreement_raw = parsed.get("agreement") if isinstance(parsed, dict) else None
        if not isinstance(agreement_raw, (int, float)):
            logger.warning(
                "equivalence_judge_malformed_output",
                parsed_type=type(parsed).__name__,
                parsed_preview=str(parsed)[:200],
            )
            return 0.0

        # Clamp to [0, 1].
        agreement = float(agreement_raw)
        if agreement < 0.0 or agreement > 1.0:
            logger.warning(
                "equivalence_judge_out_of_range",
                raw_value=agreement,
            )
            agreement = max(0.0, min(1.0, agreement))

        return agreement
