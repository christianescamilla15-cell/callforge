from __future__ import annotations

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import SUPPORT_SYSTEM
from callforge.domain.value_objects import AgentName


class SupportAgent(BaseAgent):
    name = AgentName.SUPPORT
    system_prompt = SUPPORT_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        parts = [self.format_knowledge(ctx)]
        if ctx.quality_feedback:
            parts.append(
                f"PREVIOUS ATTEMPT WAS REJECTED BY QUALITY REVIEW: {ctx.quality_feedback}\n"
                "Produce an improved answer."
            )
        parts.append(f"Customer message: {ctx.user_message}")
        return "\n\n".join(parts)
