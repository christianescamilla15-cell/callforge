"""SupportWorkflow: the main message-handling pipeline.

router -> knowledge retrieval -> specialist agent -> quality check
       -> (retry once on low quality) -> escalation path when needed.

The workflow is persistence-free: it receives history, runs agents, and
returns a WorkflowOutcome with everything the use case must persist
(messages, agent runs, events, ticket/escalation drafts)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from callforge.agents.base import AgentContext, AgentResult, BaseAgent
from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.domain.entities import (
    AgentEvent,
    AgentRun,
    Escalation,
    ResolutionStep,
    Ticket,
)
from callforge.domain.value_objects import (
    AgentName,
    EventType,
    Intent,
    TicketPriority,
    Urgency,
)
from callforge.infrastructure.knowledge.store import KeywordKnowledgeStore
from callforge.infrastructure.voice.phrases import (
    CONTROLLED_FALLBACK as _CONTROLLED_FALLBACK_REPLY,
)
from callforge.orchestration.policies import EscalationPolicy

logger = logging.getLogger(__name__)

_VALID_INTENTS = {i.value for i in Intent}
_VALID_URGENCIES = {u.value for u in Urgency}
_VALID_PRIORITIES = {p.value for p in TicketPriority}


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
        knowledge_store: KeywordKnowledgeStore,
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
            return self._controlled_fallback(ctx, runs, events, router_result)
        routing = router_result.data
        ctx.routing = routing
        next_agent = routing.get("next_agent", "support")
        if next_agent not in ("support", "troubleshooting", "escalation"):
            next_agent = "support"
        events.append(
            self._event(ctx, AgentName.ROUTER, EventType.ROUTED, routing)
        )

        intent = self._safe_intent(routing.get("intent"))
        category = str(routing.get("category") or "general")
        urgency = self._safe_urgency(routing.get("urgency"))

        # 2. Knowledge retrieval
        if next_agent in ("support", "troubleshooting"):
            ctx.knowledge = knowledge_store.search(
                user_message, top_k=self._knowledge_top_k
            )
            events.append(
                self._event(
                    ctx,
                    None,
                    EventType.KNOWLEDGE_RETRIEVED,
                    {
                        "query": user_message[:200],
                        "hits": [
                            {"id": s.document_id, "title": s.title, "score": s.score}
                            for s in ctx.knowledge
                        ],
                    },
                )
            )

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
            return self._controlled_fallback(
                ctx, runs, events, reply_result, intent, category, urgency
            )

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
                self._event(
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
            events.append(
                self._event(ctx, specialist.name, EventType.RETRIED, {"attempt": retries})
            )
            reply_result = await specialist.run(ctx)
            runs.append(reply_result.run)
            if reply_result.failed:
                return self._controlled_fallback(
                    ctx, runs, events, reply_result, intent, category, urgency
                )

        reply = str(reply_result.data.get("reply") or "").strip()
        if not reply:
            reply = _CONTROLLED_FALLBACK_REPLY
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

        # Persistable diagnostic step proposed by the troubleshooting agent
        new_step: ResolutionStep | None = None
        if specialist.name == AgentName.TROUBLESHOOTING:
            step_data = reply_result.data.get("diagnostic_step") or {}
            instruction = (
                str(step_data.get("instruction") or "").strip()
                if isinstance(step_data, dict)
                else ""
            )
            if instruction and instruction.lower() != "null":
                new_step = ResolutionStep(
                    conversation_id=ctx.conversation_id,
                    step_number=len(ctx.resolution_steps) + 1,
                    instruction=instruction,
                    expected_check=(
                        str(step_data.get("expected_check") or "") or None
                        if isinstance(step_data, dict)
                        else None
                    ),
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
            new_resolution_step=new_step,
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

        esc_data = escalation_result.data
        priority_raw = str(esc_data.get("priority", "medium"))
        priority = TicketPriority(
            priority_raw if priority_raw in _VALID_PRIORITIES else "medium"
        )
        summary_for_human = str(
            esc_data.get("summary_for_human") or ctx.user_message[:500]
        )
        reason = str(esc_data.get("reason") or "Automatic escalation")
        customer_message = str(
            esc_data.get("customer_message")
            or "Tu caso fue escalado a un agente humano. Te contactarán a la brevedad."
        )
        summary = (
            json.dumps(summary_result.data, ensure_ascii=False)
            if summary_result.data
            else None
        )

        ticket = Ticket(
            conversation_id=ctx.conversation_id,
            title=f"[{category}] {ctx.user_message[:80]}",
            description=summary_for_human,
            priority=priority,
        )
        escalation = Escalation(
            conversation_id=ctx.conversation_id,
            ticket_id=ticket.id,
            reason=reason,
            priority=priority,
            summary_for_human=summary_for_human,
        )
        events.append(
            self._event(
                ctx, AgentName.ESCALATION, EventType.ESCALATED,
                {"reason": reason, "priority": priority.value, "ticket_id": ticket.id},
            )
        )
        return WorkflowOutcome(
            reply=customer_message,
            agent_used=agent_used,
            intent=intent,
            category=category,
            urgency=urgency,
            confidence=escalation_result.confidence,
            quality_score=quality_score,
            escalated=True,
            ticket=ticket,
            escalation=escalation,
            summary=summary,
            agent_runs=runs,
            events=events,
        )

    def _controlled_fallback(
        self,
        ctx: AgentContext,
        runs: list[AgentRun],
        events: list[AgentEvent],
        failed_result: AgentResult,
        intent: Intent = Intent.OTHER,
        category: str = "general",
        urgency: Urgency = Urgency.MEDIUM,
    ) -> WorkflowOutcome:
        logger.error(
            "All LLM providers failed for conversation %s: %s",
            ctx.conversation_id,
            failed_result.run.error,
        )
        events.append(
            self._event(
                ctx, failed_result.agent_name, EventType.FALLBACK_USED,
                {"error": failed_result.run.error or "unknown"},
            )
        )
        return WorkflowOutcome(
            reply=_CONTROLLED_FALLBACK_REPLY,
            agent_used=failed_result.agent_name,
            intent=intent,
            category=category,
            urgency=urgency,
            confidence=0.0,
            quality_score=None,
            escalated=False,
            ticket=None,
            escalation=None,
            summary=None,
            agent_runs=runs,
            events=events,
            used_controlled_fallback=True,
        )

    def _event(
        self,
        ctx: AgentContext,
        agent_name: AgentName | None,
        event_type: EventType,
        payload: dict,
    ) -> AgentEvent:
        return AgentEvent(
            conversation_id=ctx.conversation_id,
            agent_name=agent_name,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False),
        )

    @staticmethod
    def _safe_intent(value: object) -> Intent:
        return Intent(value) if value in _VALID_INTENTS else Intent.OTHER

    @staticmethod
    def _safe_urgency(value: object) -> Urgency:
        return Urgency(value) if value in _VALID_URGENCIES else Urgency.MEDIUM
