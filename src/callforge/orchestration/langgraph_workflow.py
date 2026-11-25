"""LangGraph adapter of the support turn, behind the same application port as the state machine.

    router ─┬─ fallback                      (LLM down)
            ├─ escalation ─ summarizer       (customer asked for a human)
            └─ retrieve ─┬─ support ─────────┐
                         └─ troubleshooting ─┴─ quality ─┬─ (retry) → same specialist
                                                         └─ decide ─┬─ END (reply)
                                                                    └─ escalation ─ summarizer

Same agents, same `EscalationPolicy`, same outcome builders (`orchestration.steps`): only the control
flow is expressed as a `StateGraph` instead of Python `if`/`while`. The explicit `SupportWorkflow` stays
the default engine; this one is opt-in (WORKFLOW_ENGINE=langgraph) and its agreement with the state
machine is measured by tests/test_paridad_motores.py. No checkpointer, cache or LangSmith tracing is
used: the graph state lives only for the turn, in memory (ADR 0005).

Every node leaves a `NodeTrace` (node, latency, agents it called, where it went) in
`WorkflowOutcome.node_traces` and one log line `langgraph nodo=…`.
"""
from __future__ import annotations

import json
import logging
import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from callforge.agents.base import AgentContext, AgentResult, BaseAgent
from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.application.ports import KnowledgeSearch, NodeTrace, WorkflowOutcome
from callforge.domain.entities import AgentEvent, AgentRun, ResolutionStep
from callforge.domain.value_objects import AgentName, EventType, Intent, Urgency
from callforge.orchestration.policies import EscalationPolicy
from callforge.orchestration.steps import (
    controlled_fallback,
    diagnostic_step,
    escalation_outcome,
    final_reply,
    knowledge_event,
    make_event,
    next_agent_of,
    safe_intent,
    safe_urgency,
)

logger = logging.getLogger(__name__)

NODES = ("router", "retrieve", "support", "troubleshooting", "quality", "decide", "escalation",
         "summarizer", "fallback")


class TurnState(TypedDict, total=False):
    conversation_id: str
    user_message: str
    history: list[dict[str, str]]
    resolution_steps: list[ResolutionStep]
    routing: dict | None
    next_agent: str
    intent: Intent
    category: str
    urgency: Urgency
    knowledge: list
    reply: AgentResult
    candidate_reply: str | None
    quality_feedback: str | None
    quality_score: float | None
    retries: int
    escalated_by: AgentName
    escalation_result: AgentResult
    summary_data: dict
    failed: AgentResult
    route: str
    runs: Annotated[list[AgentRun], operator.add]
    events: Annotated[list[AgentEvent], operator.add]
    traces: Annotated[list[NodeTrace], operator.add]


@dataclass(frozen=True)
class TurnContext:
    """Run-scoped dependencies: they are not state, so they never enter a channel."""

    knowledge_store: KnowledgeSearch


Node = Callable[[TurnState, Runtime[TurnContext]], Awaitable[dict[str, Any]]]


def _ctx(state: TurnState) -> AgentContext:
    """The AgentContext every agent sees, rebuilt from the graph state (the state machine mutates one)."""
    return AgentContext(
        conversation_id=state["conversation_id"],
        user_message=state["user_message"],
        history=state["history"],
        knowledge=state.get("knowledge", []),
        routing=state.get("routing"),
        candidate_reply=state.get("candidate_reply"),
        quality_feedback=state.get("quality_feedback"),
        resolution_steps=state.get("resolution_steps", []),
    )


def _by_route(state: TurnState) -> str:
    return state["route"]


class LangGraphSupportWorkflow:
    """`SupportWorkflowPort` implemented as a compiled LangGraph `StateGraph`."""

    def __init__(
        self,
        router: RouterAgent,
        support: SupportAgent,
        troubleshooting: TroubleshootingAgent,
        escalation: EscalationAgent,
        summarizer: SummarizerAgent,
        quality: QualityAgent,
        policy: EscalationPolicy,
        knowledge_top_k: int = 3,
    ) -> None:
        self._router = router
        self._specialists: dict[str, BaseAgent] = {"support": support, "troubleshooting": troubleshooting}
        self._escalation = escalation
        self._summarizer = summarizer
        self._quality = quality
        self._policy = policy
        self._knowledge_top_k = knowledge_top_k
        # router, retrieve, specialist, quality, decide, escalation, summarizer + 2 per retry, with slack
        self._recursion_limit = 12 + 2 * max(policy.max_quality_retries, 0)
        self.graph = self._build()

    # ------------------------------------------------------------------ graph

    def _build(self):
        builder = StateGraph(TurnState, context_schema=TurnContext)
        for name, node in (
            ("router", self._route),
            ("retrieve", self._retrieve),
            ("support", self._specialist("support")),
            ("troubleshooting", self._specialist("troubleshooting")),
            ("quality", self._check_quality),
            ("decide", self._decide),
            ("escalation", self._escalate),
            ("summarizer", self._summarize),
            ("fallback", self._fallback),
        ):
            builder.add_node(name, self._traced(name, node))
        builder.add_edge(START, "router")
        builder.add_conditional_edges("router", _by_route, ["fallback", "escalation", "retrieve"])
        builder.add_conditional_edges("retrieve", _by_route, ["support", "troubleshooting"])
        for specialist in self._specialists:
            builder.add_conditional_edges(specialist, _by_route, ["quality", "fallback"])
        builder.add_conditional_edges("quality", _by_route, ["support", "troubleshooting", "decide"])
        builder.add_conditional_edges("decide", _by_route, ["escalation", END])
        builder.add_edge("escalation", "summarizer")
        builder.add_edge("summarizer", END)
        builder.add_edge("fallback", END)
        return builder.compile()

    def _traced(self, name: str, node: Node) -> Node:
        async def traced(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
            started = time.perf_counter()
            update = await node(state, runtime)
            trace = NodeTrace(
                node=name,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                agents=[run.agent_name.value for run in update.get("runs", [])],
                next=update.get("route", ""),
            )
            logger.info("langgraph nodo=%s ms=%.1f agentes=%s siguiente=%s conversacion=%s", trace.node,
                        trace.latency_ms, ",".join(trace.agents) or "-", trace.next or "-",
                        state["conversation_id"])
            return {**update, "traces": [trace]}

        return traced

    # ------------------------------------------------------------------ nodes

    async def _route(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        ctx = _ctx(state)
        result = await self._router.run(ctx)
        if result.failed:
            return {"runs": [result.run], "failed": result, "route": "fallback"}
        routing = result.data
        next_agent = next_agent_of(routing)
        return {
            "runs": [result.run],
            "routing": routing,
            "next_agent": next_agent,
            "events": [make_event(ctx, AgentName.ROUTER, EventType.ROUTED, routing)],
            "intent": safe_intent(routing.get("intent")),
            "category": str(routing.get("category") or "general"),
            "urgency": safe_urgency(routing.get("urgency")),
            **({"escalated_by": AgentName.ROUTER, "route": "escalation"} if next_agent == "escalation"
               else {"route": "retrieve"}),
        }

    async def _retrieve(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        ctx = _ctx(state)
        ctx.knowledge = runtime.context.knowledge_store.search(state["user_message"],
                                                               top_k=self._knowledge_top_k)
        return {"knowledge": ctx.knowledge, "events": [knowledge_event(ctx)], "route": state["next_agent"]}

    def _specialist(self, name: str) -> Node:
        agent = self._specialists[name]

        async def specialist(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
            result = await agent.run(_ctx(state))
            if result.failed:
                return {"runs": [result.run], "failed": result, "route": "fallback"}
            return {"runs": [result.run], "reply": result,
                    "candidate_reply": str(result.data.get("reply", "")), "route": "quality"}

        return specialist

    async def _check_quality(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        ctx = _ctx(state)
        result = await self._quality.run(ctx)
        if result.failed:  # rule Q4: no retry, the reply goes out without a score
            return {"runs": [result.run], "quality_score": None, "route": "decide"}
        score = float(result.data.get("quality_score", result.confidence))
        retries = state.get("retries", 0)
        issues = result.data.get("issues", [])
        events = [make_event(ctx, AgentName.QUALITY, EventType.QUALITY_CHECKED,
                             {"quality_score": score, "retries": retries, "issues": issues})]
        update: dict[str, Any] = {"runs": [result.run], "quality_score": score, "route": "decide"}
        if self._policy.should_retry(score, retries):
            retries += 1
            specialist = self._specialists[state["next_agent"]]
            events.append(make_event(ctx, specialist.name, EventType.RETRIED, {"attempt": retries}))
            update |= {"retries": retries, "route": state["next_agent"],
                       "quality_feedback": json.dumps(issues, ensure_ascii=False)}
        return {**update, "events": events}

    async def _decide(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        reply = state["reply"]
        if self._policy.should_escalate(
            router_next_agent=state["next_agent"],
            agent_suggested=bool(reply.data.get("suggest_escalation", False)),
            agent_confidence=reply.confidence,
            quality_score=state.get("quality_score"),
            retries_exhausted=state.get("retries", 0) >= self._policy.max_quality_retries,
        ):
            return {"escalated_by": self._specialists[state["next_agent"]].name, "route": "escalation"}
        return {"route": END}

    async def _escalate(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        result = await self._escalation.run(_ctx(state))
        return {"runs": [result.run], "escalation_result": result, "route": "summarizer"}

    async def _summarize(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        result = await self._summarizer.run(_ctx(state))
        return {"runs": [result.run], "summary_data": result.data, "route": END}

    async def _fallback(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        return {"route": END}

    # ------------------------------------------------------------------ port

    async def handle_message(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        knowledge_store: KnowledgeSearch,
        resolution_steps: list[ResolutionStep] | None = None,
    ) -> WorkflowOutcome:
        final: TurnState = await self.graph.ainvoke(
            {"conversation_id": conversation_id, "user_message": user_message, "history": history,
             "resolution_steps": resolution_steps or [], "retries": 0, "runs": [], "events": [],
             "traces": []},
            context=TurnContext(knowledge_store=knowledge_store),
            config={"recursion_limit": self._recursion_limit},
        )
        outcome = self._outcome(final)
        outcome.node_traces = list(final["traces"])
        return outcome

    def _outcome(self, final: TurnState) -> WorkflowOutcome:
        ctx = _ctx(final)
        runs, events = list(final["runs"]), list(final["events"])
        intent = final.get("intent", Intent.OTHER)
        category = final.get("category", "general")
        urgency = final.get("urgency", Urgency.MEDIUM)
        if "failed" in final:
            return controlled_fallback(ctx, runs, events, final["failed"], intent, category, urgency)
        if "escalation_result" in final:
            return escalation_outcome(
                ctx, runs, events, final["escalation_result"], intent, category, urgency,
                quality_score=final.get("quality_score"), agent_used=final["escalated_by"],
                summary_data=final.get("summary_data") or {},
            )
        reply = final["reply"]
        specialist = self._specialists[final["next_agent"]].name
        return WorkflowOutcome(
            reply=final_reply(reply),
            agent_used=specialist,
            intent=intent,
            category=category,
            urgency=urgency,
            confidence=reply.confidence,
            quality_score=final.get("quality_score"),
            escalated=False,
            ticket=None,
            escalation=None,
            summary=None,
            agent_runs=runs,
            events=events,
            new_resolution_step=diagnostic_step(ctx, specialist, reply),
        )
