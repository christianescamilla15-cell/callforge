"""Pieces of a support turn shared by both engines (state machine and LangGraph adapter).

Only the CONTROL FLOW differs between engines: which agent runs next, when to retry, when to escalate.
How an event, an escalation ticket, a controlled fallback or a diagnostic step is BUILT lives here once,
so a parity failure always means a different path, never a different copy of a business rule.
"""
from __future__ import annotations

import json
import logging

from callforge.agents.base import AgentContext, AgentResult
from callforge.application.ports import WorkflowOutcome
from callforge.domain.entities import AgentEvent, AgentRun, Escalation, ResolutionStep, Ticket
from callforge.domain.value_objects import AgentName, EventType, Intent, TicketPriority, Urgency
from callforge.infrastructure.voice.phrases import CONTROLLED_FALLBACK as CONTROLLED_FALLBACK_REPLY

logger = logging.getLogger("callforge.orchestration.workflow")

SPECIALISTS = ("support", "troubleshooting")
_VALID_INTENTS = {i.value for i in Intent}
_VALID_URGENCIES = {u.value for u in Urgency}
_VALID_PRIORITIES = {p.value for p in TicketPriority}


def next_agent_of(routing: dict) -> str:
    """The router's choice; anything outside the catalogue is handled as «support» (rule R6)."""
    next_agent = routing.get("next_agent", "support")
    return next_agent if next_agent in (*SPECIALISTS, "escalation") else "support"


def safe_intent(value: object) -> Intent:
    return Intent(value) if value in _VALID_INTENTS else Intent.OTHER


def safe_urgency(value: object) -> Urgency:
    return Urgency(value) if value in _VALID_URGENCIES else Urgency.MEDIUM


def make_event(ctx: AgentContext, agent_name: AgentName | None, event_type: EventType,
               payload: dict) -> AgentEvent:
    return AgentEvent(
        conversation_id=ctx.conversation_id,
        agent_name=agent_name,
        event_type=event_type,
        payload=json.dumps(payload, ensure_ascii=False),
    )


def knowledge_event(ctx: AgentContext) -> AgentEvent:
    return make_event(
        ctx, None, EventType.KNOWLEDGE_RETRIEVED,
        {
            "query": ctx.user_message[:200],
            "hits": [{"id": s.document_id, "title": s.title, "score": s.score} for s in ctx.knowledge],
        },
    )


def escalation_outcome(
    ctx: AgentContext,
    runs: list[AgentRun],
    events: list[AgentEvent],
    escalation_result: AgentResult,
    intent: Intent,
    category: str,
    urgency: Urgency,
    quality_score: float | None,
    agent_used: AgentName,
    summary_data: dict,
) -> WorkflowOutcome:
    """Ticket + Escalation from the EscalationAgent's package; the SummarizerAgent's summary rides along."""
    esc_data = escalation_result.data
    priority_raw = str(esc_data.get("priority", "medium"))
    priority = TicketPriority(priority_raw if priority_raw in _VALID_PRIORITIES else "medium")
    summary_for_human = str(esc_data.get("summary_for_human") or ctx.user_message[:500])
    reason = str(esc_data.get("reason") or "Automatic escalation")
    customer_message = str(
        esc_data.get("customer_message")
        or "Tu caso fue escalado a un agente humano. Te contactarán a la brevedad."
    )
    summary = json.dumps(summary_data, ensure_ascii=False) if summary_data else None

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
        make_event(ctx, AgentName.ESCALATION, EventType.ESCALATED,
                   {"reason": reason, "priority": priority.value, "ticket_id": ticket.id})
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


def controlled_fallback(
    ctx: AgentContext,
    runs: list[AgentRun],
    events: list[AgentEvent],
    failed_result: AgentResult,
    intent: Intent = Intent.OTHER,
    category: str = "general",
    urgency: Urgency = Urgency.MEDIUM,
) -> WorkflowOutcome:
    logger.error("All LLM providers failed for conversation %s: %s", ctx.conversation_id,
                 failed_result.run.error)
    events.append(
        make_event(ctx, failed_result.agent_name, EventType.FALLBACK_USED,
                   {"error": failed_result.run.error or "unknown"})
    )
    return WorkflowOutcome(
        reply=CONTROLLED_FALLBACK_REPLY,
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


def final_reply(reply_result: AgentResult) -> str:
    """The specialist's reply; an empty one becomes the controlled reply (rule F5)."""
    return str(reply_result.data.get("reply") or "").strip() or CONTROLLED_FALLBACK_REPLY


def diagnostic_step(ctx: AgentContext, specialist: AgentName,
                    reply_result: AgentResult) -> ResolutionStep | None:
    """The persistable step proposed by the troubleshooting agent (rules D1, D3, D4)."""
    if specialist != AgentName.TROUBLESHOOTING:
        return None
    step_data = reply_result.data.get("diagnostic_step") or {}
    instruction = str(step_data.get("instruction") or "").strip() if isinstance(step_data, dict) else ""
    if not instruction or instruction.lower() == "null":
        return None
    return ResolutionStep(
        conversation_id=ctx.conversation_id,
        step_number=len(ctx.resolution_steps) + 1,
        instruction=instruction,
        expected_check=(
            str(step_data.get("expected_check") or "") or None if isinstance(step_data, dict) else None
        ),
    )
