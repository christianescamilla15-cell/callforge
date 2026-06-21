"""SQLAlchemy implementations of the domain repository ports."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from callforge.domain.entities import (
    DEFAULT_TENANT_ID,
    AgentEvent,
    AgentRun,
    CompanionMemory,
    Conversation,
    Customer,
    Escalation,
    Feedback,
    KnowledgeDocument,
    LLMUsage,
    Message,
    ResolutionStep,
    Tenant,
    Ticket,
)
from callforge.domain.repositories import (
    ConversationRepository,
    CustomerRepository,
    KnowledgeRepository,
    MemoryRepository,
    ResolutionRepository,
    TelemetryRepository,
    TenantRepository,
    TicketRepository,
)
from callforge.domain.value_objects import (
    AgentName,
    Channel,
    ConversationStatus,
    Intent,
    MessageRole,
    ResolutionStepStatus,
    TicketPriority,
    TicketStatus,
    Urgency,
)
from callforge.infrastructure import models as m


class SqlTenantRepository(TenantRepository):
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, tenant: Tenant) -> None:
        self._s.add(
            m.TenantModel(
                id=tenant.id,
                name=tenant.name,
                api_key=tenant.api_key,
                created_at=tenant.created_at,
            )
        )

    def get(self, tenant_id: str) -> Tenant | None:
        row = self._s.get(m.TenantModel, tenant_id)
        return self._to_entity(row) if row else None

    def get_by_api_key(self, api_key: str) -> Tenant | None:
        if not api_key:
            return None  # the default tenant's empty key is not a credential
        row = self._s.execute(
            select(m.TenantModel).where(m.TenantModel.api_key == api_key)
        ).scalar_one_or_none()
        return self._to_entity(row) if row else None

    @staticmethod
    def _to_entity(row: m.TenantModel) -> Tenant:
        return Tenant(
            id=row.id, name=row.name, api_key=row.api_key, created_at=row.created_at
        )


class SqlCustomerRepository(CustomerRepository):
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def add(self, customer: Customer) -> None:
        customer.tenant_id = self._tenant  # repo enforces tenancy on writes
        self._s.add(
            m.CustomerModel(
                id=customer.id,
                external_id=customer.external_id,
                name=customer.name,
                email=customer.email,
                tenant_id=self._tenant,
                created_at=customer.created_at,
            )
        )

    def get(self, customer_id: str) -> Customer | None:
        row = self._s.get(m.CustomerModel, customer_id)
        if row is None or row.tenant_id != self._tenant:
            return None
        return self._to_entity(row)

    def get_by_external_id(self, external_id: str) -> Customer | None:
        row = self._s.execute(
            select(m.CustomerModel).where(
                m.CustomerModel.external_id == external_id,
                m.CustomerModel.tenant_id == self._tenant,
            )
        ).scalar_one_or_none()
        return self._to_entity(row) if row else None

    @staticmethod
    def _to_entity(row: m.CustomerModel) -> Customer:
        return Customer(
            id=row.id,
            external_id=row.external_id,
            name=row.name,
            email=row.email,
            tenant_id=row.tenant_id,
            created_at=row.created_at,
        )


class SqlConversationRepository(ConversationRepository):
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def add(self, conversation: Conversation) -> None:
        conversation.tenant_id = self._tenant
        self._s.add(
            m.ConversationModel(
                id=conversation.id,
                customer_id=conversation.customer_id,
                tenant_id=self._tenant,
                channel=conversation.channel.value,
                status=conversation.status.value,
                intent=conversation.intent.value if conversation.intent else None,
                category=conversation.category,
                urgency=conversation.urgency.value if conversation.urgency else None,
                summary=conversation.summary,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        )

    def get(self, conversation_id: str) -> Conversation | None:
        row = self._s.get(m.ConversationModel, conversation_id)
        if row is None or row.tenant_id != self._tenant:
            return None
        return self._to_entity(row)

    def list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        stmt = (
            select(m.ConversationModel)
            .where(m.ConversationModel.tenant_id == self._tenant)
            .order_by(m.ConversationModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            stmt = stmt.where(m.ConversationModel.status == status)
        return [self._to_entity(r) for r in self._s.execute(stmt).scalars()]

    def update(self, conversation: Conversation) -> None:
        row = self._s.get(m.ConversationModel, conversation.id)
        if row is None or row.tenant_id != self._tenant:
            raise ValueError(f"Conversation {conversation.id} not found")
        row.status = conversation.status.value
        row.intent = conversation.intent.value if conversation.intent else None
        row.category = conversation.category
        row.urgency = conversation.urgency.value if conversation.urgency else None
        row.summary = conversation.summary
        row.updated_at = conversation.updated_at

    def add_message(self, message: Message) -> None:
        self._s.add(
            m.MessageModel(
                id=message.id,
                conversation_id=message.conversation_id,
                role=message.role.value,
                content=message.content,
                agent_name=message.agent_name.value if message.agent_name else None,
                confidence=message.confidence,
                created_at=message.created_at,
            )
        )

    def list_messages(self, conversation_id: str) -> list[Message]:
        rows = self._s.execute(
            select(m.MessageModel)
            .where(m.MessageModel.conversation_id == conversation_id)
            .order_by(m.MessageModel.created_at)
        ).scalars()
        return [
            Message(
                id=r.id,
                conversation_id=r.conversation_id,
                role=MessageRole(r.role),
                content=r.content,
                agent_name=AgentName(r.agent_name) if r.agent_name else None,
                confidence=r.confidence,
                created_at=r.created_at,
            )
            for r in rows
        ]

    @staticmethod
    def _to_entity(row: m.ConversationModel) -> Conversation:
        return Conversation(
            id=row.id,
            customer_id=row.customer_id,
            tenant_id=row.tenant_id,
            channel=Channel(row.channel),
            status=ConversationStatus(row.status),
            intent=Intent(row.intent) if row.intent else None,
            category=row.category,
            urgency=Urgency(row.urgency) if row.urgency else None,
            summary=row.summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlTicketRepository(TicketRepository):
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def add(self, ticket: Ticket) -> None:
        ticket.tenant_id = self._tenant
        self._s.add(
            m.TicketModel(
                id=ticket.id,
                conversation_id=ticket.conversation_id,
                tenant_id=self._tenant,
                title=ticket.title,
                description=ticket.description,
                priority=ticket.priority.value,
                status=ticket.status.value,
                created_at=ticket.created_at,
                updated_at=ticket.updated_at,
            )
        )

    def get(self, ticket_id: str) -> Ticket | None:
        row = self._s.get(m.TicketModel, ticket_id)
        if row is None or row.tenant_id != self._tenant:
            return None
        return self._to_entity(row)

    def list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Ticket]:
        stmt = (
            select(m.TicketModel)
            .where(m.TicketModel.tenant_id == self._tenant)
            .order_by(m.TicketModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            stmt = stmt.where(m.TicketModel.status == status)
        return [self._to_entity(r) for r in self._s.execute(stmt).scalars()]

    def update(self, ticket: Ticket) -> None:
        row = self._s.get(m.TicketModel, ticket.id)
        if row is None or row.tenant_id != self._tenant:
            raise ValueError(f"Ticket {ticket.id} not found")
        row.status = ticket.status.value
        row.priority = ticket.priority.value
        row.updated_at = ticket.updated_at

    def add_escalation(self, escalation: Escalation) -> None:
        self._s.add(
            m.EscalationModel(
                id=escalation.id,
                conversation_id=escalation.conversation_id,
                ticket_id=escalation.ticket_id,
                reason=escalation.reason,
                priority=escalation.priority.value,
                summary_for_human=escalation.summary_for_human,
                created_at=escalation.created_at,
            )
        )

    @staticmethod
    def _to_entity(row: m.TicketModel) -> Ticket:
        return Ticket(
            id=row.id,
            conversation_id=row.conversation_id,
            tenant_id=row.tenant_id,
            title=row.title,
            description=row.description,
            priority=TicketPriority(row.priority),
            status=TicketStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlKnowledgeRepository(KnowledgeRepository):
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def add(self, document: KnowledgeDocument) -> None:
        document.tenant_id = self._tenant
        self._s.add(
            m.KnowledgeDocumentModel(
                id=document.id,
                tenant_id=self._tenant,
                title=document.title,
                content=document.content,
                category=document.category,
                tags=json.dumps(document.tags),
                embedding=json.dumps(document.embedding) if document.embedding else None,
                created_at=document.created_at,
            )
        )

    def list_all(self) -> list[KnowledgeDocument]:
        rows = self._s.execute(
            select(m.KnowledgeDocumentModel).where(
                m.KnowledgeDocumentModel.tenant_id == self._tenant
            )
        ).scalars()
        return [
            KnowledgeDocument(
                id=r.id,
                tenant_id=r.tenant_id,
                title=r.title,
                content=r.content,
                category=r.category,
                tags=json.loads(r.tags or "[]"),
                embedding=json.loads(r.embedding) if r.embedding else None,
                created_at=r.created_at,
            )
            for r in rows
        ]

    def update_embedding(self, document_id: str, embedding: list[float]) -> None:
        row = self._s.get(m.KnowledgeDocumentModel, document_id)
        if row is not None:
            row.embedding = json.dumps(embedding)


class SqlMemoryRepository(MemoryRepository):
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def add(self, memory: CompanionMemory) -> None:
        memory.tenant_id = self._tenant
        self._s.add(
            m.CompanionMemoryModel(
                id=memory.id,
                tenant_id=self._tenant,
                content=memory.content,
                embedding=json.dumps(memory.embedding) if memory.embedding else None,
                source_conversation_id=memory.source_conversation_id,
                created_at=memory.created_at,
            )
        )

    def list_all(self) -> list[CompanionMemory]:
        rows = self._s.execute(
            select(m.CompanionMemoryModel)
            .where(m.CompanionMemoryModel.tenant_id == self._tenant)
            .order_by(m.CompanionMemoryModel.created_at)
        ).scalars()
        return [
            CompanionMemory(
                id=r.id,
                tenant_id=r.tenant_id,
                content=r.content,
                embedding=json.loads(r.embedding) if r.embedding else None,
                source_conversation_id=r.source_conversation_id,
                created_at=r.created_at,
            )
            for r in rows
        ]

    def count(self) -> int:
        from sqlalchemy import func

        return (
            self._s.execute(
                select(func.count(m.CompanionMemoryModel.id)).where(
                    m.CompanionMemoryModel.tenant_id == self._tenant
                )
            ).scalar()
            or 0
        )

    def delete(self, memory_id: str) -> bool:
        row = self._s.get(m.CompanionMemoryModel, memory_id)
        if row is None or row.tenant_id != self._tenant:
            return False
        self._s.delete(row)
        return True


class SqlResolutionRepository(ResolutionRepository):
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, step: ResolutionStep) -> None:
        self._s.add(
            m.ResolutionStepModel(
                id=step.id,
                conversation_id=step.conversation_id,
                step_number=step.step_number,
                instruction=step.instruction,
                expected_check=step.expected_check,
                status=step.status.value,
                customer_response=step.customer_response,
                created_at=step.created_at,
                updated_at=step.updated_at,
            )
        )

    def list_for_conversation(self, conversation_id: str) -> list[ResolutionStep]:
        rows = self._s.execute(
            select(m.ResolutionStepModel)
            .where(m.ResolutionStepModel.conversation_id == conversation_id)
            .order_by(m.ResolutionStepModel.step_number)
        ).scalars()
        return [
            ResolutionStep(
                id=r.id,
                conversation_id=r.conversation_id,
                step_number=r.step_number,
                instruction=r.instruction,
                expected_check=r.expected_check,
                status=ResolutionStepStatus(r.status),
                customer_response=r.customer_response,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    def update(self, step: ResolutionStep) -> None:
        row = self._s.get(m.ResolutionStepModel, step.id)
        if row is None:
            raise ValueError(f"ResolutionStep {step.id} not found")
        row.status = step.status.value
        row.customer_response = step.customer_response
        row.updated_at = step.updated_at


class SqlTelemetryRepository(TelemetryRepository):
    def __init__(self, session: Session) -> None:
        self._s = session

    def add_run(self, run: AgentRun) -> None:
        self._s.add(
            m.AgentRunModel(
                id=run.id,
                conversation_id=run.conversation_id,
                agent_name=run.agent_name.value,
                input_text=run.input_text,
                output_text=run.output_text,
                decision=run.decision,
                confidence=run.confidence,
                latency_ms=run.latency_ms,
                model_used=run.model_used,
                provider=run.provider,
                tokens_in=run.tokens_in,
                tokens_out=run.tokens_out,
                estimated_cost=run.estimated_cost,
                error=run.error,
                created_at=run.created_at,
            )
        )

    def add_event(self, event: AgentEvent) -> None:
        self._s.add(
            m.AgentEventModel(
                id=event.id,
                conversation_id=event.conversation_id,
                agent_name=event.agent_name.value if event.agent_name else None,
                event_type=event.event_type.value,
                payload=event.payload,
                created_at=event.created_at,
            )
        )

    def add_usage(self, usage: LLMUsage) -> None:
        self._s.add(
            m.LLMUsageModel(
                id=usage.id,
                conversation_id=usage.conversation_id,
                provider=usage.provider,
                model=usage.model,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                estimated_cost=usage.estimated_cost,
                latency_ms=usage.latency_ms,
                created_at=usage.created_at,
            )
        )

    def add_feedback(self, feedback: Feedback) -> None:
        self._s.add(
            m.FeedbackModel(
                id=feedback.id,
                conversation_id=feedback.conversation_id,
                rating=feedback.rating,
                comment=feedback.comment,
                resolved=feedback.resolved,
                created_at=feedback.created_at,
            )
        )
