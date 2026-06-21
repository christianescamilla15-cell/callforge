from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from callforge.application.dtos import ConversationNotFoundError
from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.submit_feedback import SubmitFeedback
from callforge.presentation.api.deps import get_uow, require_token
from callforge.presentation.api.schemas import FeedbackRequest, FeedbackResponse

router = APIRouter(
    prefix="/feedback", tags=["feedback"], dependencies=[Depends(require_token)]
)


@router.post("", response_model=FeedbackResponse, status_code=201)
def submit_feedback(
    body: FeedbackRequest, uow: UnitOfWork = Depends(get_uow)
) -> FeedbackResponse:
    try:
        feedback_id = SubmitFeedback(uow).execute(
            conversation_id=body.conversation_id,
            rating=body.rating,
            comment=body.comment,
            resolved=body.resolved,
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return FeedbackResponse(id=feedback_id)
