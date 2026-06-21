from __future__ import annotations

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import TROUBLESHOOTING_SYSTEM
from callforge.domain.value_objects import AgentName


class TroubleshootingAgent(BaseAgent):
    name = AgentName.TROUBLESHOOTING
    system_prompt = TROUBLESHOOTING_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        parts = [self.format_knowledge(ctx)]
        if ctx.resolution_steps:
            lines = []
            for step in ctx.resolution_steps:
                outcome = (
                    f"customer reported: {step.customer_response}"
                    if step.customer_response
                    else f"status: {step.status.value}"
                )
                lines.append(f"{step.step_number}. {step.instruction} -> {outcome}")
            parts.append("PREVIOUS DIAGNOSTIC STEPS:\n" + "\n".join(lines))
        if ctx.quality_feedback:
            parts.append(
                f"PREVIOUS ATTEMPT WAS REJECTED BY QUALITY REVIEW: {ctx.quality_feedback}\n"
                "Produce an improved answer."
            )
        parts.append(f"Customer message: {ctx.user_message}")
        return "\n\n".join(parts)
