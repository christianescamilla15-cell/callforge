"""SupportWorkflow: the main message-handling pipeline.

router -> knowledge retrieval -> specialist agent -> quality check
       -> (retry once on low quality) -> escalation path when needed.

The workflow is persistence-free: it receives history, runs agents, and
returns a WorkflowOutcome with everything the use case must persist
(messages, agent runs, events, ticket/escalation drafts)."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
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
from callforge.orchestration.policies import EscalationPolicy

logger = logging.getLogger(__name__)

from callforge.infrastructure.voice.phrases import (  # noqa: E402
    CONTROLLED_FALLBACK as _CONTROLLED_FALLBACK_REPLY,
)

_VALID_INTENTS = {i.value for i in Intent}
_VALID_URGENCIES = {u.value for u in Urgency}
_VALID_PRIORITIES = {p.value for p in TicketPriority}

# A complete sentence is text ending in . ! ? … (+ optional closing quote)
# followed by whitespace — so we only emit once the sentence is surely done.
_STREAM_SENTENCE = re.compile(r'(.+?[.!?…]+["\')\]]?)\s+', re.S)
# First-clause flush for time-to-first-audio: a clause boundary (comma / colon /
# semicolon / dash) only after enough characters to be worth speaking.
_STREAM_CLAUSE = re.compile(r".{24,}?[,:;](?:\s|$)|.{24,}?\s[—–-]\s", re.S)

# Abliterated models sometimes ramble past their turn into a fabricated
# back-and-forth dialogue, separated by blank lines. A companion reply is one
# block, so cut at the first blank line to drop any hallucinated turns.
_DIALOGUE_BREAK = re.compile(r"\n\s*\n")


def _first_block(text: str) -> str:
    return _DIALOGUE_BREAK.split(text.strip(), maxsplit=1)[0].strip()


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
        companion_mode: bool = False,
    ) -> None:
        self._router = router
        self._support = support
        self._troubleshooting = troubleshooting
        self._escalation = escalation
        self._summarizer = summarizer
        self._quality = quality
        self._policy = policy
        self._knowledge_top_k = knowledge_top_k
        self._companion_mode = companion_mode

    @property
    def companion_mode(self) -> bool:
        return self._companion_mode

    def _companion_messages(
        self, user_message: str, history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        # Last ~12 turns is plenty of context and keeps prompt-eval fast
        # (long-term continuity comes from persistent memory, not raw history).
        messages = [
            {"role": "user" if h["role"] == "customer" else "assistant", "content": h["content"]}
            for h in history[-12:]
        ]
        messages.append({"role": "user", "content": user_message})
        return messages

    async def extract_memories(
        self, history: list[dict[str, str]], known: list[str] | None = None
    ) -> list[str]:
        """Distill durable facts about the person from recent conversation
        (one LLM call). When `known` is given, the model is told what it already
        remembers so it returns only what's NEW or changed. Returns [] on
        failure or when there's nothing new."""
        from callforge.agents.prompts import MEMORY_EXTRACT_SYSTEM
        from callforge.infrastructure.llm.base import LLMUnavailableError

        transcript = "\n".join(
            f"{'persona' if h['role'] == 'customer' else 'tu'}: {h['content']}"
            for h in history[-16:]
        )
        user_content = transcript
        if known:
            known_block = "\n".join(f"- {k}" for k in known)
            user_content = (
                f"YA RECUERDAS esto sobre la persona (NO lo repitas, solo agrega "
                f"lo nuevo o lo que cambió):\n{known_block}\n\n"
                f"CONVERSACIÓN RECIENTE:\n{transcript}"
            )
        try:
            result = await self._support._llm.complete(
                MEMORY_EXTRACT_SYSTEM,
                [{"role": "user", "content": user_content}],
                json_mode=False,
            )
        except LLMUnavailableError:
            return []
        text = result.text.strip()
        if not text or text.upper().startswith("NADA"):
            return []
        return [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]

    async def opening_line(
        self, memories: list[str], persona: str | None = None
    ) -> str:
        """Proactive contextual greeting that reopens the relationship using
        memory (one LLM call). Empty memories or any failure -> '' so the caller
        falls back to a generic greeting."""
        from callforge.agents.prompts import companion_system
        from callforge.infrastructure.llm.base import LLMUnavailableError

        if not memories:
            return ""
        system = companion_system(memories, persona)
        prompt = (
            "Reanuda la conversación TÚ, de forma proactiva: salúdame cálido y "
            "breve (1-2 frases) y retoma con naturalidad algo que recuerdas de mí "
            "(un proyecto, un gusto, cómo venía). NO preguntes '¿en qué te ayudo?'; "
            "háblame como un amigo que retoma el hilo. Un solo mensaje tuyo."
        )
        try:
            result = await self._support._llm.complete(
                system, [{"role": "user", "content": prompt}], json_mode=False
            )
        except LLMUnavailableError:
            return ""
        return _first_block(result.text).strip()

    async def stream_companion(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        out: dict,
        memories: list[str] | None = None,
        persona: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream the companion's reply sentence by sentence as the model
        generates it (low time-to-first-audio). Populates `out` with the full
        reply and the AgentRun once the stream finishes."""
        from callforge.agents.prompts import companion_system
        from callforge.infrastructure.llm.base import LLMUnavailableError

        system = companion_system(memories, persona)
        messages = self._companion_messages(user_message, history)
        llm = self._support._llm
        started = time.perf_counter()
        buffer, full = "", []
        cut = False
        first_emitted = False
        try:
            async for piece in llm.stream(system, messages):
                full.append(piece)
                buffer += piece
                # Stop at a blank line: everything after is a hallucinated turn.
                if _DIALOGUE_BREAK.search(buffer):
                    buffer = _DIALOGUE_BREAK.split(buffer, maxsplit=1)[0]
                    cut = True
                last = 0
                for m in _STREAM_SENTENCE.finditer(buffer):
                    sentence = m.group(1).strip()
                    last = m.end()
                    if sentence:
                        first_emitted = True
                        yield sentence
                buffer = buffer[last:]
                # Time-to-first-audio: if the model is still building a long
                # opening sentence (no terminator yet), flush the first clause
                # at a comma/colon so the voice can start sooner. Only the very
                # first unit — the rest stays sentence-level to avoid choppiness.
                if not first_emitted and not cut:
                    cm = _STREAM_CLAUSE.search(buffer)
                    if cm:
                        clause = buffer[: cm.end()].strip()
                        if clause:
                            first_emitted = True
                            yield clause
                            buffer = buffer[cm.end():]
                if cut:
                    break
            tail = buffer.strip()
            if tail:
                yield tail
        except LLMUnavailableError as exc:
            out["reply"] = _CONTROLLED_FALLBACK_REPLY
            out["run"] = AgentRun(
                conversation_id=conversation_id, agent_name=AgentName.COMPANION,
                input_text=user_message, output_text="", decision="error",
                confidence=0.0, latency_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            yield _CONTROLLED_FALLBACK_REPLY
            return

        reply = _first_block("".join(full)) or _CONTROLLED_FALLBACK_REPLY
        out["reply"] = reply
        out["run"] = AgentRun(
            conversation_id=conversation_id, agent_name=AgentName.COMPANION,
            input_text=user_message, output_text=reply, decision="companion",
            confidence=1.0, latency_ms=int((time.perf_counter() - started) * 1000),
            model_used=self._support._llm.name, provider="stream",
        )

    async def _companion_reply(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        memories: list[str] | None = None,
        persona: str | None = None,
    ) -> WorkflowOutcome:
        """Single warm conversational turn — no routing, no JSON, no pipeline.
        The right shape for free conversation and the uncensored local model."""
        from callforge.agents.prompts import companion_system
        from callforge.infrastructure.llm.base import LLMUnavailableError

        system = companion_system(memories, persona)
        messages = [
            {"role": "user" if h["role"] == "customer" else "assistant", "content": h["content"]}
            for h in history[-20:]
        ]
        messages.append({"role": "user", "content": user_message})

        started = time.perf_counter()
        llm = self._support._llm  # shared provider chain
        try:
            result = await llm.complete(system, messages, json_mode=False)
        except LLMUnavailableError as exc:
            run = AgentRun(
                conversation_id=conversation_id, agent_name=AgentName.COMPANION,
                input_text=user_message, output_text="", decision="error",
                confidence=0.0, latency_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            return WorkflowOutcome(
                reply=_CONTROLLED_FALLBACK_REPLY, agent_used=AgentName.COMPANION,
                intent=Intent.OTHER, category="companion", urgency=Urgency.LOW,
                confidence=0.0, quality_score=None, escalated=False, ticket=None,
                escalation=None, summary=None, agent_runs=[run], events=[],
                used_controlled_fallback=True,
            )

        reply = _first_block(result.text) or _CONTROLLED_FALLBACK_REPLY
        run = AgentRun(
            conversation_id=conversation_id, agent_name=AgentName.COMPANION,
            input_text=user_message, output_text=reply, decision="companion",
            confidence=1.0, latency_ms=result.latency_ms, model_used=result.model,
            provider=result.provider, tokens_in=result.tokens_in,
            tokens_out=result.tokens_out, estimated_cost=result.estimated_cost,
        )
        return WorkflowOutcome(
            reply=reply, agent_used=AgentName.COMPANION, intent=Intent.OTHER,
            category="companion", urgency=Urgency.LOW, confidence=1.0,
            quality_score=None, escalated=False, ticket=None, escalation=None,
            summary=None, agent_runs=[run], events=[],
        )

    async def handle_message(
        self,
        conversation_id: str,
        user_message: str,
        history: list[dict[str, str]],
        knowledge_store: KeywordKnowledgeStore,
        resolution_steps: list[ResolutionStep] | None = None,
        memories: list[str] | None = None,
        persona: str | None = None,
    ) -> WorkflowOutcome:
        if self._companion_mode:
            return await self._companion_reply(
                conversation_id, user_message, history, memories, persona
            )
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
