from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.list_tickets import ListTickets
from callforge.domain.entities import Ticket
from callforge.domain.value_objects import TicketStatus
from callforge.presentation.api.deps import get_uow, require_token
from callforge.presentation.api.schemas import TicketResponse, TicketStatusUpdateRequest

router = APIRouter(
    prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_token)]
)


def _to_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        conversation_id=ticket.conversation_id,
        title=ticket.title,
        description=ticket.description,
        priority=ticket.priority.value,
        status=ticket.status.value,
        created_at=ticket.created_at.isoformat(),
    )


@router.get("", response_model=list[TicketResponse])
def list_tickets(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    uow: UnitOfWork = Depends(get_uow),
) -> list[TicketResponse]:
    return [
        _to_response(t)
        for t in ListTickets(uow).execute(status=status, limit=limit, offset=offset)
    ]


@router.get("/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: str, uow: UnitOfWork = Depends(get_uow)) -> TicketResponse:
    ticket = ListTickets(uow).get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _to_response(ticket)


@router.patch("/{ticket_id}", response_model=TicketResponse)
def update_ticket(
    ticket_id: str,
    body: TicketStatusUpdateRequest,
    uow: UnitOfWork = Depends(get_uow),
) -> TicketResponse:
    try:
        status = TicketStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid status '{body.status}'")
    ticket = ListTickets(uow).update_status(ticket_id, status)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _to_response(ticket)
