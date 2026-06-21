from __future__ import annotations

import json

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.prompts import ESCALATION_SYSTEM
from callforge.domain.value_objects import AgentName


class EscalationAgent(BaseAgent):
    name = AgentName.ESCALATION
    system_prompt = ESCALATION_SYSTEM

    def build_user_prompt(self, ctx: AgentContext) -> str:
        routing = json.dumps(ctx.routing or {}, ensure_ascii=False)
        return (
            f"Routing classification: {routing}\n\n"
            f"Last customer message: {ctx.user_message}\n\n"
            "Produce the escalation handoff package."
        )

    def parse(self, data: dict) -> tuple[float, str | None]:
        return 0.9, data.get("priority")
