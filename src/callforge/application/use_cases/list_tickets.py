from __future__ import annotations

from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Ticket, utcnow
from callforge.domain.value_objects import TicketStatus


class ListTickets:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Ticket]:
        return self._uow.tickets.list(
            status=status, limit=min(limit, 200), offset=offset
        )

    def get(self, ticket_id: str) -> Ticket | None:
        return self._uow.tickets.get(ticket_id)

    def update_status(self, ticket_id: str, status: TicketStatus) -> Ticket | None:
        ticket = self._uow.tickets.get(ticket_id)
        if ticket is None:
            return None
        ticket.status = status
        ticket.updated_at = utcnow()
        self._uow.tickets.update(ticket)
        self._uow.commit()
        return ticket
