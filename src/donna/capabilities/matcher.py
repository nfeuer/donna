"""CapabilityMatcher — confidence-scoring wrapper over CapabilityRegistry.

Thresholds (HIGH >= 0.75, MEDIUM >= 0.40, LOW < 0.40) are starter values.
See spec §6.7.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import structlog

from donna.capabilities.models import CapabilityRow
from donna.capabilities.registry import CapabilityRegistry

logger = structlog.get_logger()

HIGH_CONFIDENCE_THRESHOLD = 0.75
MEDIUM_CONFIDENCE_THRESHOLD = 0.40


class MatchConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True)
class MatchResult:
    confidence: MatchConfidence
    best_match: CapabilityRow | None
    best_score: float
    candidates: list[tuple[CapabilityRow, float]] = field(default_factory=list)


class CapabilityMatcher:
    def __init__(self, registry: CapabilityRegistry, k: int = 5) -> None:
        self._registry = registry
        self._k = k

    async def match(self, query: str) -> MatchResult:
        candidates = await self._registry.semantic_search(query, k=self._k)

        if not candidates:
            return MatchResult(
                confidence=MatchConfidence.LOW,
                best_match=None,
                best_score=0.0,
                candidates=[],
            )

        best_cap, best_score = candidates[0]
        confidence = self._classify_confidence(best_score)

        logger.info(
            "capability_match",
            query_preview=query[:80],
            best_match=best_cap.name,
            best_score=best_score,
            confidence=confidence.value,
            candidate_count=len(candidates),
        )

        return MatchResult(
            confidence=confidence,
            best_match=best_cap if confidence != MatchConfidence.LOW else None,
            best_score=best_score,
            candidates=candidates,
        )

    @staticmethod
    def _classify_confidence(score: float) -> MatchConfidence:
        if score >= HIGH_CONFIDENCE_THRESHOLD:
            return MatchConfidence.HIGH
        if score >= MEDIUM_CONFIDENCE_THRESHOLD:
            return MatchConfidence.MEDIUM
        return MatchConfidence.LOW
