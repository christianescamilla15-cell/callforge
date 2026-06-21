from __future__ import annotations

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import QUALITY_SYSTEM
from callforge.domain.value_objects import AgentName


class QualityAgent(BaseAgent):
    name = AgentName.QUALITY
    system_prompt = QUALITY_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return (
            f"{self.format_knowledge(ctx)}\n\n"
            f"Customer message: {ctx.user_message}\n\n"
            f"Candidate reply to evaluate:\n{ctx.candidate_reply or ''}"
        )

    def parse(self, data: dict) -> tuple[float, str | None]:
        score = float(data.get("quality_score", 0.5))
        return score, data.get("hallucination_risk")
