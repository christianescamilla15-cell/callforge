"""Repository ports (interfaces). Implementations live in infrastructure."""
from __future__ import annotations

from abc import ABC, abstractmethod

from callforge.domain.entities import (
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


class MemoryRepository(ABC):
    @abstractmethod
    def add(self, memory: CompanionMemory) -> None: ...

    @abstractmethod
    def list_all(self) -> list[CompanionMemory]: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...


class TenantRepository(ABC):
    @abstractmethod
    def add(self, tenant: Tenant) -> None: ...

    @abstractmethod
    def get(self, tenant_id: str) -> Tenant | None: ...

    @abstractmethod
    def get_by_api_key(self, api_key: str) -> Tenant | None: ...


class CustomerRepository(ABC):
    @abstractmethod
    def add(self, customer: Customer) -> None: ...

    @abstractmethod
    def get(self, customer_id: str) -> Customer | None: ...

    @abstractmethod
    def get_by_external_id(self, external_id: str) -> Customer | None: ...


class ConversationRepository(ABC):
    @abstractmethod
    def add(self, conversation: Conversation) -> None: ...

    @abstractmethod
    def get(self, conversation_id: str) -> Conversation | None: ...

    @abstractmethod
    def list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Conversation]: ...

    @abstractmethod
    def update(self, conversation: Conversation) -> None: ...

    @abstractmethod
    def add_message(self, message: Message) -> None: ...

    @abstractmethod
    def list_messages(self, conversation_id: str) -> list[Message]: ...


class TicketRepository(ABC):
    @abstractmethod
    def add(self, ticket: Ticket) -> None: ...

    @abstractmethod
    def get(self, ticket_id: str) -> Ticket | None: ...

    @abstractmethod
    def list(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Ticket]: ...

    @abstractmethod
    def update(self, ticket: Ticket) -> None: ...

    @abstractmethod
    def add_escalation(self, escalation: Escalation) -> None: ...


class KnowledgeRepository(ABC):
    @abstractmethod
    def add(self, document: KnowledgeDocument) -> None: ...

    @abstractmethod
    def list_all(self) -> list[KnowledgeDocument]: ...

    @abstractmethod
    def update_embedding(self, document_id: str, embedding: list[float]) -> None: ...


class ResolutionRepository(ABC):
    @abstractmethod
    def add(self, step: ResolutionStep) -> None: ...

    @abstractmethod
    def list_for_conversation(self, conversation_id: str) -> list[ResolutionStep]: ...

    @abstractmethod
    def update(self, step: ResolutionStep) -> None: ...


class TelemetryRepository(ABC):
    @abstractmethod
    def add_run(self, run: AgentRun) -> None: ...

    @abstractmethod
    def add_event(self, event: AgentEvent) -> None: ...

    @abstractmethod
    def add_usage(self, usage: LLMUsage) -> None: ...

    @abstractmethod
    def add_feedback(self, feedback: Feedback) -> None: ...
