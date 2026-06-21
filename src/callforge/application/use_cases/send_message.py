from __future__ import annotations

from collections.abc import AsyncIterator

from callforge.application.dtos import (
    ConversationClosedError,
    ConversationNotFoundError,
    SendMessageInput,
    SendMessageOutput,
)
from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import LLMUsage, Message, utcnow
from callforge.domain.value_objects import (
    AgentName,
    ConversationStatus,
    Intent,
    MessageRole,
    ResolutionStepStatus,
    Urgency,
)
from callforge.infrastructure.knowledge.store import KeywordKnowledgeStore
from callforge.orchestration.workflow import SupportWorkflow


_MEMORY_EXTRACT_EVERY = 6   # run fact extraction every N customer messages
_MEMORY_RECALL_K = 5


class SendMessage:
    def __init__(
        self,
        uow: UnitOfWork,
        workflow: SupportWorkflow,
        knowledge_store: KeywordKnowledgeStore | None = None,
        memory_store=None,
    ) -> None:
        self._uow = uow
        self._workflow = workflow
        self._knowledge_store = knowledge_store
        self._memory_store = memory_store

    def _recall(self, query: str) -> list[str] | None:
        if self._memory_store is None or not self._workflow.companion_mode:
            return None
        try:
            return self._memory_store.recall(query, k=_MEMORY_RECALL_K)
        except Exception:  # noqa: BLE001 - memory is best-effort
            return None

    async def _maybe_remember(
        self, conversation_id: str, prior_history: list[dict], user_msg: str, reply: str
    ) -> None:
        """Every Nth customer turn, distill durable facts and store them."""
        if self._memory_store is None or not self._workflow.companion_mode:
            return
        customer_turns = sum(1 for h in prior_history if h["role"] == "customer") + 1
        if customer_turns % _MEMORY_EXTRACT_EVERY != 0:
            return
        recent = prior_history + [
            {"role": "customer", "content": user_msg},
            {"role": "assistant", "content": reply},
        ]
        try:
            known = None
            if hasattr(self._memory_store, "recent_contents"):
                known = self._memory_store.recent_contents()
            facts = await self._workflow.extract_memories(recent, known=known)
            if facts:
                self._memory_store.remember(facts, conversation_id)
                self._uow.commit()
        except Exception:  # noqa: BLE001 - never break the turn on memory
            self._uow.rollback()

    async def stream(self, data: SendMessageInput) -> AsyncIterator[str]:
        """Stream a companion reply sentence by sentence, persisting the turn
        when the stream finishes. Companion mode only (no routing/escalation)."""
        conversation = self._uow.conversations.get(data.conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(data.conversation_id)
        if conversation.status == ConversationStatus.CLOSED:
            raise ConversationClosedError(data.conversation_id)

        history = [
            {"role": m.role.value, "content": m.content}
            for m in self._uow.conversations.list_messages(conversation.id)
        ]
        self._uow.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.CUSTOMER,
                content=data.content,
            )
        )

        out: dict = {}
        memories = self._recall(data.content)
        async for sentence in self._workflow.stream_companion(
            conversation.id, data.content, history, out, memories, data.persona
        ):
            yield sentence

        reply = out.get("reply", "")
        run = out.get("run")
        self._uow.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=reply,
                agent_name=AgentName.COMPANION,
                confidence=1.0,
            )
        )
        if run is not None:
            self._uow.telemetry.add_run(run)
        conversation.intent = Intent.OTHER
        conversation.category = "companion"
        conversation.urgency = Urgency.LOW
        conversation.updated_at = utcnow()
        self._uow.conversations.update(conversation)
        self._uow.commit()

        await self._maybe_remember(conversation.id, history, data.content, reply)

    async def execute(self, data: SendMessageInput) -> SendMessageOutput:
        conversation = self._uow.conversations.get(data.conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(data.conversation_id)
        if conversation.status == ConversationStatus.CLOSED:
            raise ConversationClosedError(data.conversation_id)

        # Escalated conversations belong to a human: persist the message so
        # the agent sees it, but do NOT run the automated workflow again.
        if conversation.status == ConversationStatus.ESCALATED:
            self._uow.conversations.add_message(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.CUSTOMER,
                    content=data.content,
                )
            )
            conversation.updated_at = utcnow()
            self._uow.conversations.update(conversation)
            self._uow.commit()
            from callforge.infrastructure.voice.phrases import ESCALATED_NOTICE

            return SendMessageOutput(
                conversation_id=conversation.id,
                reply=ESCALATED_NOTICE,
                agent_used="escalation",
                intent=conversation.intent.value if conversation.intent else "other",
                category=conversation.category or "general",
                urgency=conversation.urgency.value if conversation.urgency else "medium",
                confidence=1.0,
                quality_score=None,
                escalated=True,
                ticket_id=None,
                conversation_status=conversation.status.value,
            )

        history = [
            {"role": m.role.value, "content": m.content}
            for m in self._uow.conversations.list_messages(conversation.id)
        ]

        self._uow.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.CUSTOMER,
                content=data.content,
            )
        )

        # Diagnostic state: the customer's message answers the last proposed step.
        resolution_steps = self._uow.resolutions.list_for_conversation(conversation.id)
        pending = [
            s for s in resolution_steps if s.status == ResolutionStepStatus.PROPOSED
        ]
        if pending:
            last = pending[-1]
            last.status = ResolutionStepStatus.ANSWERED
            last.customer_response = data.content
            last.updated_at = utcnow()
            self._uow.resolutions.update(last)

        knowledge_store = self._knowledge_store or KeywordKnowledgeStore(
            self._uow.knowledge
        )
        memories = self._recall(data.content)
        outcome = await self._workflow.handle_message(
            conversation_id=conversation.id,
            user_message=data.content,
            history=history,
            knowledge_store=knowledge_store,
            resolution_steps=resolution_steps,
            memories=memories,
            persona=data.persona,
        )

        # Persist assistant reply
        self._uow.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=outcome.reply,
                agent_name=outcome.agent_used,
                confidence=outcome.confidence,
            )
        )

        # Persist telemetry: every agent run + per-run LLM usage + events
        for run in outcome.agent_runs:
            self._uow.telemetry.add_run(run)
            if run.provider:
                self._uow.telemetry.add_usage(
                    LLMUsage(
                        conversation_id=conversation.id,
                        provider=run.provider,
                        model=run.model_used,
                        tokens_in=run.tokens_in,
                        tokens_out=run.tokens_out,
                        estimated_cost=run.estimated_cost,
                        latency_ms=run.latency_ms,
                    )
                )
        for event in outcome.events:
            self._uow.telemetry.add_event(event)

        if outcome.new_resolution_step is not None:
            self._uow.resolutions.add(outcome.new_resolution_step)

        # Persist escalation artifacts
        ticket_id = None
        if outcome.escalated and outcome.ticket and outcome.escalation:
            self._uow.tickets.add(outcome.ticket)
            self._uow.tickets.add_escalation(outcome.escalation)
            ticket_id = outcome.ticket.id
            conversation.status = ConversationStatus.ESCALATED

        # Update conversation classification
        conversation.intent = outcome.intent
        conversation.category = outcome.category
        conversation.urgency = outcome.urgency
        if outcome.summary:
            conversation.summary = outcome.summary
        conversation.updated_at = utcnow()
        self._uow.conversations.update(conversation)

        self._uow.commit()

        await self._maybe_remember(conversation.id, history, data.content, outcome.reply)

        return SendMessageOutput(
            conversation_id=conversation.id,
            reply=outcome.reply,
            agent_used=outcome.agent_used.value,
            intent=outcome.intent.value,
            category=outcome.category,
            urgency=outcome.urgency.value,
            confidence=outcome.confidence,
            quality_score=outcome.quality_score,
            escalated=outcome.escalated,
            ticket_id=ticket_id,
            conversation_status=conversation.status.value,
        )
