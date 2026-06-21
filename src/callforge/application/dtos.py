from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StartConversationInput:
    customer_external_id: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    channel: str = "api"


@dataclass
class StartConversationOutput:
    conversation_id: str
    customer_id: str
    status: str


@dataclass
class SendMessageInput:
    conversation_id: str
    content: str
    persona: str | None = None  # active companion persona/mode (None = default)


@dataclass
class SendMessageOutput:
    conversation_id: str
    reply: str
    agent_used: str
    intent: str
    category: str
    urgency: str
    confidence: float
    quality_score: float | None
    escalated: bool
    ticket_id: str | None
    conversation_status: str


@dataclass
class ConversationDetailOutput:
    id: str
    customer_id: str
    channel: str
    status: str
    intent: str | None
    category: str | None
    urgency: str | None
    summary: str | None
    messages: list[dict] = field(default_factory=list)
    resolution_steps: list[dict] = field(default_factory=list)


class ConversationNotFoundError(Exception):
    pass


class ConversationClosedError(Exception):
    pass
