"""Unit of Work: one transaction boundary per request, repos bound to it."""
from __future__ import annotations

from sqlalchemy.orm import Session

from callforge.domain.entities import DEFAULT_TENANT_ID
from callforge.infrastructure.metrics_reader import MetricsReader
from callforge.infrastructure.repositories import (
    SqlConversationRepository,
    SqlCustomerRepository,
    SqlKnowledgeRepository,
    SqlMemoryRepository,
    SqlResolutionRepository,
    SqlTelemetryRepository,
    SqlTenantRepository,
    SqlTicketRepository,
)


class UnitOfWork:
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.tenants = SqlTenantRepository(session)
        self.customers = SqlCustomerRepository(session, tenant_id)
        self.conversations = SqlConversationRepository(session, tenant_id)
        self.tickets = SqlTicketRepository(session, tenant_id)
        self.knowledge = SqlKnowledgeRepository(session, tenant_id)
        self.memories = SqlMemoryRepository(session, tenant_id)
        self.resolutions = SqlResolutionRepository(session)
        self.telemetry = SqlTelemetryRepository(session)
        self.metrics = MetricsReader(session, tenant_id)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
