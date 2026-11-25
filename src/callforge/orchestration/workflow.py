"""SupportWorkflow: the main message-handling pipeline.

router -> knowledge retrieval -> specialist agent -> quality check
       -> (retry once on low quality) -> escalation path when needed.

The workflow is persistence-free: it receives history, runs agents, and
returns a WorkflowOutcome with everything the use case must persist
(messages, agent runs, events, ticket/escalation drafts)."""
from __future__ import annotations

import json
import logging

from callforge.agents.base import AgentContext, BaseAgent
from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.application.ports import KnowledgeSearch, WorkflowOutcome
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

__all__ = ["SupportWorkflow", "WorkflowOutcome"]


class SupportWorkflow:
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
        self._support = support
        self._troubleshooting = troubleshooting
        self._escalation = escalation
        self._summarizer = summarizer
        self._quality = quality
        self._policy = policy
        self._knowledge_top_k = knowledge_top_k

    async def handle_message(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        knowledge_store: KnowledgeSearch,
        resolution_steps: list[ResolutionStep] | None = None,
    ) -> WorkflowOutcome:
        ctx = AgentContext(
            conversation_id=conversation_id,
            user_message=user_message,
            history=history,
            resolution_steps=resolution_steps or [],
        )
        runs: list[AgentRun] = []
        events: list[AgentEvent] = []

        # 1. Route
        router_result = await self._router.run(ctx)
        runs.append(router_result.run)
        if router_result.failed:
            return controlled_fallback(ctx, runs, events, router_result)
        routing = router_result.data
        ctx.routing = routing
        next_agent = next_agent_of(routing)
        events.append(make_event(ctx, AgentName.ROUTER, EventType.ROUTED, routing))

        intent = safe_intent(routing.get("intent"))
        category = str(routing.get("category") or "general")
        urgency = safe_urgency(routing.get("urgency"))

        # 2. Knowledge retrieval
        if next_agent in ("support", "troubleshooting"):
            ctx.knowledge = knowledge_store.search(user_message, top_k=self._knowledge_top_k)
            events.append(knowledge_event(ctx))

        # 3. Escalation path decided directly by the router
        if next_agent == "escalation":
            return await self._escalate(
                ctx, runs, events, intent, category, urgency,
                quality_score=None, agent_used=AgentName.ROUTER,
            )

        specialist: BaseAgent = (
            self._troubleshooting if next_agent == "troubleshooting" else self._support
        )

        # 4. Specialist reply + quality loop
        reply_result = await specialist.run(ctx)
        runs.append(reply_result.run)
        if reply_result.failed:
            return controlled_fallback(ctx, runs, events, reply_result, intent, category, urgency)

        quality_score: float | None = None
        retries = 0
        while True:
            ctx.candidate_reply = str(reply_result.data.get("reply", ""))
            quality_result = await self._quality.run(ctx)
            runs.append(quality_result.run)
            if quality_result.failed:
                quality_score = None
                break
            quality_score = float(
                quality_result.data.get("quality_score", quality_result.confidence)
            )
            events.append(
                make_event(
                    ctx, AgentName.QUALITY, EventType.QUALITY_CHECKED,
                    {"quality_score": quality_score, "retries": retries,
                     "issues": quality_result.data.get("issues", [])},
                )
            )
            if not self._policy.should_retry(quality_score, retries):
                break
            retries += 1
            ctx.quality_feedback = json.dumps(
                quality_result.data.get("issues", []), ensure_ascii=False
            )
            events.append(make_event(ctx, specialist.name, EventType.RETRIED, {"attempt": retries}))
            reply_result = await specialist.run(ctx)
            runs.append(reply_result.run)
            if reply_result.failed:
                return controlled_fallback(ctx, runs, events, reply_result, intent, category, urgency)

        reply = final_reply(reply_result)
        suggested_escalation = bool(reply_result.data.get("suggest_escalation", False))

        # 5. Escalation policy
        if self._policy.should_escalate(
            router_next_agent=next_agent,
            agent_suggested=suggested_escalation,
            agent_confidence=reply_result.confidence,
            quality_score=quality_score,
            retries_exhausted=retries >= self._policy.max_quality_retries,
        ):
            return await self._escalate(
                ctx, runs, events, intent, category, urgency,
                quality_score=quality_score, agent_used=specialist.name,
            )

        return WorkflowOutcome(
            reply=reply,
            agent_used=specialist.name,
            intent=intent,
            category=category,
            urgency=urgency,
            confidence=reply_result.confidence,
            quality_score=quality_score,
            escalated=False,
            ticket=None,
            escalation=None,
            summary=None,
            agent_runs=runs,
            events=events,
            # Persistable diagnostic step proposed by the troubleshooting agent
            new_resolution_step=diagnostic_step(ctx, specialist.name, reply_result),
        )

    async def _escalate(
        self,
        ctx: AgentContext,
        runs: list[AgentRun],
        events: list[AgentEvent],
        intent: Intent,
        category: str,
        urgency: Urgency,
        quality_score: float | None,
        agent_used: AgentName,
    ) -> WorkflowOutcome:
        escalation_result = await self._escalation.run(ctx)
        runs.append(escalation_result.run)
        summary_result = await self._summarizer.run(ctx)
        runs.append(summary_result.run)
        return escalation_outcome(
            ctx, runs, events, escalation_result, intent, category, urgency,
            quality_score=quality_score, agent_used=agent_used, summary_data=summary_result.data,
        )
