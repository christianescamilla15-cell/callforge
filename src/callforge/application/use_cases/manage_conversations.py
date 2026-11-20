from __future__ import annotations

from callforge.application.dtos import ConversationClosedError, ConversationNotFoundError
from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Conversation
from callforge.domain.lifecycle import transition
from callforge.domain.value_objects import ConversationStatus


class ListConversations:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        return self._uow.conversations.list(
            status=status, limit=min(limit, 200), offset=offset
        )


class CloseConversation:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, conversation_id: str, resolved: bool = True) -> str:
        conversation = self._uow.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        if conversation.status == ConversationStatus.CLOSED:
            # Terminal: a closed conversation is neither reopened nor re-closed.
            raise ConversationClosedError(conversation_id)
        transition(
            conversation,
            ConversationStatus.RESOLVED if resolved else ConversationStatus.CLOSED,
        )
        self._uow.conversations.update(conversation)
        self._uow.commit()
        return conversation.status.value
