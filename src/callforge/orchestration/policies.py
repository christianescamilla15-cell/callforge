"""Explicit, testable workflow policies. No magic inside agents."""
from __future__ import annotations

from dataclasses import dataclass

from callforge.config import Settings


@dataclass
class EscalationPolicy:
    quality_threshold: float
    confidence_threshold: float
    max_quality_retries: int

    @classmethod
    def from_settings(cls, settings: Settings) -> EscalationPolicy:
        return cls(
            quality_threshold=settings.quality_threshold,
            confidence_threshold=settings.confidence_threshold,
            max_quality_retries=settings.max_quality_retries,
        )

    def should_escalate(
        self,
        router_next_agent: str,
        agent_suggested: bool,
        agent_confidence: float,
        quality_score: float | None,
        retries_exhausted: bool,
    ) -> bool:
        if router_next_agent == "escalation":
            return True
        if agent_suggested:
            return True
        if agent_confidence < self.confidence_threshold:
            return True
        if (
            quality_score is not None
            and quality_score < self.quality_threshold
            and retries_exhausted
        ):
            return True
        return False

    def should_retry(self, quality_score: float | None, retries_done: int) -> bool:
        return (
            quality_score is not None
            and quality_score < self.quality_threshold
            and retries_done < self.max_quality_retries
        )
