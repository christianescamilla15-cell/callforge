from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from callforge.application.dtos import (
    ConversationClosedError,
    ConversationNotFoundError,
    SendMessageInput,
    StartConversationInput,
)
from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.get_conversation import GetConversation
from callforge.application.use_cases.manage_conversations import (
    CloseConversation,
    ListConversations,
)
from callforge.application.use_cases.send_message import SendMessage
from callforge.application.use_cases.start_conversation import StartConversation
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore
from callforge.orchestration.workflow import SupportWorkflow
from callforge.presentation.api.deps import (
    get_knowledge_store,
    get_memory_store,
    get_uow,
    get_workflow,
    require_token,
)
from callforge.presentation.api.schemas import (
    CloseConversationRequest,
    ConversationDetailResponse,
    ConversationSummary,
    SendMessageRequest,
    SendMessageResponse,
    StartConversationRequest,
    StartConversationResponse,
)

router = APIRouter(
    prefix="/conversations", tags=["conversations"], dependencies=[Depends(require_token)]
)


@router.post("/start", response_model=StartConversationResponse)
def start_conversation(
    body: StartConversationRequest, uow: UnitOfWork = Depends(get_uow)
) -> StartConversationResponse:
    result = StartConversation(uow).execute(
        StartConversationInput(
            customer_external_id=body.customer_external_id,
            customer_name=body.customer_name,
            customer_email=body.customer_email,
            channel=body.channel,
        )
    )
    return StartConversationResponse(**result.__dict__)


@router.post("/{conversation_id}/message", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    uow: UnitOfWork = Depends(get_uow),
    workflow: SupportWorkflow = Depends(get_workflow),
    knowledge_store: HybridKnowledgeStore = Depends(get_knowledge_store),
    memory_store=Depends(get_memory_store),
) -> SendMessageResponse:
    try:
        result = await SendMessage(uow, workflow, knowledge_store, memory_store).execute(
            SendMessageInput(conversation_id=conversation_id, content=body.content)
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except ConversationClosedError:
        raise HTTPException(status_code=409, detail="Conversation is closed")
    return SendMessageResponse(**result.__dict__)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    uow: UnitOfWork = Depends(get_uow),
) -> list[ConversationSummary]:
    conversations = ListConversations(uow).execute(
        status=status, limit=limit, offset=offset
    )
    return [
        ConversationSummary(
            id=c.id,
            customer_id=c.customer_id,
            channel=c.channel.value,
            status=c.status.value,
            intent=c.intent.value if c.intent else None,
            category=c.category,
            urgency=c.urgency.value if c.urgency else None,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in conversations
    ]


@router.post("/{conversation_id}/close")
def close_conversation(
    conversation_id: str,
    body: CloseConversationRequest,
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    try:
        status = CloseConversation(uow).execute(conversation_id, resolved=body.resolved)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation_id": conversation_id, "status": status}


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str, uow: UnitOfWork = Depends(get_uow)
) -> ConversationDetailResponse:
    try:
        result = GetConversation(uow).execute(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetailResponse(**result.__dict__)
