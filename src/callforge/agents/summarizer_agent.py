from __future__ import annotations

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import SUMMARIZER_SYSTEM
from callforge.domain.value_objects import AgentName


class SummarizerAgent(BaseAgent):
    name = AgentName.SUMMARIZER
    system_prompt = SUMMARIZER_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        transcript = "\n".join(
            f"{h['role']}: {h['content']}" for h in ctx.history[-20:]
        )
        return (
            f"Conversation transcript:\n{transcript}\n\n"
            f"Latest customer message: {ctx.user_message}\n\n"
            "Summarize this conversation."
        )

    def parse(self, data: dict) -> tuple[float, str | None]:
        return 0.9, data.get("final_status")
