from __future__ import annotations

from callforge.application.dtos import ConversationNotFoundError
from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Feedback, utcnow
from callforge.domain.value_objects import ConversationStatus


class SubmitFeedback:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        conversation_id: str,
        rating: int,
        comment: str | None = None,
        resolved: bool | None = None,
    ) -> str:
        conversation = self._uow.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

        feedback = Feedback(
            conversation_id=conversation_id,
            rating=rating,
            comment=comment,
            resolved=resolved,
        )
        self._uow.telemetry.add_feedback(feedback)

        # Customer confirming resolution closes the loop (unless already escalated).
        if resolved is True and conversation.status == ConversationStatus.ACTIVE:
            conversation.status = ConversationStatus.RESOLVED
            conversation.updated_at = utcnow()
            self._uow.conversations.update(conversation)

        self._uow.commit()
        return feedback.id
