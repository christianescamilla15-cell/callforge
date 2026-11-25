"""Builds the support workflow behind the application port, according to WORKFLOW_ENGINE."""
from __future__ import annotations

from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.application.ports import SupportWorkflowPort
from callforge.config import Settings
from callforge.infrastructure.llm.base import LLMProvider
from callforge.orchestration.policies import EscalationPolicy
from callforge.orchestration.workflow import SupportWorkflow


def build_support_workflow(settings: Settings, llm: LLMProvider) -> SupportWorkflowPort:
    parts = dict(
        router=RouterAgent(llm),
        support=SupportAgent(llm),
        troubleshooting=TroubleshootingAgent(llm),
        escalation=EscalationAgent(llm),
        summarizer=SummarizerAgent(llm),
        quality=QualityAgent(llm),
        policy=EscalationPolicy.from_settings(settings),
        knowledge_top_k=settings.knowledge_top_k,
    )
    if settings.workflow_engine == "langgraph":
        try:
            from callforge.orchestration.langgraph_workflow import LangGraphSupportWorkflow
        except ModuleNotFoundError as exc:  # the production image ships without the framework
            raise RuntimeError(
                "WORKFLOW_ENGINE=langgraph necesita el adaptador: pip install -r requirements-langgraph.txt"
            ) from exc
        return LangGraphSupportWorkflow(**parts)
    return SupportWorkflow(**parts)
