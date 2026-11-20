from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Tenant
from callforge.presentation.api.deps import get_uow, require_admin
from callforge.presentation.api.schemas import ErrorResponse

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid X-Admin-Token"},
        403: {"model": ErrorResponse, "description": "Admin API disabled (ADMIN_TOKEN not set)"},
    },
)


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class TenantResponse(BaseModel):
    id: str
    name: str
    api_key: str


@router.post("/tenants", response_model=TenantResponse, status_code=201, summary="Create a tenant")
def create_tenant(
    body: CreateTenantRequest, uow: UnitOfWork = Depends(get_uow)
) -> TenantResponse:
    """Register a company and issue its API key (48 hex chars, shown only in this
    response). That key scopes every request to the tenant's own data."""
    tenant = Tenant(name=body.name, api_key=secrets.token_hex(24))
    uow.tenants.add(tenant)
    uow.commit()
    return TenantResponse(id=tenant.id, name=tenant.name, api_key=tenant.api_key)
