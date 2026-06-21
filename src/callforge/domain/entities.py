"""Domain entities. Pure dataclasses - no ORM, no framework imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from callforge.domain.value_objects import (
    AgentName,
    Channel,
    ConversationStatus,
    EventType,
    Intent,
    MessageRole,
    ResolutionStepStatus,
    TicketPriority,
    TicketStatus,
    Urgency,
)


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_TENANT_ID = "default"


@dataclass
class Tenant:
    name: str = ""
    api_key: str = ""
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Customer:
    id: str = field(default_factory=new_id)
    external_id: str | None = None
    name: str | None = None
    email: str | None = None
    tenant_id: str = DEFAULT_TENANT_ID
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Conversation:
    customer_id: str = ""
    id: str = field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT_ID
    channel: Channel = Channel.API
    status: ConversationStatus = ConversationStatus.ACTIVE
    intent: Intent | None = None
    category: str | None = None
    urgency: Urgency | None = None
    summary: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Message:
    conversation_id: str = ""
    role: MessageRole = MessageRole.CUSTOMER
    content: str = ""
    id: str = field(default_factory=new_id)
    agent_name: AgentName | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Ticket:
    conversation_id: str = ""
    title: str = ""
    description: str = ""
    id: str = field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT_ID
    priority: TicketPriority = TicketPriority.MEDIUM
    status: TicketStatus = TicketStatus.OPEN
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Escalation:
    conversation_id: str = ""
    ticket_id: str | None = None
    reason: str = ""
    priority: TicketPriority = TicketPriority.MEDIUM
    summary_for_human: str = ""
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class ResolutionStep:
    """One diagnostic step proposed to the customer during troubleshooting."""

    conversation_id: str = ""
    step_number: int = 1
    instruction: str = ""
    expected_check: str | None = None
    status: ResolutionStepStatus = ResolutionStepStatus.PROPOSED
    customer_response: str | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class AgentRun:
    """One agent invocation. This IS the observability record."""

    conversation_id: str = ""
    agent_name: AgentName = AgentName.ROUTER
    input_text: str = ""
    output_text: str = ""
    decision: str | None = None
    confidence: float | None = None
    latency_ms: int = 0
    model_used: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0
    error: str | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class AgentEvent:
    """Workflow-level decision event (routing, retry, escalation, fallback)."""

    conversation_id: str = ""
    agent_name: AgentName | None = None
    event_type: EventType = EventType.ROUTED
    payload: str = "{}"  # JSON string
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class KnowledgeDocument:
    title: str = ""
    content: str = ""
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    id: str = field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT_ID
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class CompanionMemory:
    """A durable fact the companion remembers about the person, retrievable
    across sessions by meaning (embedding)."""

    content: str = ""
    tenant_id: str = DEFAULT_TENANT_ID
    embedding: list[float] | None = None
    source_conversation_id: str | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Feedback:
    conversation_id: str = ""
    rating: int = 0  # 1-5
    comment: str | None = None
    resolved: bool | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class LLMUsage:
    conversation_id: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0
    latency_ms: int = 0
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
