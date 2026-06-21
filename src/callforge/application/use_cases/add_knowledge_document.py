from __future__ import annotations

from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import KnowledgeDocument
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore


class AddKnowledgeDocument:
    def __init__(
        self, uow: UnitOfWork, knowledge_store: HybridKnowledgeStore | None = None
    ) -> None:
        self._uow = uow
        self._knowledge_store = knowledge_store

    def execute(
        self,
        title: str,
        content: str,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        document = KnowledgeDocument(
            title=title, content=content, category=category, tags=tags or []
        )
        if self._knowledge_store is not None:
            self._knowledge_store.add(document)  # embeds at ingest when available
        else:
            self._uow.knowledge.add(document)
        self._uow.commit()
        return document.id
