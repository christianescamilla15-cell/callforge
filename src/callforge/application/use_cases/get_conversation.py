from __future__ import annotations

from callforge.application.dtos import ConversationDetailOutput, ConversationNotFoundError
from callforge.application.unit_of_work import UnitOfWork


class GetConversation:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, conversation_id: str) -> ConversationDetailOutput:
        conversation = self._uow.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        messages = self._uow.conversations.list_messages(conversation_id)
        steps = self._uow.resolutions.list_for_conversation(conversation_id)
        return ConversationDetailOutput(
            id=conversation.id,
            customer_id=conversation.customer_id,
            channel=conversation.channel.value,
            status=conversation.status.value,
            intent=conversation.intent.value if conversation.intent else None,
            category=conversation.category,
            urgency=conversation.urgency.value if conversation.urgency else None,
            summary=conversation.summary,
            messages=[
                {
                    "id": m.id,
                    "role": m.role.value,
                    "content": m.content,
                    "agent_name": m.agent_name.value if m.agent_name else None,
                    "confidence": m.confidence,
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages
            ],
            resolution_steps=[
                {
                    "step_number": s.step_number,
                    "instruction": s.instruction,
                    "expected_check": s.expected_check,
                    "status": s.status.value,
                    "customer_response": s.customer_response,
                }
                for s in steps
            ],
        )
