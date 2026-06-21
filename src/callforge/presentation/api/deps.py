from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request

from callforge.application.unit_of_work import UnitOfWork
from callforge.config import Settings
from callforge.domain.entities import DEFAULT_TENANT_ID
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore
from callforge.infrastructure.repositories import SqlTenantRepository
from callforge.orchestration.workflow import SupportWorkflow


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_workflow(request: Request) -> SupportWorkflow:
    return request.app.state.workflow


def resolve_tenant_id(
    settings: Settings, session_factory, x_api_token: str | None
) -> str | None:
    """Shared auth/tenancy resolution (REST header or webchat query param).

    - Token matches the global API_TOKEN -> default tenant (single-tenant mode)
    - Token matches a tenant api_key    -> that tenant
    - No token and no API_TOKEN set     -> default tenant (open local mode)
    - Anything else                     -> None (unauthorized)
    """
    if x_api_token:
        if settings.api_token and x_api_token == settings.api_token:
            return DEFAULT_TENANT_ID
        session = session_factory()
        try:
            tenant = SqlTenantRepository(session).get_by_api_key(x_api_token)
        finally:
            session.close()
        if tenant is not None:
            return tenant.id
        return None  # unknown credential
    if settings.api_token:
        return None  # token required but missing
    return DEFAULT_TENANT_ID


def authenticate(
    request: Request,
    x_api_token: str | None = Header(default=None),
) -> str:
    tenant_id = resolve_tenant_id(
        request.app.state.settings, request.app.state.session_factory, x_api_token
    )
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Token")
    request.state.tenant_id = tenant_id
    return tenant_id


# Kept as an alias: routers gate on authentication, which now also resolves tenancy.
require_token = authenticate


def get_uow(request: Request) -> Iterator[UnitOfWork]:
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
    session = request.app.state.session_factory()
    uow = UnitOfWork(session, tenant_id=tenant_id)
    try:
        yield uow
    except Exception:
        uow.rollback()
        raise
    finally:
        session.close()


def get_knowledge_store(
    request: Request, uow: UnitOfWork = Depends(get_uow)
) -> HybridKnowledgeStore:
    settings: Settings = request.app.state.settings
    return HybridKnowledgeStore(
        uow.knowledge,
        embedder=request.app.state.embedder,
        min_similarity=settings.knowledge_min_similarity,
    )


def get_memory_store(request: Request, uow: UnitOfWork = Depends(get_uow)):
    from callforge.infrastructure.memory import MemoryStore

    return MemoryStore(uow.memories, embedder=request.app.state.embedder)


def require_admin(
    settings: Settings = Depends(get_settings_dep),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="Admin API disabled (set ADMIN_TOKEN)")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
