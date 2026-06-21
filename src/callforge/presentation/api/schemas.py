from __future__ import annotations

from pydantic import BaseModel, Field


class StartConversationRequest(BaseModel):
    customer_external_id: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    channel: str = "api"


class StartConversationResponse(BaseModel):
    conversation_id: str
    customer_id: str
    status: str


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class SendMessageResponse(BaseModel):
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


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    agent_name: str | None
    confidence: float | None
    created_at: str


class ResolutionStepItem(BaseModel):
    step_number: int
    instruction: str
    expected_check: str | None
    status: str
    customer_response: str | None


class ConversationDetailResponse(BaseModel):
    id: str
    customer_id: str
    channel: str
    status: str
    intent: str | None
    category: str | None
    urgency: str | None
    summary: str | None
    messages: list[MessageItem]
    resolution_steps: list[ResolutionStepItem] = []


class ConversationSummary(BaseModel):
    id: str
    customer_id: str
    channel: str
    status: str
    intent: str | None
    category: str | None
    urgency: str | None
    created_at: str
    updated_at: str


class CloseConversationRequest(BaseModel):
    resolved: bool = True


class TicketStatusUpdateRequest(BaseModel):
    status: str  # open | in_progress | resolved | closed


class KnowledgeDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


class KnowledgeDocumentResponse(BaseModel):
    id: str


class TicketResponse(BaseModel):
    id: str
    conversation_id: str
    title: str
    description: str
    priority: str
    status: str
    created_at: str


class FeedbackRequest(BaseModel):
    conversation_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    resolved: bool | None = None


class FeedbackResponse(BaseModel):
    id: str


class HealthResponse(BaseModel):
    status: str
    env: str
    database: str
    llm_providers: list[str]
