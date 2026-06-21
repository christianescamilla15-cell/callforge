from __future__ import annotations

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import ROUTER_SYSTEM
from callforge.domain.value_objects import AgentName


class RouterAgent(BaseAgent):
    name = AgentName.ROUTER
    system_prompt = ROUTER_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return f"Customer message: {ctx.user_message}"

    def parse(self, data: dict) -> tuple[float, str | None]:
        confidence = float(data.get("confidence", 0.5))
        return confidence, data.get("next_agent")
