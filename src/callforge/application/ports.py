"""Application ports: what the use cases need from the support workflow, not how it is built.

`SendMessage` and the routes depend on `SupportWorkflowPort`. Two engines implement it: the explicit
state machine (`orchestration.workflow.SupportWorkflow`, the default) and the LangGraph adapter
(`orchestration.langgraph_workflow.LangGraphSupportWorkflow`, opt-in with WORKFLOW_ENGINE=langgraph).
Both return the same `WorkflowOutcome`; tests/test_paridad_motores.py measures that they agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from callforge.domain.entities import AgentEvent, AgentRun, Escalation, ResolutionStep, Ticket
from callforge.domain.value_objects import AgentName, Intent, Urgency

if TYPE_CHECKING:
    from callforge.infrastructure.knowledge.store import KnowledgeSnippet


@dataclass
class NodeTrace:
    """One step of a turn: which node ran, how long it took, which agents it called and where it went."""

    node: str
    latency_ms: float
    agents: list[str] = field(default_factory=list)
    next: str = ""


@dataclass
class WorkflowOutcome:
    reply: str
    agent_used: AgentName
    intent: Intent
    category: str
    urgency: Urgency
    confidence: float
    quality_score: float | None
    escalated: bool
    ticket: Ticket | None
    escalation: Escalation | None
    summary: str | None
    agent_runs: list[AgentRun] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    new_resolution_step: ResolutionStep | None = None
    used_controlled_fallback: bool = False
    node_traces: list[NodeTrace] = field(default_factory=list)


class KnowledgeSearch(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[KnowledgeSnippet]:
        """The `top_k` most relevant snippets for `query`, best first (empty when nothing is relevant)."""


class SupportWorkflowPort(Protocol):
    async def handle_message(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        knowledge_store: KnowledgeSearch,
        resolution_steps: list[ResolutionStep] | None = None,
    ) -> WorkflowOutcome:
        """Run one support turn: route, retrieve, answer, check quality, escalate when the policy says so."""
