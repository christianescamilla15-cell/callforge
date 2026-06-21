from __future__ import annotations

from fastapi import APIRouter, Depends

from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.add_knowledge_document import AddKnowledgeDocument
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore
from callforge.presentation.api.deps import get_knowledge_store, get_uow, require_token
from callforge.presentation.api.schemas import (
    KnowledgeDocumentRequest,
    KnowledgeDocumentResponse,
)

router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_token)]
)


@router.post("/documents", response_model=KnowledgeDocumentResponse, status_code=201)
def add_document(
    body: KnowledgeDocumentRequest,
    uow: UnitOfWork = Depends(get_uow),
    knowledge_store: HybridKnowledgeStore = Depends(get_knowledge_store),
) -> KnowledgeDocumentResponse:
    document_id = AddKnowledgeDocument(uow, knowledge_store).execute(
        title=body.title, content=body.content, category=body.category, tags=body.tags
    )
    return KnowledgeDocumentResponse(id=document_id)
